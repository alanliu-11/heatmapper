# Heatmapper

Options intelligence platform that overlays real-time sentiment analysis on top of options flow data. Detects divergences between market positioning and crowd narrative.

Built with FastAPI + Plotly.js + SQLite. Powered by **Bright Data** (SERP API + Web Unlocker API) and **FinBERT** financial NLP.

## Features

- **Options heatmaps** (Open Interest, Volume, IV, Price Probability) with calls/puts/net views
- **FinBERT sentiment analysis** from News, Twitter, and Reddit via Bright Data SERP API
- **Sentiment-Options divergence detection** with real-time alerts when sentiment contradicts options positioning
- **Company data** (market cap, P/E, EPS, beta, 52-week range) via Bright Data Web Unlocker API
- **Per-ticker sentiment breakdown** with per-source scores and visual gauges
- **Watchlist** with divergence scanning and push notifications
- **Push notifications** (Web Push API) for divergence alerts, with background scanner every 15 min
- **Dark mode** with system preference detection and localStorage persistence
- **Skeleton loading states** for all data sections
- **Auth system** with username/password login, JWT sessions
- **Landing page** with feature overview for demo/hackathon presentation

## Bright Data Integration

Two Bright Data products are used:

1. **SERP API** (`serp_api1` zone) - Scrapes Google News, Twitter/X, and Reddit for financial sentiment data
2. **Web Unlocker API** (`web_unlocker1` zone) - Scrapes stockanalysis.com for structured company fundamentals (market cap, P/E, EPS, beta, etc.)

## Quickstart

```bash
cp .env.example .env
# Fill in API keys
pip install -r requirements.txt
uvicorn app:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/register` | Create account |
| `POST` | `/api/login` | Sign in, returns JWT cookie |
| `POST` | `/api/logout` | Sign out |
| `GET` | `/api/me` | Current user |
| `GET` | `/heatmap/{ticker}` | Options heatmap data |
| `GET` | `/probability/{ticker}` | Price probability distribution |
| `POST` | `/heatmap/{ticker}/refresh` | Invalidate cache |
| `GET` | `/api/news/{ticker}` | News + sentiment scores |
| `GET` | `/api/company/{ticker}` | Company fundamentals (via Web Unlocker) |
| `GET` | `/api/divergence/{ticker}` | Sentiment-options divergence analysis |
| `POST` | `/api/scan` | Scan watchlist for divergences + send push alerts |
| `GET` | `/api/sentiment-heatmap` | Multi-ticker sentiment grid |
| `GET` | `/api/watchlist` | User's watchlist |
| `POST` | `/api/watchlist` | Add ticker |
| `DELETE` | `/api/watchlist/{ticker}` | Remove ticker |
| `GET` | `/api/watchlist/summary` | Watchlist sentiment summary |
| `GET` | `/api/push/vapid-key` | VAPID public key |
| `POST` | `/api/push/subscribe` | Register push subscription |
| `DELETE` | `/api/push/subscribe` | Unsubscribe |

## Project Structure

```
app.py              FastAPI routes, divergence scanner, background scheduler
auth.py             JWT auth (PBKDF2-SHA256)
database.py         SQLite schema (users, watchlist, news, company_data, push_subscriptions)
scraper.py          Yahoo Finance options chain fetcher
processor.py        Heatmap and probability distribution builders
cache.py            In-memory TTL cache (15 min)
news_scraper.py     Bright Data SERP API + Web Unlocker API
sentiment.py        FinBERT sentiment analysis (ProsusAI/finbert)
notifications.py    Web Push notification sender
static/
  index.html        Dashboard (heatmaps, sentiment, divergence, news)
  login.html        Landing page + login
  register.html     Registration
  watchlist.html    Watchlist with divergence scanning
  settings.html     Account settings
  styles.css        Design system (light/dark themes, skeletons, components)
  sw.js             Service worker for push notifications
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BRIGHTDATA_API_TOKEN` | Yes | Bright Data API token (SERP + Web Unlocker) |
| `SECRET_KEY` | For production | JWT signing key |
| `VAPID_PRIVATE_KEY` | For push | Web Push private key |
| `VAPID_PUBLIC_KEY` | For push | Web Push public key |

## Tech Stack

- **Backend:** Python, FastAPI, SQLite
- **Frontend:** Vanilla HTML/CSS/JS, Plotly.js
- **Sentiment:** FinBERT (ProsusAI/finbert via HuggingFace Transformers)
- **Data:** Bright Data SERP API + Web Unlocker API, Yahoo Finance (yfinance)
- **Auth:** JWT (PyJWT), PBKDF2-SHA256
- **Notifications:** Web Push API (pywebpush)
