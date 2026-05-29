from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from cache import cached_fetch, invalidate
from processor import build_heatmap, build_probability_heatmap
from database import init_db, get_db
from auth import create_user, authenticate, create_token, verify_token
from news_scraper import scrape_news
from sentiment import analyze_articles
from notifications import VAPID_PUBLIC_KEY
import sqlite3
import json

app = FastAPI(title="Options Heatmap")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
def startup():
    init_db()


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

@app.get("/")
def index(request: Request):
    user = get_current_user(request)
    if not user:
        return FileResponse("static/login.html")
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

@app.get("/settings")
def settings_page(request: Request):
    user = get_current_user(request)
    if not user:
        return FileResponse("static/login.html")
    return FileResponse("static/settings.html")

@app.get("/watchlist")
def watchlist_page(request: Request):
    user = get_current_user(request)
    if not user:
        return FileResponse("static/login.html")
    return FileResponse("static/watchlist.html")


# --- Auth API ---

@app.post("/api/register")
def register(body: RegisterRequest):
    try:
        user = create_user(body.username, body.password)
    except sqlite3.IntegrityError:
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
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/heatmap/{ticker}/refresh")
def refresh(ticker: str):
    invalidate(ticker)
    return {"status": "invalidated", "ticker": ticker.upper()}


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

        import time
        needs_fetch = row is None or (
            time.time() - time.mktime(time.strptime(row["fetched_at"], "%Y-%m-%d %H:%M:%S"))
            > NEWS_CACHE_TTL
        )

        if needs_fetch:
            articles = scrape_news(ticker)
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
            "INSERT OR IGNORE INTO watchlist (user_id, ticker) VALUES (?, ?)",
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
            "INSERT OR REPLACE INTO push_subscriptions (user_id, subscription_json) VALUES (?, ?)",
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
