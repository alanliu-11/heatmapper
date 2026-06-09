from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from cache import cached_fetch, invalidate
from scraper import RateLimitError
from processor import build_heatmap, build_probability_heatmap, build_exposure_heatmap, _parse_chain
from database import init_db, get_db, seconds_since
from auth import create_user, authenticate, create_token, verify_token
from news_scraper import scrape_news, scrape_twitter, scrape_reddit, scrape_all, scrape_company_info
from sentiment import analyze_articles
from notifications import VAPID_PUBLIC_KEY, send_push
from typing import Literal
import psycopg
import json
import asyncio
import time
import logging

logger = logging.getLogger("heatmapper")

# Throttle between tickers in a scan so we trickle requests to the data provider
# instead of bursting them all at once, which is what trips rate limits.
SCAN_TICKER_DELAY = 2.0

app = FastAPI(title="Options Heatmap")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
def startup():
    init_db()


@app.exception_handler(RateLimitError)
def rate_limit_handler(request: Request, exc: RateLimitError):
    # Surface upstream throttling as a real 429 (with Retry-After) so clients
    # can back off intelligently instead of seeing a generic error.
    return JSONResponse(
        status_code=429,
        content={"detail": str(exc)},
        headers={"Retry-After": "60"},
    )


# --- Auth helpers ---

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=6, max_length=128)

class LoginRequest(BaseModel):
    username: str
    password: str

class WatchlistRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)


def get_current_user(request: Request) -> dict | None:
    token = request.cookies.get("token")
    if not token:
        return None
    return verify_token(token)


# --- Pages ---

def _clear_stale_cookie(response: FileResponse, request: Request) -> FileResponse:
    if request.cookies.get("token"):
        response.delete_cookie("token", path="/")
    return response

@app.get("/")
def index(request: Request):
    user = get_current_user(request)
    if not user:
        return _clear_stale_cookie(FileResponse("static/landing.html"), request)
    return FileResponse("static/index.html")

@app.get("/register")
def register_page():
    return FileResponse("static/register.html")

@app.get("/login")
def login_page():
    return FileResponse("static/login.html")

@app.get("/sw.js")
def service_worker():
    return FileResponse("static/sw.js", media_type="application/javascript")

@app.get("/news")
def news_page(request: Request):
    user = get_current_user(request)
    if not user:
        return _clear_stale_cookie(FileResponse("static/landing.html"), request)
    return FileResponse("static/news.html")

@app.get("/settings")
def settings_page(request: Request):
    user = get_current_user(request)
    if not user:
        return _clear_stale_cookie(FileResponse("static/landing.html"), request)
    return FileResponse("static/settings.html")

@app.get("/watchlist")
def watchlist_page(request: Request):
    user = get_current_user(request)
    if not user:
        return _clear_stale_cookie(FileResponse("static/landing.html"), request)
    return FileResponse("static/watchlist.html")


# --- Auth API ---

@app.post("/api/register")
def register(body: RegisterRequest):
    try:
        user = create_user(body.username, body.password)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="Username already taken")
    token = create_token(user["id"], user["username"])
    response = JSONResponse({"ok": True, "username": user["username"]})
    response.set_cookie(
        "token", token, httponly=True, samesite="lax", max_age=7 * 24 * 3600, path="/"
    )
    return response

@app.post("/api/login")
def login(body: LoginRequest):
    user = authenticate(body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_token(user["id"], user["username"])
    response = JSONResponse({"ok": True, "username": user["username"]})
    response.set_cookie(
        "token", token, httponly=True, samesite="lax", max_age=7 * 24 * 3600, path="/"
    )
    return response

@app.post("/auth/login")
async def form_login(request: Request):
    from fastapi.responses import RedirectResponse
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    user = authenticate(username, password)
    if not user:
        return RedirectResponse("/login?error=1", status_code=303)
    token = create_token(user["id"], user["username"])
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        "token", token, httponly=True, samesite="lax", max_age=7 * 24 * 3600, path="/"
    )
    return response

