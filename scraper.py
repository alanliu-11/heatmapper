"""Options-chain fetching, backed by the Tradier market-data API.

We moved off Yahoo Finance's unofficial endpoint because it hard-blocks
datacenter IPs (the host this runs on). Tradier exposes a real, documented
options API. To keep the rest of the app untouched, the functions here adapt
Tradier's responses back into the same chain-JSON shape `processor.py` already
consumes:

    {"optionChain": {"result": [{
        "expirationDates": [<unix ts>, ...],
        "quote": {"regularMarketPrice": <spot>},
        "options": [{"expirationDate": <unix ts>, "calls": [...], "puts": [...]}],
    }]}}

Each contract dict carries the fields the processor reads: strike,
openInterest, volume, impliedVolatility, and expiration (unix ts).

Setup: create a free Tradier developer account, grab the access token, and set
TRADIER_TOKEN in the environment (.env). The default base URL points at the
sandbox (delayed data, works with a free token); set TRADIER_BASE_URL to
https://api.tradier.com/v1 if you have a funded/production token.
"""

import os
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TRADIER_TOKEN", "")
BASE_URL = os.getenv("TRADIER_BASE_URL", "https://sandbox.tradier.com/v1").rstrip("/")

# Retry budget for transient throttling (HTTP 429), with exponential backoff.
MAX_ATTEMPTS = 4
BACKOFF_BASE = 0.5


class RateLimitError(Exception):
    """The upstream data provider is throttling us (HTTP 429).

    Surfaced so the API layer can return a real 429 with Retry-After instead of
    a confusing generic error.
    """


class TradierConfigError(RuntimeError):
    """Tradier isn't usable as configured (missing token / rejected auth)."""


def _as_list(value):
    # Tradier collapses single-element arrays to a bare object and uses null for
    # "none"; normalise both to a list.
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _date_to_ts(date_str: str) -> int:
    """'YYYY-MM-DD' -> unix seconds at UTC midnight.

    Kept in UTC so the processor's datetime.utcfromtimestamp(...) round-trips
    back to the same calendar date for its expiry labels.
    """
    return int(
        datetime.strptime(date_str, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


def _get(path: str, params: dict) -> dict:
    if not TOKEN:
        raise TradierConfigError(
            "TRADIER_TOKEN is not set. Create a free Tradier developer account, "
            "copy the access token, and set TRADIER_TOKEN in the environment."
        )

    url = f"{BASE_URL}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}

    last_exc = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt > 0:
            time.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
        resp = requests.get(url, params=params, headers=headers, timeout=30)

        if resp.status_code == 429:
            last_exc = RateLimitError(
                "Tradier returned 429 (rate limited). Retry later."
            )
            continue
        if resp.status_code == 401:
            raise TradierConfigError(
                "Tradier rejected the token (401). Check TRADIER_TOKEN and that "
                "TRADIER_BASE_URL matches the token (sandbox vs production)."
            )
        resp.raise_for_status()
        return resp.json()

    raise last_exc


def _get_expirations(ticker: str) -> list[str]:
    data = _get(
        "markets/options/expirations",
        {"symbol": ticker, "includeAllRoots": "true", "strikes": "false"},
    )
    expirations = (data.get("expirations") or {})
    return _as_list(expirations.get("date"))


def _get_spot(ticker: str) -> float | None:
    data = _get("markets/quotes", {"symbols": ticker})
    quotes = (data.get("quotes") or {})
    quote = next(iter(_as_list(quotes.get("quote"))), None)
    if not quote:
        return None
    # `last` can be null outside regular hours; fall back to the close.
    for key in ("last", "close", "prevclose"):
        v = quote.get(key)
        if v:
            return float(v)
    return None


def _get_chain(ticker: str, expiry_date: str) -> list[dict]:
    data = _get(
        "markets/options/chains",
        {"symbol": ticker, "expiration": expiry_date, "greeks": "true"},
    )
    options = (data.get("options") or {})
    return _as_list(options.get("option"))


def _build_chain_dict(
    expiry_date: str, all_expiry_ts: list[int], spot: float | None, options: list[dict]
) -> dict:
    exp_ts = _date_to_ts(expiry_date)
    calls, puts = [], []
    for o in options:
        greeks = o.get("greeks") or {}
        rec = {
            "strike": o.get("strike"),
            "openInterest": o.get("open_interest") or 0,
            "volume": o.get("volume") or 0,
            # mid_iv is Tradier's mid-market implied vol; smv_vol is a smoothed
            # fallback when mid isn't available.
            "impliedVolatility": greeks.get("mid_iv") or greeks.get("smv_vol"),
            "expiration": exp_ts,
        }
        if o.get("option_type") == "call":
            calls.append(rec)
        elif o.get("option_type") == "put":
            puts.append(rec)

    return {
        "optionChain": {
            "result": [
                {
                    "expirationDates": all_expiry_ts,
                    "quote": {"regularMarketPrice": spot},
                    "options": [
                        {"expirationDate": exp_ts, "calls": calls, "puts": puts}
                    ],
                }
            ]
        }
    }


def fetch_all_expirations(ticker: str, max_expirations: int = 6) -> list[dict]:
    ticker = ticker.upper()

    dates = _get_expirations(ticker)
    if not dates:
        raise ValueError(f"No option expirations found for {ticker}.")

    spot = _get_spot(ticker)
    all_expiry_ts = [_date_to_ts(d) for d in dates]

    chains = []
    for date_str in dates[:max_expirations]:
        options = _get_chain(ticker, date_str)
        chains.append(_build_chain_dict(date_str, all_expiry_ts, spot, options))

    return chains
