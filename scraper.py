import requests

_session = None
_crumb = None

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}


def _ensure_session():
    global _session, _crumb
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
        _session.get("https://fc.yahoo.com", timeout=10)
        resp = _session.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10)
        _crumb = resp.text
    return _session, _crumb


def fetch_options_chain(ticker: str, expiry_timestamp: int = None) -> dict:
    session, crumb = _ensure_session()
    url = f"https://query2.finance.yahoo.com/v7/finance/options/{ticker.upper()}"
    params = {"crumb": crumb}
    if expiry_timestamp:
        params["date"] = expiry_timestamp
    resp = session.get(url, params=params, timeout=30)
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