@app.post("/api/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("token", path="/")
    return response

@app.get("/api/me")
def me(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    conn = get_db()
    try:
        row = conn.execute("SELECT email FROM users WHERE id = ?", (user["id"],)).fetchone()
        user["email"] = row["email"] if row else ""
        return user
    finally:
        conn.close()

class UpdateEmailRequest(BaseModel):
    email: str = Field(max_length=255)

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6, max_length=128)

@app.put("/api/me/email")
def update_email(body: UpdateEmailRequest, request: Request):
    user = _require_user(request)
    conn = get_db()
    try:
        conn.execute("UPDATE users SET email = ? WHERE id = ?", (body.email.strip(), user["id"]))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@app.put("/api/me/password")
def change_password(body: ChangePasswordRequest, request: Request):
    from auth import authenticate, _hash_password
    user = _require_user(request)
    if not authenticate(user["username"], body.current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (_hash_password(body.new_password), user["id"]),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# --- Heatmap API ---

@app.get("/heatmap/{ticker}")
def heatmap(
    ticker: str,
    metric: Literal["openInterest", "volume", "impliedVolatility"] = "openInterest",
    max_expirations: int = Query(default=6, ge=1, le=12),
):
    try:
        chains = cached_fetch(ticker, max_expirations)
        return build_heatmap(chains, metric)
    except RateLimitError:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/probability/{ticker}")
def probability(
    ticker: str,
    max_expirations: int = Query(default=6, ge=1, le=12),
    near_pct: float = Query(default=0.25, gt=0.0, le=1.0),
    band_width: float = Query(default=5.0, gt=0.0, le=100.0),
):
    try:
        chains = cached_fetch(ticker, max_expirations)
        return build_probability_heatmap(chains, near_pct=near_pct, band_width=band_width)
    except RateLimitError:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/debug/iv/{ticker}")
def debug_iv(ticker: str, max_expirations: int = Query(default=6, ge=1, le=12)):
    """Temporary: returns raw per-expiry strike/IV pairs to diagnose heatmap stability."""
    chains = cached_fetch(ticker, max_expirations)
    from datetime import datetime
    import math
    now = datetime.utcnow().timestamp()
    result = []
    for chain in chains:
        opt = chain["optionChain"]["result"][0]["options"][0]
        exp_ts = opt.get("expirationDate")
        t = (exp_ts - now) / (365.25 * 24 * 3600) if exp_ts else None
        label = datetime.utcfromtimestamp(exp_ts).strftime("%Y-%m-%d") if exp_ts else "?"
        pairs = []
        for side in ("calls", "puts"):
            for o in opt.get(side, []):
                k = o.get("strike")
                iv = o.get("impliedVolatility")
                if k is not None and iv is not None:
                    pairs.append({"strike": k, "iv": iv, "side": side})
        pairs.sort(key=lambda x: x["strike"])
        result.append({"expiry": label, "t_years": round(t, 4) if t else None, "quotes": pairs})
    return result


@app.get("/exposure/{ticker}")
def exposure(
    ticker: str,
    kind: Literal["gamma", "vanna"] = "gamma",
    max_expirations: int = Query(default=6, ge=1, le=12),
):
    try:
        chains = cached_fetch(ticker, max_expirations)
        return build_exposure_heatmap(chains, kind=kind)
    except RateLimitError:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/heatmap/{ticker}/refresh")
def refresh(ticker: str):
    invalidate(ticker)
    return {"status": "invalidated", "ticker": ticker.upper()}


# --- Company Info API ---

COMPANY_CACHE_TTL = 60 * 60

@app.get("/api/company/{ticker}")
def company_info(ticker: str):
    ticker = ticker.upper()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT data, fetched_at FROM company_data WHERE ticker = ? LIMIT 1",
            (ticker,),
        ).fetchone()

        needs_fetch = row is None or seconds_since(row["fetched_at"]) > COMPANY_CACHE_TTL

        if needs_fetch:
            data = scrape_company_info(ticker)
            if data:
                conn.execute("DELETE FROM company_data WHERE ticker = ?", (ticker,))
                conn.execute(
                    "INSERT INTO company_data (ticker, data) VALUES (?, ?)",
                    (ticker, json.dumps(data)),
                )
                conn.commit()
                return {"ticker": ticker, **data}
            elif row:
                return {"ticker": ticker, **json.loads(row["data"])}
            return {"ticker": ticker}

        return {"ticker": ticker, **json.loads(row["data"])}
    finally:
        conn.close()


# --- Divergence API ---

@app.get("/api/divergence/{ticker}")
def divergence(ticker: str):
    ticker = ticker.upper()

    try:
        chains = cached_fetch(ticker, 6)
    except RateLimitError:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Could not fetch options data")

    import pandas as pd
    frames = [_parse_chain(c) for c in chains]
    df = pd.concat(frames, ignore_index=True)
    df["openInterest"] = pd.to_numeric(df["openInterest"], errors="coerce").fillna(0)

    total_call_oi = int(df[df["type"] == "call"]["openInterest"].sum())
    total_put_oi = int(df[df["type"] == "put"]["openInterest"].sum())
    total_oi = total_call_oi + total_put_oi
    pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else 999.0

    strikes = sorted(df["strike"].unique())
    max_pain_strike = None
    min_pain = float("inf")
    for s in strikes:
        pain = 0.0
        for _, r in df.iterrows():
            if r["type"] == "call" and s > r["strike"]:
                pain += (s - r["strike"]) * r["openInterest"]
            elif r["type"] == "put" and s < r["strike"]:
                pain += (r["strike"] - s) * r["openInterest"]
        if pain < min_pain:
            min_pain = pain
            max_pain_strike = float(s)

    if pcr < 0.7:
        positioning = "bullish"
    elif pcr > 1.3:
        positioning = "bearish"
    else:
        positioning = "neutral"

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT sentiment_score FROM news WHERE ticker = ? ORDER BY fetched_at DESC LIMIT 20",
            (ticker,),
        ).fetchall()
    finally:
        conn.close()

    if rows:
        avg_sent = round(sum(r["sentiment_score"] for r in rows) / len(rows), 4)
    else:
        avg_sent = 0.0

    if avg_sent >= 0.05:
        sent_label = "bullish"
    elif avg_sent <= -0.05:
        sent_label = "bearish"
    else:
        sent_label = "neutral"

    divergence_detected = False
    divergence_type = None
    signal = None

    if positioning == "bullish" and sent_label == "bearish":
        divergence_detected = True
        divergence_type = "bearish_divergence"
        signal = "Options flow is bullish but sentiment is bearish. Smart money may be positioning ahead of the narrative."
    elif positioning == "bearish" and sent_label == "bullish":
        divergence_detected = True
        divergence_type = "bullish_divergence"
        signal = "Options flow is bearish but sentiment is bullish. The crowd may be overlooking downside risk."
    elif positioning == "bullish" and sent_label == "bullish":
        signal = "Options flow and sentiment are aligned bullish. Consensus trade."
    elif positioning == "bearish" and sent_label == "bearish":
        signal = "Options flow and sentiment are aligned bearish. Consensus trade."
    else:
        signal = "No strong directional signal from either options or sentiment."

    return {
        "ticker": ticker,
        "put_call_ratio": pcr,
        "positioning": positioning,
        "avg_sentiment": avg_sent,
        "sentiment_label": sent_label,
        "divergence": divergence_detected,
        "divergence_type": divergence_type,
        "signal": signal,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
        "total_oi": total_oi,
        "max_pain": max_pain_strike,
    }


