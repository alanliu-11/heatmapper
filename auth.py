import os
import hashlib
import secrets
import time
import jwt
from dotenv import load_dotenv
from database import get_db

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
TOKEN_TTL = 7 * 24 * 3600  # 7 days


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"{salt}${h.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    salt, h = stored.split("$", 1)
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return secrets.compare_digest(check.hex(), h)


def create_user(username: str, password: str) -> dict:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username.strip(), _hash_password(password)),
        )
        conn.commit()
        user = conn.execute(
            "SELECT id, username, created_at FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()
        return dict(user)
    finally:
        conn.close()


def authenticate(username: str, password: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, created_at FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()
        if row is None:
            return None
        if not _verify_password(password, row["password_hash"]):
            return None
        return {"id": row["id"], "username": row["username"], "created_at": row["created_at"]}
    finally:
        conn.close()


def create_token(user_id: int, username: str) -> str:
    return jwt.encode(
        {"sub": str(user_id), "username": username, "exp": int(time.time()) + TOKEN_TTL},
        SECRET_KEY,
        algorithm="HS256",
    )


def verify_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return {"id": int(payload["sub"]), "username": payload["username"]}
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
