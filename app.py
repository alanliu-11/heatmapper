from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from cache import cached_fetch, invalidate
from processor import build_heatmap

app = FastAPI(title="Options Heatmap")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/heatmap/{ticker}")
def heatmap(
    ticker: str,
    metric: str = Query(
        default="openInterest",
        enum=["openInterest", "volume", "impliedVolatility"],
    ),
    max_expirations: int = Query(default=6, ge=1, le=12),
):
    try:
        chains = cached_fetch(ticker, max_expirations)
        return build_heatmap(chains, metric)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/heatmap/{ticker}/refresh")
def refresh(ticker: str):
    invalidate(ticker)
    return {"status": "invalidated", "ticker": ticker.upper()}