# --- News API ---

NEWS_CACHE_TTL = 15 * 60

@app.get("/api/news/{ticker}")
def get_news(ticker: str, limit: int = Query(default=10, ge=1, le=50)):
    ticker = ticker.upper()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT fetched_at FROM news WHERE ticker = ? ORDER BY fetched_at DESC LIMIT 1",
            (ticker,),
        ).fetchone()

        needs_fetch = row is None or seconds_since(row["fetched_at"]) > NEWS_CACHE_TTL

        if needs_fetch:
            articles = scrape_all(ticker)
            articles = analyze_articles(articles)
            conn.execute("DELETE FROM news WHERE ticker = ?", (ticker,))
            for a in articles:
                conn.execute(
                    """INSERT INTO news (ticker, title, source, url, snippet, published,
                       sentiment_score, sentiment_label) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ticker, a["title"], a["source"], a["url"], a["snippet"],
                     a["published"], a["sentiment"]["compound"], a["sentiment"]["label"]),
                )
            conn.commit()

        rows = conn.execute(
            "SELECT id, title, source, url, snippet, published, sentiment_score, sentiment_label, fetched_at "
            "FROM news WHERE ticker = ? ORDER BY fetched_at DESC LIMIT ?",
            (ticker, limit),
        ).fetchall()

        return {"ticker": ticker, "articles": [dict(r) for r in rows]}
    finally:
        conn.close()


# --- Sentiment Heatmap API ---

DEFAULT_TICKERS = ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "AMD"]

@app.get("/api/sentiment-heatmap")
def sentiment_heatmap(request: Request, tickers: str = Query(default="")):
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()] if tickers else []

    if not ticker_list:
        user = get_current_user(request)
        if user:
            conn = get_db()
            try:
                rows = conn.execute(
                    "SELECT ticker FROM watchlist WHERE user_id = ? ORDER BY added_at DESC",
                    (user["id"],),
                ).fetchall()
                ticker_list = [r["ticker"] for r in rows]
            finally:
                conn.close()

    if not ticker_list:
        ticker_list = DEFAULT_TICKERS

    sources = ["News", "Twitter", "Reddit", "Overall"]
    scores = []
    labels = []

    conn = get_db()
    try:
        for ticker in ticker_list:
            row = conn.execute(
                "SELECT fetched_at FROM news WHERE ticker = ? ORDER BY fetched_at DESC LIMIT 1",
                (ticker,),
            ).fetchone()

            needs_fetch = row is None or seconds_since(row["fetched_at"]) > NEWS_CACHE_TTL

            if needs_fetch:
                articles = scrape_all(ticker)
                articles = analyze_articles(articles)
                conn.execute("DELETE FROM news WHERE ticker = ?", (ticker,))
                for a in articles:
                    conn.execute(
                        """INSERT INTO news (ticker, title, source, url, snippet, published,
                           sentiment_score, sentiment_label) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (ticker, a["title"], a["source"], a["url"], a["snippet"],
                         a["published"], a["sentiment"]["compound"], a["sentiment"]["label"]),
                    )
                conn.commit()

            rows = conn.execute(
                "SELECT source, sentiment_score FROM news WHERE ticker = ? ORDER BY fetched_at DESC LIMIT 30",
                (ticker,),
            ).fetchall()

            news_scores = [r["sentiment_score"] for r in rows if "Twitter" not in r["source"] and "Reddit" not in r["source"]]
            twitter_scores = [r["sentiment_score"] for r in rows if "Twitter" in r["source"]]
            reddit_scores = [r["sentiment_score"] for r in rows if "Reddit" in r["source"]]
            all_scores = [r["sentiment_score"] for r in rows]

            ticker_row = []
            ticker_labels_row = []
            for group in [news_scores, twitter_scores, reddit_scores, all_scores]:
                if group:
                    avg = sum(group) / len(group)
                    ticker_row.append(round(avg, 3))
                    lbl = "positive" if avg >= 0.05 else "negative" if avg <= -0.05 else "neutral"
                    ticker_labels_row.append(f"{lbl} ({len(group)})")
                else:
                    ticker_row.append(0.0)
                    ticker_labels_row.append("no data")

            scores.append(ticker_row)
            labels.append(ticker_labels_row)
    finally:
        conn.close()

    return {
        "tickers": ticker_list,
        "sources": sources,
        "scores": scores,
        "labels": labels,
    }


