import time

import requests

_session = None
_crumb = None

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}

# How many times to (re)try a request before giving up, and the base delay
# for exponential backoff between attempts (0.5s, 1s, 2s, ...).
MAX_ATTEMPTS = 4
BACKOFF_BASE = 0.5


class YahooRateLimitError(Exception):
    """Yahoo Finance is throttling us (HTTP 429 / 401 with a bad crumb).

    Raised so callers can distinguish "Yahoo is rate-limiting this IP, retry
    later" from a genuine bad request. The confusing part of the raw error is
    that the throttle surfaces as a 401 Unauthorized: the crumb endpoint hands
    back the literal text "Too Many Requests" instead of a token, and that bad
    crumb then gets rejected by the options endpoint.
    """


def _looks_like_valid_crumb(text: str) -> bool:
    # A real crumb is a short token with no whitespace, e.g. "abc123XYZ".
    # Throttle responses come back as prose like "Too Many Requests".
    return bool(text) and not any(c.isspace() for c in text) and len(text) < 64


def _ensure_session(force: bool = False):
    global _session, _crumb
    if _session is None or _crumb is None or force:
        session = requests.Session()
        session.headers.update(HEADERS)
        session.get("https://fc.yahoo.com", timeout=10)
        resp = session.get(
            "https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10
        )
        crumb = resp.text.strip()
        if resp.status_code != 200 or not _looks_like_valid_crumb(crumb):
            # Don't cache a bad crumb — leave the globals untouched so the next
            # attempt starts clean.
            raise YahooRateLimitError(
                "Yahoo refused to issue a crumb "
                f"(status {resp.status_code}, body {crumb!r:.60}); "
                "the IP is being rate-limited. Retry later."
            )
        _session, _crumb = session, crumb
    return _session, _crumb


def fetch_options_chain(ticker: str, expiry_timestamp: int = None) -> dict:
    url = f"https://query2.finance.yahoo.com/v7/finance/options/{ticker.upper()}"

    # Yahoo's crumb/cookie pair expires after a while, and the whole endpoint
    # gets throttled per-IP. Retry with exponential backoff, re-establishing the
    # session on auth failures so a long-running server self-heals. If we never
    # succeed, surface a clear rate-limit error instead of a bare 401.
    last_exc = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt > 0:
            time.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
        try:
            session, crumb = _ensure_session(force=attempt > 0)
        except YahooRateLimitError as e:
            last_exc = e
            continue

        params = {"crumb": crumb}
        if expiry_timestamp:
            params["date"] = expiry_timestamp
        resp = session.get(url, params=params, timeout=30)

        if resp.status_code in (401, 403, 429):
            # Throttled or stale crumb — force a fresh session on the next pass.
            last_exc = YahooRateLimitError(
                f"Yahoo returned {resp.status_code} for {ticker.upper()}; "
                "the IP is likely being rate-limited. Retry later."
            )
            _invalidate_session()
            continue

        resp.raise_for_status()
        return resp.json()

    raise last_exc


def _invalidate_session():
    global _session, _crumb
    _session = None
    _crumb = None


def fetch_all_expirations(ticker: str, max_expirations: int = 6) -> list[dict]:
    base = fetch_options_chain(ticker)
    result = base["optionChain"]["result"][0]
    expiry_timestamps = result["expirationDates"][:max_expirations]

    chains = [base]
    for ts in expiry_timestamps[1:]:
        chains.append(fetch_options_chain(ticker, ts))

    return chains
