"""Options-chain fetching from the Questrade API.

Questrade is a real, documented brokerage API (no datacenter-IP blocking, no
scraping), so this replaces the Yahoo scraping that kept getting rate-limited.
To leave the rest of the app untouched, the functions here adapt Questrade's
responses back into the same chain-JSON shape `processor.py` consumes:

    {"optionChain": {"result": [{
        "expirationDates": [<unix ts>, ...],
        "quote": {"regularMarketPrice": <spot>},
        "options": [{"expirationDate": <unix ts>, "calls": [...], "puts": [...]}],
    }]}}

Auth (OAuth2 refresh-token flow):
  - A manual refresh token bootstraps the first exchange (env QUESTRADE_REFRESH_TOKEN).
  - Each exchange returns a short-lived access token + api_server, AND a NEW
    refresh token. Questrade refresh tokens are single-use, so we persist the
    rotated one in the DB (app_secrets) — the env var is only the bootstrap.
  - A lock serialises refreshes so concurrent callers don't invalidate each
    other's tokens.
"""

import os
import threading
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

from database import get_secret, set_secret

load_dotenv()

ENV_REFRESH_TOKEN = os.getenv("QUESTRADE_REFRESH_TOKEN", "")
LOGIN_URL = "https://login.questrade.com/oauth2/token"
REFRESH_SECRET_KEY = "questrade_refresh_token"

# Batch size for the options-quotes endpoint (symbolIds per POST).
_QUOTE_CHUNK = 100

_lock = threading.Lock()
_access_token = None
_api_server = None
_access_expiry = 0.0


class RateLimitError(Exception):
    """Questrade is throttling us (HTTP 429). Surfaced as HTTP 429 by the API."""


class QuestradeAuthError(RuntimeError):
    """Questrade auth is unusable — typically an invalid/expired refresh token."""


# --- Auth -----------------------------------------------------------------

def _current_refresh_token() -> str:
    # Prefer the rotated token persisted in the DB; fall back to the env
    # bootstrap (used only on the very first exchange, or if the DB is absent).
    try:
        tok = get_secret(REFRESH_SECRET_KEY)
    except Exception:
        tok = None
    return tok or ENV_REFRESH_TOKEN


def _refresh_access_token():
    """Exchange the refresh token for an access token; persist the rotated one.

    Must be called with _lock held.
    """
    global _access_token, _api_server, _access_expiry

    refresh_token = _current_refresh_token()
    if not refresh_token:
        raise QuestradeAuthError(
            "No Questrade refresh token. Set QUESTRADE_REFRESH_TOKEN."
        )

    resp = requests.get(
        LOGIN_URL,
        params={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30,
    )
    if resp.status_code != 200:
        raise QuestradeAuthError(
            f"Questrade token refresh failed ({resp.status_code}). The refresh "
            "token is likely invalid or already used — generate a new manual "
            "token in the Questrade API centre and update QUESTRADE_REFRESH_TOKEN."
        )

    data = resp.json()
    _access_token = data["access_token"]
    _api_server = data["api_server"].rstrip("/")
    # Refresh a minute early to avoid racing the expiry.
    _access_expiry = time.time() + data.get("expires_in", 1800) - 60

    new_refresh = data.get("refresh_token")
    if new_refresh:
        try:
            set_secret(REFRESH_SECRET_KEY, new_refresh)
        except Exception:
            # If we can't persist (e.g. no DB locally), the in-memory access
            # token still works until it expires; only restarts are affected.
            pass


def _ensure_access():
    """Return a valid (access_token, api_server), refreshing if needed.

    Must be called with _lock held.
    """
    if _access_token is None or time.time() >= _access_expiry:
        _refresh_access_token()
    return _access_token, _api_server


def _request(method: str, path: str, *, params=None, json=None) -> dict:
    with _lock:
        token, server = _ensure_access()

    url = f"{server}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.request(
        method, url, params=params, json=json, headers=headers, timeout=30
    )

    if resp.status_code == 401:
        # Access token went stale early — force one refresh and retry.
        with _lock:
            _refresh_access_token()
            token, server = _access_token, _api_server
        url = f"{server}/{path.lstrip('/')}"
        resp = requests.request(
            method, url, params=params, json=json,
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )

    if resp.status_code == 429:
        raise RateLimitError("Questrade rate limit (429). Retry later.")
    resp.raise_for_status()
    return resp.json()


# --- Data -----------------------------------------------------------------

def _resolve_symbol_id(ticker: str) -> int:
    data = _request("GET", "v1/symbols/search", params={"prefix": ticker})
    symbols = data.get("symbols", [])
    for s in symbols:
        if s.get("symbol", "").upper() == ticker.upper():
            return s["symbolId"]
    if symbols:
        return symbols[0]["symbolId"]
    raise ValueError(f"Questrade: symbol '{ticker}' not found.")