# --- Watchlist API ---

def _require_user(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

@app.get("/api/watchlist")
def get_watchlist(request: Request):
    user = _require_user(request)
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT ticker, added_at FROM watchlist WHERE user_id = ? ORDER BY added_at DESC",
            (user["id"],),
        ).fetchall()
        return {"watchlist": [dict(r) for r in rows]}
    finally:
        conn.close()

@app.post("/api/watchlist")
def add_to_watchlist(body: WatchlistRequest, request: Request):
    user = _require_user(request)
    ticker = body.ticker.upper().strip()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO watchlist (user_id, ticker) VALUES (?, ?) "
            "ON CONFLICT (user_id, ticker) DO NOTHING",
            (user["id"], ticker),
        )
        conn.commit()
        return {"ok": True, "ticker": ticker}
    finally:
        conn.close()

@app.delete("/api/watchlist/{ticker}")
def remove_from_watchlist(ticker: str, request: Request):
    user = _require_user(request)
    ticker = ticker.upper().strip()
    conn = get_db()
    try:
        conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?",
            (user["id"], ticker),
        )
        conn.commit()
        return {"ok": True, "ticker": ticker}
    finally:
        conn.close()

@app.get("/api/watchlist/summary")
def watchlist_summary(request: Request):
    """Return watchlist tickers with their latest sentiment summary."""
    user = _require_user(request)
    conn = get_db()
    try:
        tickers = conn.execute(
            "SELECT ticker FROM watchlist WHERE user_id = ? ORDER BY added_at DESC",
            (user["id"],),
        ).fetchall()

        summaries = []
        for row in tickers:
            t = row["ticker"]
            latest = conn.execute(
                "SELECT sentiment_score, sentiment_label FROM news WHERE ticker = ? "
                "ORDER BY fetched_at DESC LIMIT 5",
                (t,),
            ).fetchall()
            avg_score = 0.0
            if latest:
                avg_score = sum(r["sentiment_score"] for r in latest) / len(latest)
            label = "positive" if avg_score >= 0.05 else "negative" if avg_score <= -0.05 else "neutral"
            summaries.append({
                "ticker": t,
                "avg_sentiment": round(avg_score, 3),
                "sentiment_label": label,
                "article_count": len(latest),
            })
        return {"summaries": summaries}
    finally:
        conn.close()


