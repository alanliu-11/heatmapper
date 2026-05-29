# Heatmapper

A web app that visualizes stock options data as interactive heatmaps with sentiment-powered news tracking, watchlists, and push notifications.

Built with FastAPI + Plotly.js + SQLite. Data sourced from Yahoo Finance. News via Bright Data SERP API.

## Features

- **Options heatmaps** (Open Interest, Volume, IV, Price Probability) with calls/puts/net views
- **News scraping + sentiment analysis** using VADER on Google News headlines per ticker
- **Watchlist** with per-stock sentiment summaries persisted across sessions
- **Push notifications** (Web Push API) for watched stocks when negative sentiment news hits
- **Weekly email digest** summarizing watchlist sentiment
- **Auth system** with username/password login, JWT sessions

## Quickstart

```bash
cp .env.example .env
# Fill in API keys (optional for dev -- placeholders work without them)
pip install -r requirements.txt
uvicorn app:app --reload
```

Open [http://localhost:8000](http://localhost:8000). Create an account, add tickers to your watchlist, and view sentiment-tagged news.

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/register` | Create account |
| `POST` | `/api/login` | Sign in |
| `POST` | `/api/logout` | Sign out |
| `GET` | `/api/me` | Current user |
| `GET` | `/heatmap/{ticker}` | Heatmap data (openInterest, volume, impliedVolatility) |
| `GET` | `/probability/{ticker}` | Price probability distribution |
| `POST` | `/heatmap/{ticker}/refresh` | Invalidate cache for ticker |
| `GET` | `/api/news/{ticker}` | News articles with sentiment scores |
| `GET` | `/api/watchlist` | User's watchlist |
| `POST` | `/api/watchlist` | Add ticker to watchlist |
| `DELETE` | `/api/watchlist/{ticker}` | Remove ticker from watchlist |
| `GET` | `/api/watchlist/summary` | Watchlist with sentiment summaries |
| `GET` | `/api/push/vapid-key` | VAPID public key for push subscription |
| `POST` | `/api/push/subscribe` | Register push subscription |

## Project Structure

```
app.py              FastAPI routes and server
auth.py             JWT auth, password hashing
database.py         SQLite schema and connection
scraper.py          Yahoo Finance options chain fetcher
processor.py        Heatmap and probability builders
cache.py            In-memory TTL cache
news_scraper.py     Bright Data SERP API (with placeholder fallback)
sentiment.py        VADER sentiment analysis
notifications.py    Web Push notification sender
email_digest.py     Weekly email TLDR (run via cron)
static/
  index.html        Dashboard (heatmap + news sidebar)
  login.html        Login page
  register.html     Registration page
  watchlist.html    Watchlist management page
  styles.css        Design system (CSS tokens + components)
  sw.js             Service worker for push notifications
```

## Environment Variables

See `.env.example` for all available config. The app runs in dev mode with placeholder data when API keys are not set.

## Weekly Email Digest

```bash
# Run manually or via cron
python email_digest.py
```

Requires SMTP credentials in `.env` and email addresses on user accounts.
