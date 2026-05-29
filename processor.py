import math
import pandas as pd
import numpy as np
from datetime import datetime

MEAN_METRICS = {"impliedVolatility"}

RISK_FREE_RATE = 0.0
_SECONDS_PER_YEAR = 365.25 * 24 * 3600


def _parse_chain(chain_json: dict) -> pd.DataFrame:
    result = chain_json["optionChain"]["result"][0]
    options = result["options"][0]

    calls = pd.DataFrame(options.get("calls", []))
    puts  = pd.DataFrame(options.get("puts",  []))

    calls["type"] = "call"
    puts["type"]  = "put"

    df = pd.concat([calls, puts], ignore_index=True)

    if "expiration" in df.columns:
        df["expiry_label"] = df["expiration"].apply(
            lambda ts: datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        )

    return df


def build_heatmap(chains: list[dict], metric: str = "openInterest") -> dict:
    frames = [_parse_chain(c) for c in chains]
    df = pd.concat(frames, ignore_index=True)

    df = df[["strike", "expiry_label", "type", metric]].copy()
    df[metric] = pd.to_numeric(df[metric], errors="coerce").fillna(0)

    agg = "mean" if metric in MEAN_METRICS else "sum"

    calls_pivot = (
        df[df["type"] == "call"]
        .groupby(["strike", "expiry_label"])[metric]
        .agg(agg)
        .unstack("expiry_label")
        .fillna(0)
    )
    puts_pivot = (
        df[df["type"] == "put"]
        .groupby(["strike", "expiry_label"])[metric]
        .agg(agg)
        .unstack("expiry_label")
        .fillna(0)
    )

    strikes     = sorted(df["strike"].unique().tolist())
    expirations = sorted(df["expiry_label"].unique().tolist())

    calls_pivot = calls_pivot.reindex(index=strikes, columns=expirations, fill_value=0)
    puts_pivot  = puts_pivot.reindex(index=strikes, columns=expirations, fill_value=0)

    try:
        spot_price = chains[0]["optionChain"]["result"][0]["quote"]["regularMarketPrice"]
    except (KeyError, IndexError):
        spot_price = None

    return {
        "strikes":     strikes,
        "expirations": expirations,
        "calls":       calls_pivot.values.tolist(),
        "puts":        puts_pivot.values.tolist(),
        "metric":      metric,
        "spot_price":  spot_price,
    }


# ---------------------------------------------------------------------------
# Price-probability heatmap
#
# For each expiration we model the terminal price S_T as lognormal:
#     ln(S_T) ~ Normal( ln(S0) + (r - 0.5*sigma^2)*T,  sigma^2 * T )
# using the ATM implied volatility for that expiry. The probability mass in the
# bin around each strike is the lognormal CDF difference across the bin edges.
# Each expiration column is normalized to sum to 100 (%).
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _lognormal_cdf(x: float, s0: float, sigma: float, t: float,
                   r: float = RISK_FREE_RATE) -> float:
    """P(S_T <= x) for a lognormal terminal price."""
    if x <= 0:
        return 0.0
    denom = sigma * math.sqrt(t)
    if denom <= 0:
        return 1.0 if x >= s0 else 0.0
    z = (math.log(x / s0) - (r - 0.5 * sigma * sigma) * t) / denom
    return _norm_cdf(z)


def build_probability_heatmap(chains: list[dict], near_pct: float = 0.25,
                              band_width: float = 5.0) -> dict:
    try:
        spot = chains[0]["optionChain"]["result"][0]["quote"]["regularMarketPrice"]
    except (KeyError, IndexError):
        spot = None
    if not spot or spot <= 0:
        raise ValueError("Spot price unavailable; cannot build probability distribution.")

    now = datetime.utcnow().timestamp()

    per_expiry = []          # (label, T, strikes, ivs)

    for chain in chains:
        result = chain["optionChain"]["result"][0]
        opt = result["options"][0]
        expiry_ts = opt.get("expirationDate")
        if expiry_ts is None:
            continue

        t = (expiry_ts - now) / _SECONDS_PER_YEAR
        if t <= 0:
            t = 1.0 / _SECONDS_PER_YEAR  # floor for same-day / expired chains

        iv_by_strike: dict[float, list[float]] = {}
        for side in ("calls", "puts"):
            for o in opt.get(side, []):
                k = o.get("strike")
                iv = o.get("impliedVolatility")
                if k is None or iv is None or iv <= 0:
                    continue
                iv_by_strike.setdefault(k, []).append(iv)
        if not iv_by_strike:
            continue

        ks = sorted(iv_by_strike)
        ivs = [sum(iv_by_strike[k]) / len(iv_by_strike[k]) for k in ks]
        label = datetime.utcfromtimestamp(expiry_ts).strftime("%Y-%m-%d")
        per_expiry.append((label, t, ks, ivs))

    # regular price-band grid spanning ±near_pct around spot; each row is a
    # band of width `band_width` and probability is integrated over the band.
    lo_price = math.floor(spot * (1 - near_pct) / band_width) * band_width
    hi_price = math.ceil(spot * (1 + near_pct) / band_width) * band_width
    lo_price = max(lo_price, band_width)
    n_bands = int(round((hi_price - lo_price) / band_width))
    if n_bands < 1:
        raise ValueError("Band width too large for the selected range.")
    edges = [lo_price + i * band_width for i in range(n_bands + 1)]
    strikes = [(edges[i] + edges[i + 1]) / 2 for i in range(n_bands)]

    per_expiry.sort(key=lambda e: e[1])
    expirations: list[str] = []
    columns: list[list[float]] = []

    for label, t, ks, ivs in per_expiry:
        atm_sigma = float(np.interp(spot, ks, ivs))
        if atm_sigma <= 0:
            continue

        col = [
            max(
                _lognormal_cdf(edges[i + 1], spot, atm_sigma, t)
                - _lognormal_cdf(edges[i], spot, atm_sigma, t),
                0.0,
            )
            for i in range(len(strikes))
        ]
        total = sum(col)
        col = [100.0 * p / total for p in col] if total > 0 else [0.0] * len(strikes)

        expirations.append(label)
        columns.append(col)

    probabilities = [
        [columns[j][i] for j in range(len(expirations))]
        for i in range(len(strikes))
    ]

    return {
        "strikes":       strikes,
        "expirations":   expirations,
        "probabilities": probabilities,
        "spot_price":    spot,
        "metric":        "probability",
    }
