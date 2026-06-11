import time
from scraper import fetch_all_expirations

TTL = 15 * 60  # 15 minutes

_store: dict[tuple[str, int], tuple[float, list]] = {}


def cached_fetch(ticker: str, max_expirations: int = 6) -> list:
    ticker = ticker.upper()
    key = (ticker, max_expirations)
    entry = _store.get(key)

    if entry is not None:
        timestamp, data = entry
        if time.time() - timestamp < TTL:
            return data

    data = fetch_all_expirations(ticker, max_expirations)
    _store[key] = (time.time(), data)
    return data


def warm(ticker: str, max_expirations: int = 6) -> None:
    """Fetch and store regardless of TTL, so a fresh entry is always in place
    before the old one expires. Used by the background pre-warmer."""
    ticker = ticker.upper()
    data = fetch_all_expirations(ticker, max_expirations)
    _store[(ticker, max_expirations)] = (time.time(), data)


def invalidate(ticker: str) -> None:
    ticker = ticker.upper()
    for key in [k for k in _store if k[0] == ticker]:
        del _store[key]


def invalidate_all() -> None:
    _store.clear()
