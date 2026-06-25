"""Options-chain fetching using yfinance as a fallback data source.

This implementation returns the same shape the rest of the app expects:

    {"optionChain": {"result": [{
        "expirationDates": [<unix ts>, ...],
        "quote": {"regularMarketPrice": <spot>, "previousClose": <prev>},
        "options": [{"expirationDate": <unix ts>, "calls": [...], "puts": [...]}],
    }]}}

It's intentionally simple and defensive: missing fields are handled gracefully
and the function prefers yfinance (Yahoo) data which requires no account.
"""

from datetime import datetime
from time import mktime
from typing import List

import yfinance as yf


class RateLimitError(Exception):
    pass


def _date_str_to_ts(d: str) -> int:
    # yfinance expirations are 'YYYY-MM-DD'
    try:
        dt = datetime.strptime(d, "%Y-%m-%d")
    except Exception:
        raise ValueError(f"Invalid expiry date: {d}")
    return int(mktime(dt.timetuple()))


def _normalize_iv(volatility) -> float | None:
    if volatility is None:
        return None
    try:
        v = float(volatility)
    except Exception:
        return None
    if v <= 0:
        return None
    # If value looks like percent (>5), convert to decimal
    normalized = v / 100.0 if v > 5 else v
    return normalized if 0.01 <= normalized <= 5.0 else None


def fetch_all_expirations(ticker: str, max_expirations: int = 6) -> List[dict]:
    t = yf.Ticker(ticker)

    expirations = [d for d in list(t.options or []) if isinstance(d, str) and d]
    if not expirations:
        raise ValueError(f"No options found for {ticker}.")

    # spot and previous close
    hist = t.history(period="2d")
    spot = None
    prev_close = None
    if not hist.empty:
        closes = hist["Close"].tolist()
        if len(closes) >= 1:
            spot = float(closes[-1])
        if len(closes) >= 2:
            prev_close = float(closes[-2])

    all_expiry_ts = []
    valid_expirations = []
    for d in expirations:
        try:
            ts = _date_str_to_ts(d)
            all_expiry_ts.append(ts)
            valid_expirations.append(d)
        except Exception:
            # skip invalid expiry entries
            continue

    chains = []
    for exp in valid_expirations[:max_expirations]:
        try:
            oc = t.option_chain(exp)
        except Exception:
            # If yfinance fails for this expiry, skip it.
            continue

        calls_df = oc.calls
        puts_df = oc.puts

        def rows_to_list(df):
            out = []
            for _, r in df.iterrows():
                strike = r.get("strike")
                if strike is None:
                    continue
                def safe_int(val):
                    try:
                        return int(val)
                    except Exception:
                        return 0

                def safe_float(val):
                    try:
                        return float(val)
                    except Exception:
                        return None

                rec = {
                    "strike": float(strike),
                    "openInterest": safe_int(r.get("openInterest", 0)),
                    "volume": safe_int(r.get("volume", 0)),
                    "impliedVolatility": _normalize_iv(r.get("impliedVolatility") or r.get("imp_volatility")),
                    "expiration": _date_str_to_ts(exp),
                }
                out.append(rec)
            return out

        calls = rows_to_list(calls_df)
        puts = rows_to_list(puts_df)

        chains.append(
            {
                "optionChain": {
                    "result": [
                        {
                            "expirationDates": all_expiry_ts,
                            "quote": {"regularMarketPrice": spot, "previousClose": prev_close},
                            "options": [
                                {"expirationDate": _date_str_to_ts(exp), "calls": calls, "puts": puts}
                            ],
                        }
                    ]
                }
            }
        )

    if not chains:
        raise ValueError(f"No valid option chains for {ticker}.")

    return chains
