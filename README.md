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
- **Design system** with CSS tokens, card-based dashboard, responsive layout

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
| `POST` | `/api/register` | Create account (username, password) |
| `POST` | `/api/login` | Sign in, returns JWT cookie |
| `POST` | `/api/logout` | Sign out, clears cookie |
| `GET` | `/api/me` | Current authenticated user |
| `GET` | `/heatmap/{ticker}` | Heatmap data (openInterest, volume, impliedVolatility) |
| `GET` | `/probability/{ticker}` | Price probability distribution |
| `POST` | `/heatmap/{ticker}/refresh` | Invalidate cache for ticker |
| `GET` | `/api/news/{ticker}` | News articles with sentiment scores (cached 15 min) |
| `GET` | `/api/watchlist` | User's watchlist |
| `POST` | `/api/watchlist` | Add ticker to watchlist |
| `DELETE` | `/api/watchlist/{ticker}` | Remove ticker from watchlist |
| `GET` | `/api/watchlist/summary` | Watchlist with avg sentiment per ticker |
| `GET` | `/api/push/vapid-key` | VAPID public key for push subscription |
| `POST` | `/api/push/subscribe` | Register push subscription |
| `DELETE` | `/api/push/subscribe` | Unsubscribe from push notifications |

## Project Structure

```
app.py              FastAPI routes and server
auth.py             JWT auth (PBKDF2-SHA256), token create/verify
database.py         SQLite schema (users, watchlist, news, push_subscriptions)
scraper.py          Yahoo Finance options chain fetcher
processor.py        Heatmap and probability distribution builders
cache.py            In-memory TTL cache (15 min)
news_scraper.py     Bright Data SERP API (placeholder fallback when no API key)
sentiment.py        VADER sentiment analysis on headlines
notifications.py    Web Push notification sender (pywebpush)
email_digest.py     Weekly email TLDR (run via cron)
static/
  index.html        Dashboard (heatmap + news sidebar + controls)
  login.html        Login page
  register.html     Registration page
  watchlist.html    Watchlist management (3-column card grid)
  styles.css        Design system (CSS custom properties, components)
  sw.js             Service worker for push notifications
```

## Environment Variables

See `.env.example` for all available config:

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | For production | JWT signing key (falls back to dev default) |
| `BRIGHTDATA_API_TOKEN` | No | Enables real news scraping (placeholder data without it) |
| `VAPID_PRIVATE_KEY` | No | Enables Web Push notifications |
| `VAPID_PUBLIC_KEY` | No | Public key for push subscription |
| `SMTP_HOST` | No | Enables weekly email digest |
| `SMTP_PORT` | No | SMTP port (default 587) |
| `SMTP_USER` | No | SMTP username |
| `SMTP_PASS` | No | SMTP password |
| `FROM_EMAIL` | No | Sender email for digest |

## Weekly Email Digest

```bash
# Run manually
python email_digest.py

# Or schedule via cron (every Sunday at 9am)
# 0 9 * * 0 cd /path/to/heatmapper && python email_digest.py
```

Requires SMTP credentials in `.env` and an email address on the user account.

## Tech Stack

- **Backend:** Python, FastAPI, SQLite
- **Frontend:** Vanilla HTML/CSS/JS, Plotly.js
- **Auth:** JWT (PyJWT), PBKDF2-SHA256 password hashing
- **Sentiment:** VADER (vaderSentiment)
- **News:** Bright Data SERP API
- **Notifications:** Web Push API (pywebpush)
- **Email:** SMTP (stdlib smtplib)
