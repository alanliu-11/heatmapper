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
# Black-Scholes building blocks
#
# Yahoo's chain gives us an implied vol per strike but no greeks, so we derive
# everything we need (call price, gamma, vanna) from Black-Scholes. Crucially we
# use each strike's OWN implied vol, so the volatility smile/skew is preserved
# rather than collapsed to a single ATM number.
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_d1_d2(s: float, k: float, sigma: float, t: float,
              r: float = RISK_FREE_RATE) -> tuple[float, float]:
    denom = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / denom
    return d1, d1 - denom


def _bs_call_price(s: float, k: float, sigma: float, t: float,
                   r: float = RISK_FREE_RATE) -> float:
    if t <= 0 or sigma <= 0 or s <= 0 or k <= 0:
        return max(s - k, 0.0)
    d1, d2 = _bs_d1_d2(s, k, sigma, t, r)
    return s * _norm_cdf(d1) - k * math.exp(-r * t) * _norm_cdf(d2)


def _bs_gamma(s: float, k: float, sigma: float, t: float,
              r: float = RISK_FREE_RATE) -> float:
    """d2V/dS2 — identical for calls and puts."""
    if t <= 0 or sigma <= 0 or s <= 0 or k <= 0:
        return 0.0
    d1, _ = _bs_d1_d2(s, k, sigma, t, r)
    return _norm_pdf(d1) / (s * sigma * math.sqrt(t))


def _bs_vanna(s: float, k: float, sigma: float, t: float,
              r: float = RISK_FREE_RATE) -> float:
    """d2V/dS d(sigma) per 1.00 of vol — identical for calls and puts."""
    if t <= 0 or sigma <= 0 or s <= 0 or k <= 0:
        return 0.0
    d1, d2 = _bs_d1_d2(s, k, sigma, t, r)
    return -_norm_pdf(d1) * d2 / sigma


# ---------------------------------------------------------------------------
# Price-probability heatmap (Breeden-Litzenberger)
#
# The risk-neutral density is recovered directly from the option market:
#     dC/dK   = -e^{rT} * P(S_T > K)            (1 + e^{rT} dC/dK = CDF)
#     d2C/dK2 =  e^{rT} * f(K)                  (the density itself)
# so the probability the price lands in a band [a, b] telescopes neatly to
#     P(a < S_T < b) = e^{rT} * ( dC/dK|_b - dC/dK|_a ).
# We build C(K) from Black-Scholes using each strike's OWN implied vol (the
# smile), so the recovered distribution inherits the market's real skew and fat
# tails instead of a symmetric lognormal. Each column is normalized to 100 (%).
# ---------------------------------------------------------------------------

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
    expected_prices: list[float] = []   # probability-weighted price per expiry

    h = band_width / 2.0  # finite-difference step for dC/dK at a band edge
    disc = lambda t: math.exp(RISK_FREE_RATE * t)

    for label, t, ks, ivs in per_expiry:
        if len(ks) < 2:
            continue  # need at least two strikes to define a smile

        # IV interpolated along the smile (np.interp clamps beyond the wings).
        iv_at = lambda k: float(np.interp(k, ks, ivs))

        def c_prime(k: float) -> float:
            """dC/dK via central difference, each leg priced at its smile IV."""
            up = _bs_call_price(spot, k + h, iv_at(k + h), t)
            dn = _bs_call_price(spot, k - h, iv_at(k - h), t)
            return (up - dn) / (2.0 * h)

        d = disc(t)
        col = [
            max(d * (c_prime(edges[i + 1]) - c_prime(edges[i])), 0.0)
            for i in range(len(strikes))
        ]
        total = sum(col)
        col = [100.0 * p / total for p in col] if total > 0 else [0.0] * len(strikes)

        # Weighted-average predicted price: each band's midpoint price times its
        # probability (col is in %, so divide by 100), summed over all bands.
        expected = sum(strikes[i] * col[i] for i in range(len(strikes))) / 100.0

        expirations.append(label)
        columns.append(col)
        expected_prices.append(round(expected, 2))

    probabilities = [
        [columns[j][i] for j in range(len(expirations))]
        for i in range(len(strikes))
    ]

    return {
        "strikes":         strikes,
        "expirations":     expirations,
        "probabilities":   probabilities,
        "expected_prices": expected_prices,
        "spot_price":      spot,
        "metric":          "probability",
    }


