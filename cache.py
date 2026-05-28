import time
from scraper import fetch_all_expirations

TTL = 15 * 60  # 15 minutes

_store: dict[str, tuple[float, list]] = {}


def cached_fetch(ticker: str, max_expirations: int = 6) -> list:
    ticker = ticker.upper()
    entry = _store.get(ticker)

    if entry is not None:
        timestamp, data = entry
        if time.time() - timestamp < TTL:
            return data

    data = fetch_all_expirations(ticker, max_expirations)
    _store[ticker] = (time.time(), data)
    return data


def invalidate(ticker: str) -> None:
    _store.pop(ticker.upper(), None)


def invalidate_all() -> None:
    _store.clear()
