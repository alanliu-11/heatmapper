import pandas as pd
from datetime import datetime

# IV is averaged across contracts; OI and volume are summed
MEAN_METRICS = {"impliedVolatility"}


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

    return {
        "strikes":     strikes,
        "expirations": expirations,
        "calls":       calls_pivot.values.tolist(),
        "puts":        puts_pivot.values.tolist(),
        "metric":      metric,
    }
