# Options Heatmap

A web app that visualizes stock options data as interactive heatmaps. Enter any ticker to see open interest, volume, implied volatility, or price probability distributions across strikes and expirations.

Built with FastAPI + Plotly.js. Data sourced from Yahoo Finance.

## Features

- **Open Interest / Volume / IV heatmaps** with calls, puts, and net (calls minus puts) views
- **Price probability heatmap** modeling terminal prices as lognormal distributions derived from ATM implied volatility
- Spot price overlay on all views
- Configurable number of expirations (1-12) and price band width
- In-memory cache with 15-minute TTL and manual refresh endpoint

## Quickstart

```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/heatmap/{ticker}` | Returns heatmap data. Query params: `metric` (`openInterest`, `volume`, `impliedVolatility`), `max_expirations` (1-12). |
| `GET` | `/probability/{ticker}` | Returns probability distribution. Query params: `max_expirations`, `near_pct` (price range as fraction of spot), `band_width` (dollar width per bin). |
| `POST` | `/heatmap/{ticker}/refresh` | Invalidates the cache for a ticker. |

## Project Structure

```
app.py           FastAPI routes and server setup
scraper.py       Yahoo Finance options chain fetcher
processor.py     Heatmap and probability distribution builders
cache.py         In-memory TTL cache
static/index.html   Frontend (Plotly.js)
```
