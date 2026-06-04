"""Options-chain fetching from Yahoo Finance, routed through BrightData.

Yahoo's options endpoint hard-blocks datacenter IPs (the host this runs on), so
we tunnel the requests through BrightData's residential network using the same
account already configured for news scraping. Yahoo then sees a residential US
IP instead of the datacenter IP.

Yahoo still requires a crumb+cookie handshake, and that pair is tied to the IP
it was issued on. So we pin the whole session to ONE BrightData IP via a sticky
session id (appended to the proxy username) — the crumb fetch and every option
call go through the same exit node. Forcing a new session rotates to a fresh IP,
which is how we self-heal from a stale crumb or a throttled exit node.

Env (already present for news scraping):
    BRIGHTDATA_USER  e.g. brd-customer-<id>-zone-<zone>   (encodes the zone)
    BRIGHTDATA_PASS
Optional:
    BRIGHTDATA_PROXY_HOST   default brd.superproxy.io:22225
    BRIGHTDATA_COUNTRY      default us   (residential exit country)

If BrightData creds are absent we fall back to a direct connection (which will
likely be blocked on a datacenter host, but works locally).
"""

import os
import random
import time

import requests
from dotenv import load_dotenv

load_dotenv()

BRIGHTDATA_USER = os.getenv("BRIGHTDATA_USER", "")
BRIGHTDATA_PASS = os.getenv("BRIGHTDATA_PASS", "")
BRIGHTDATA_PROXY_HOST = os.getenv("BRIGHTDATA_PROXY_HOST", "brd.superproxy.io:22225")
BRIGHTDATA_COUNTRY = os.getenv("BRIGHTDATA_COUNTRY", "us")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}

MAX_ATTEMPTS = 3
BACKOFF_BASE = 0.5

_session = None
_crumb = None


class RateLimitError(Exception):
    """Yahoo is throttling/blocking us even through the proxy (401/403/429, or a
    crumb endpoint that returns "Too Many Requests" instead of a token).

    Surfaced so the API layer can return a real 429 with Retry-After instead of
    a confusing generic error.
    """


def _proxies() -> dict | None:
    """Build a proxy dict pinned to one sticky BrightData exit IP.

    The sticky session id keeps the crumb+cookie and the option calls on the
    same residential IP; a new id (one per session) means a new exit node.
    """
    if not BRIGHTDATA_USER or not BRIGHTDATA_PASS:
        return None
    username = BRIGHTDATA_USER
    if BRIGHTDATA_COUNTRY:
        username += f"-country-{BRIGHTDATA_COUNTRY}"
    username += f"-session-{random.randint(0, 1_000_000_000)}"
    proxy_url = f"http://{username}:{BRIGHTDATA_PASS}@{BRIGHTDATA_PROXY_HOST}"
    return {"http": proxy_url, "https": proxy_url}


def _looks_like_valid_crumb(text: str) -> bool:
    # A real crumb is a short token with no whitespace, e.g. "abc123XYZ".
    # Throttle responses come back as prose like "Too Many Requests".
    return bool(text) and not any(c.isspace() for c in text) and len(text) < 64


def _ensure_session(force: bool = False):
    global _session, _crumb
    if _session is None or _crumb is None or force:
        session = requests.Session()
        session.headers.update(HEADERS)
        proxies = _proxies()
        if proxies:
            session.proxies.update(proxies)
        # Prime cookies, then fetch the crumb — both through the same sticky IP.
        session.get("https://fc.yahoo.com", timeout=20)
        resp = session.get(
            "https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=20
        )
        crumb = resp.text.strip()
        if resp.status_code != 200 or not _looks_like_valid_crumb(crumb):
            raise RateLimitError(
                "Yahoo refused to issue a crumb "
                f"(status {resp.status_code}, body {crumb!r:.60}); "
                "the exit IP is being rate-limited. Retry later."
            )
        _session, _crumb = session, crumb
    return _session, _crumb


def _invalidate_session():
    global _session, _crumb
    _session = None
    _crumb = None


def fetch_options_chain(ticker: str, expiry_timestamp: int = None) -> dict:
    url = f"https://query2.finance.yahoo.com/v7/finance/options/{ticker.upper()}"

    # Retry with exponential backoff. On an auth/throttle failure, rotate to a
    # fresh sticky session (new exit IP + new crumb) and try again so a stale
    # crumb or a flagged exit node self-heals.
    last_exc = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt > 0:
            time.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
        try:
            session, crumb = _ensure_session(force=attempt > 0)
        except RateLimitError as e:
            last_exc = e
            continue

        params = {"crumb": crumb}
        if expiry_timestamp:
            params["date"] = expiry_timestamp
        resp = session.get(url, params=params, timeout=30)

        if resp.status_code in (401, 403, 429):
            last_exc = RateLimitError(
                f"Yahoo returned {resp.status_code} for {ticker.upper()} "
                "through the proxy; the exit IP is likely rate-limited. "
                "Retry later."
            )
            _invalidate_session()
            continue

        resp.raise_for_status()
        return resp.json()

    raise last_exc


def fetch_all_expirations(ticker: str, max_expirations: int = 6) -> list[dict]:
    base = fetch_options_chain(ticker)
    result = base["optionChain"]["result"][0]
    expiry_timestamps = result["expirationDates"][:max_expirations]

    chains = [base]
    for ts in expiry_timestamps[1:]:
        chains.append(fetch_options_chain(ticker, ts))

    return chains