def _get_spot(symbol_id: int) -> tuple[float | None, float | None]:
    """Return (current_price, prev_close). prev_close is yesterday's closing price."""
    data = _request("GET", f"v1/markets/quotes/{symbol_id}")
    quotes = data.get("quotes", [])
    if not quotes:
        return None, None
    q = quotes[0]
    spot = None
    for key in ("lastTradePrice", "lastTradePriceTrHrs", "closePrice"):
        v = q.get(key)
        if v:
            spot = float(v)
            break
    prev_close = q.get("closePrice")
    prev_close = float(prev_close) if prev_close else None
    # If spot fell back to closePrice there's no meaningful day-change to show.
    if spot == prev_close:
        prev_close = None
    return spot, prev_close


def _iso_to_ts(iso: str) -> int:
    """Questrade expiry like '2026-06-20T00:00:00.000000-04:00' -> unix seconds."""
    return int(datetime.fromisoformat(iso).timestamp())


def _normalize_iv(volatility) -> float | None:
    """Questrade reports implied volatility as a percent (e.g. 23.45), while the
    processor expects a decimal (0.2345). IVs as decimals are <~5 and as percent
    are >~5, so divide when it's clearly a percentage.

    The threshold-5 heuristic breaks for IVs between 1-5% reported as a percent
    (e.g. 4.9 stays 4.9 = 490% in decimal). The post-normalization bounds check
    catches those and any other obviously corrupt values from the API."""
    if volatility is None:
        return None
    v = float(volatility)
    if v <= 0:
        return None
    normalized = v / 100.0 if v > 5 else v
    # Reject anything outside 1%–500% annualised vol — implausible in any market.
    return normalized if 0.01 <= normalized <= 5.0 else None


def fetch_all_expirations(ticker: str, max_expirations: int = 6) -> list[dict]:
    ticker = ticker.upper()
    symbol_id = _resolve_symbol_id(ticker)
    spot, prev_close = _get_spot(symbol_id)

    chain = _request("GET", f"v1/symbols/{symbol_id}/options").get("optionChain", [])
    if not chain:
        raise ValueError(f"No options found for {ticker}.")

    # Each optionChain entry is one expiry. Build, per expiry, a map of
    # symbolId -> (strike, side) so we can join the quote data back by id.
    expiries = chain[:max_expirations]
    all_expiry_ts = [_iso_to_ts(e["expiryDate"]) for e in chain]

    per_expiry = []   # list of (expiry_ts, {symbolId: (strike, side)})
    all_ids: list[int] = []
    for entry in expiries:
        exp_ts = _iso_to_ts(entry["expiryDate"])
        id_map: dict[int, tuple[float, str]] = {}
        for root in entry.get("chainPerRoot", []):
            for s in root.get("chainPerStrikePrice", []):
                strike = s.get("strikePrice")
                if s.get("callSymbolId"):
                    id_map[s["callSymbolId"]] = (strike, "calls")
                    all_ids.append(s["callSymbolId"])
                if s.get("putSymbolId"):
                    id_map[s["putSymbolId"]] = (strike, "puts")
                    all_ids.append(s["putSymbolId"])
        per_expiry.append((exp_ts, id_map))

    # Batch-fetch quotes for every option id, keyed by symbolId.
    quotes_by_id: dict[int, dict] = {}
    for i in range(0, len(all_ids), _QUOTE_CHUNK):
        batch = all_ids[i : i + _QUOTE_CHUNK]
        data = _request("POST", "v1/markets/quotes/options", json={"optionIds": batch})
        for q in data.get("optionQuotes", []):
            quotes_by_id[q["symbolId"]] = q

    chains = []
    for exp_ts, id_map in per_expiry:
        calls, puts = [], []
        for sym_id, (strike, side) in id_map.items():
            q = quotes_by_id.get(sym_id)
            if not q:
                continue
            rec = {
                "strike": strike,
                "openInterest": q.get("openInterest") or 0,
                "volume": q.get("volume") or 0,
                "impliedVolatility": _normalize_iv(q.get("volatility")),
                "expiration": exp_ts,
            }
            (calls if side == "calls" else puts).append(rec)

        chains.append(
            {
                "optionChain": {
                    "result": [
                        {
                            "expirationDates": all_expiry_ts,
                            "quote": {"regularMarketPrice": spot, "previousClose": prev_close},
                            "options": [
                                {"expirationDate": exp_ts, "calls": calls, "puts": puts}
                            ],
                        }
                    ]
                }
            }
        )

    return chains