# ---------------------------------------------------------------------------
# Dealer exposure heatmaps: GEX (gamma) and VEX (vanna)
#
# For each option we estimate the dollar exposure a dealer carries, summed by
# open interest, under the standard convention that dealers are LONG calls and
# SHORT puts. We expose calls and puts separately so the frontend's existing
# "Net (Calls - Puts)" view yields the signed dealer book.
#
#   GEX (per strike) = gamma * OI * 100 * spot^2 * 0.01
#       -> dollars of delta dealers must re-hedge per 1% move in spot.
#       Positive net GEX => dealers buy dips / sell rips => volatility damped,
#       price tends to pin. Negative net GEX => they chase moves => volatility
#       amplified. The strike where cumulative net GEX flips sign is the
#       "gamma flip"; large positive strikes act as call walls (magnets /
#       resistance), large negative strikes as put walls (support).
#
#   VEX (per strike) = vanna * OI * 100 * spot * 0.01
#       -> how dealer delta shifts as implied vol moves; drives flows around
#       vol expansions/crushes (e.g. into and out of events).
#
# Conventions vary across vendors; the *structure* (where exposure concentrates
# and where net flips sign) is what's read, not the absolute dollar figure.
# ---------------------------------------------------------------------------

_CONTRACT_MULTIPLIER = 100

# Dealer exposure concentrates near the money; far-OTM strikes carry ~0 gamma and
# only add blank rows to the heatmap. Restrict the displayed strikes to this band
# around spot so the chart focuses on where the exposure actually lives.
_EXPOSURE_STRIKE_WINDOW = 0.15


def build_exposure_heatmap(chains: list[dict], kind: str = "gamma") -> dict:
    if kind not in ("gamma", "vanna"):
        raise ValueError("kind must be 'gamma' or 'vanna'")

    try:
        spot = chains[0]["optionChain"]["result"][0]["quote"]["regularMarketPrice"]
    except (KeyError, IndexError):
        spot = None
    if not spot or spot <= 0:
        raise ValueError("Spot price unavailable; cannot build exposure heatmap.")

    now = datetime.utcnow().timestamp()
    call_map: dict[tuple[float, str], float] = {}
    put_map:  dict[tuple[float, str], float] = {}
    strikes_set: set[float] = set()
    labels_set: set[str] = set()

    for chain in chains:
        result = chain["optionChain"]["result"][0]
        opt = result["options"][0]
        expiry_ts = opt.get("expirationDate")
        if expiry_ts is None:
            continue

        t = (expiry_ts - now) / _SECONDS_PER_YEAR
        if t <= 0:
            t = 1.0 / _SECONDS_PER_YEAR
        label = datetime.utcfromtimestamp(expiry_ts).strftime("%Y-%m-%d")
        labels_set.add(label)

        for side, target in (("calls", call_map), ("puts", put_map)):
            for o in opt.get(side, []):
                k = o.get("strike")
                iv = o.get("impliedVolatility")
                oi = o.get("openInterest") or 0
                if k is None or iv is None or iv <= 0 or oi <= 0:
                    continue

                if kind == "gamma":
                    greek = _bs_gamma(spot, k, iv, t)
                    val = greek * oi * _CONTRACT_MULTIPLIER * spot * spot * 0.01
                else:
                    greek = _bs_vanna(spot, k, iv, t)
                    val = greek * oi * _CONTRACT_MULTIPLIER * spot * 0.01

                target[(k, label)] = target.get((k, label), 0.0) + val
                strikes_set.add(k)

    lo, hi = spot * (1 - _EXPOSURE_STRIKE_WINDOW), spot * (1 + _EXPOSURE_STRIKE_WINDOW)
    strikes = sorted(k for k in strikes_set if lo <= k <= hi)
    expirations = sorted(labels_set)

    calls = [[call_map.get((k, lab), 0.0) for lab in expirations] for k in strikes]
    puts  = [[put_map.get((k, lab), 0.0)  for lab in expirations] for k in strikes]

    return {
        "strikes":     strikes,
        "expirations": expirations,
        "calls":       calls,
        "puts":        puts,
        "metric":      "gex" if kind == "gamma" else "vex",
        "spot_price":  spot,
    }
