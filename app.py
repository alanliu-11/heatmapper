from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from cache import cached_fetch, invalidate
from processor import build_heatmap, build_probability_heatmap
from database import init_db
from auth import create_user, authenticate, create_token, verify_token
import sqlite3

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
    return user


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
