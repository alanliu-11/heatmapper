import os
import json
from pywebpush import webpush, WebPushException
from dotenv import load_dotenv
from database import get_db

load_dotenv()

VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_CLAIMS = {"sub": "mailto:admin@heatmapper.local"}


def send_push(subscription_info: dict, title: str, body: str, url: str = "/") -> bool:
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        return False

    payload = json.dumps({"title": title, "body": body, "url": url})

    try:
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS,
        )
        return True
    except WebPushException:
        return False


def notify_watchlist_users(ticker: str, headline: str, sentiment_label: str):
    """Send push notification to all users watching this ticker."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT ps.subscription_json FROM push_subscriptions ps
               JOIN watchlist w ON w.user_id = ps.user_id
               WHERE w.ticker = ?""",
            (ticker.upper(),),
        ).fetchall()

        for row in rows:
            sub = json.loads(row["subscription_json"])
            emoji = "+" if sentiment_label == "positive" else "-" if sentiment_label == "negative" else "~"
            send_push(
                sub,
                title=f"[{emoji}] {ticker.upper()} News",
                body=headline,
                url=f"/?ticker={ticker.upper()}",
            )
    finally:
        conn.close()