# --- Push Notifications API ---

@app.get("/api/push/vapid-key")
def get_vapid_key():
    return {"publicKey": VAPID_PUBLIC_KEY}

@app.post("/api/push/subscribe")
async def push_subscribe(request: Request):
    user = _require_user(request)
    body = await request.json()
    sub_json = json.dumps(body.get("subscription", body))
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO push_subscriptions (user_id, subscription_json) VALUES (?, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET subscription_json = EXCLUDED.subscription_json",
            (user["id"], sub_json),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@app.delete("/api/push/subscribe")
def push_unsubscribe(request: Request):
    user = _require_user(request)
    conn = get_db()
    try:
        conn.execute("DELETE FROM push_subscriptions WHERE user_id = ?", (user["id"],))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# --- Divergence Scanner ---

def _compute_divergence(ticker: str) -> dict | None:
    import pandas as pd
    try:
        chains = cached_fetch(ticker, 6)
    except Exception:
        return None

    frames = [_parse_chain(c) for c in chains]
    df = pd.concat(frames, ignore_index=True)
    df["openInterest"] = pd.to_numeric(df["openInterest"], errors="coerce").fillna(0)

    total_call_oi = int(df[df["type"] == "call"]["openInterest"].sum())
    total_put_oi = int(df[df["type"] == "put"]["openInterest"].sum())
    if total_call_oi == 0:
        return None
    pcr = total_put_oi / total_call_oi

    positioning = "bullish" if pcr < 0.7 else "bearish" if pcr > 1.3 else "neutral"

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT sentiment_score FROM news WHERE ticker = ? ORDER BY fetched_at DESC LIMIT 20",
            (ticker,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return None

    avg_sent = sum(r["sentiment_score"] for r in rows) / len(rows)
    sent_label = "bullish" if avg_sent >= 0.05 else "bearish" if avg_sent <= -0.05 else "neutral"

    if positioning == "bullish" and sent_label == "bearish":
        return {"ticker": ticker, "type": "bearish_divergence", "pcr": round(pcr, 2), "sentiment": round(avg_sent, 3),
                "msg": f"{ticker}: Bullish options flow but bearish sentiment (P/C {pcr:.2f}, Sent {avg_sent:+.3f})"}
    elif positioning == "bearish" and sent_label == "bullish":
        return {"ticker": ticker, "type": "bullish_divergence", "pcr": round(pcr, 2), "sentiment": round(avg_sent, 3),
                "msg": f"{ticker}: Bearish options flow but bullish sentiment (P/C {pcr:.2f}, Sent {avg_sent:+.3f})"}
    return None


def _scan_and_notify() -> list[dict]:
    conn = get_db()
    try:
        users = conn.execute(
            "SELECT DISTINCT ps.user_id, ps.subscription_json FROM push_subscriptions ps"
        ).fetchall()
        if not users:
            return []

        all_tickers = set()
        user_tickers = {}
        for u in users:
            uid = u["user_id"]
            tickers = conn.execute(
                "SELECT ticker FROM watchlist WHERE user_id = ?", (uid,)
            ).fetchall()
            ut = [r["ticker"] for r in tickers]
            user_tickers[uid] = {"tickers": ut, "sub": json.loads(u["subscription_json"])}
            all_tickers.update(ut)
    finally:
        conn.close()

    alerts = []
    divergences = {}
    for i, ticker in enumerate(all_tickers):
        if i > 0:
            time.sleep(SCAN_TICKER_DELAY)
        d = _compute_divergence(ticker)
        if d:
            divergences[ticker] = d
            alerts.append(d)

    for uid, info in user_tickers.items():
        for ticker in info["tickers"]:
            if ticker in divergences:
                d = divergences[ticker]
                send_push(
                    info["sub"],
                    title=f"Divergence: {ticker}",
                    body=d["msg"],
                    url=f"/?ticker={ticker}",
                )

    return alerts


@app.post("/api/scan")
def scan_watchlist(request: Request):
    user = _require_user(request)
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT ticker FROM watchlist WHERE user_id = ?", (user["id"],)
        ).fetchall()
        tickers = [r["ticker"] for r in rows]
    finally:
        conn.close()

    if not tickers:
        return {"alerts": [], "message": "No tickers in watchlist"}

    alerts = []
    for i, ticker in enumerate(tickers):
        if i > 0:
            time.sleep(SCAN_TICKER_DELAY)
        d = _compute_divergence(ticker)
        if d:
            alerts.append(d)

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT subscription_json FROM push_subscriptions WHERE user_id = ?",
            (user["id"],),
        ).fetchone()
        if row and alerts:
            sub = json.loads(row["subscription_json"])
            for a in alerts:
                send_push(sub, title=f"Divergence: {a['ticker']}", body=a["msg"], url=f"/?ticker={a['ticker']}")
    finally:
        conn.close()

    return {"alerts": alerts, "scanned": len(tickers), "divergences_found": len(alerts)}


# --- Background Scanner ---

async def _background_scanner():
    while True:
        await asyncio.sleep(15 * 60)
        try:
            # Run in a worker thread: the scan does blocking network I/O and now
            # sleeps between tickers, so calling it inline would freeze the event
            # loop (and every other request) for the duration of the scan.
            alerts = await asyncio.to_thread(_scan_and_notify)
            if alerts:
                logger.info(f"Background scan found {len(alerts)} divergences")
        except Exception as e:
            logger.error(f"Background scan error: {e}")


@app.on_event("startup")
def start_background_scanner():
    asyncio.get_event_loop().create_task(_background_scanner())
