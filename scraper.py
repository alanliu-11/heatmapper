import requests

_session = None
_crumb = None

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}


def _ensure_session(force: bool = False):
    global _session, _crumb
    if _session is None or _crumb is None or force:
        _session = requests.Session()
        _session.headers.update(HEADERS)
        _session.get("https://fc.yahoo.com", timeout=10)
        resp = _session.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10)
        _crumb = resp.text
    return _session, _crumb


def fetch_options_chain(ticker: str, expiry_timestamp: int = None) -> dict:
    url = f"https://query2.finance.yahoo.com/v7/finance/options/{ticker.upper()}"

    # Yahoo's crumb/cookie pair expires after a while. On an auth failure
    # (401/403), re-establish the session once and retry so a long-running
    # server self-heals without a restart.
    for attempt in range(2):
        session, crumb = _ensure_session(force=attempt > 0)
        params = {"crumb": crumb}
        if expiry_timestamp:
            params["date"] = expiry_timestamp
        resp = session.get(url, params=params, timeout=30)
        if resp.status_code in (401, 403) and attempt == 0:
            continue
        resp.raise_for_status()
        return resp.json()


def fetch_all_expirations(ticker: str, max_expirations: int = 6) -> list[dict]:
    base = fetch_options_chain(ticker)
    result = base["optionChain"]["result"][0]
    expiry_timestamps = result["expirationDates"][:max_expirations]

    chains = [base]
    for ts in expiry_timestamps[1:]:
        chains.append(fetch_options_chain(ticker, ts))

    return chains
