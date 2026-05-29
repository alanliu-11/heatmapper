"""Weekly email digest for watchlist sentiment summaries.

Run via cron: python email_digest.py

Requires SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, and FROM_EMAIL in .env.
Each user needs an 'email' column in the users table (added by migration below).

This is a placeholder -- swap the SMTP section for SendGrid/Resend in production.
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from database import get_db

load_dotenv()

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "noreply@heatmapper.local")


def build_digest_html(username: str, summaries: list[dict]) -> str:
    rows = ""
    for s in summaries:
        color = "#22c55e" if s["label"] == "positive" else "#ef4444" if s["label"] == "negative" else "#6b6b6b"
        score = f"+{s['avg']:.3f}" if s["avg"] >= 0 else f"{s['avg']:.3f}"
        rows += f"""
        <tr>
          <td style="padding:8px 12px;font-family:monospace;font-weight:700;">{s['ticker']}</td>
          <td style="padding:8px 12px;color:{color};font-weight:600;">{s['label'].upper()} ({score})</td>
          <td style="padding:8px 12px;text-align:right;">{s['count']} articles</td>
        </tr>"""

    return f"""
    <div style="font-family:Inter,sans-serif;max-width:600px;margin:0 auto;color:#1a1a1a;">
      <h2 style="margin-bottom:4px;">Weekly Watchlist Digest</h2>
      <p style="color:#6b6b6b;margin-bottom:20px;">Hi {username}, here's your weekly sentiment summary.</p>
      <table style="width:100%;border-collapse:collapse;border:1px solid #e5e5e5;border-radius:8px;">
        <thead>
          <tr style="background:#f5f5f7;">
            <th style="padding:8px 12px;text-align:left;">Ticker</th>
            <th style="padding:8px 12px;text-align:left;">Sentiment</th>
            <th style="padding:8px 12px;text-align:right;">Coverage</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="color:#999;font-size:12px;margin-top:20px;">
        Sent by Heatmapper. Manage your watchlist at your dashboard.
      </p>
    </div>"""


def send_email(to_email: str, subject: str, html_body: str) -> bool:
    if not SMTP_HOST or not SMTP_USER:
        print(f"[DRY RUN] Would send to {to_email}: {subject}")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Failed to send to {to_email}: {e}")
        return False


def run_weekly_digest():
    conn = get_db()
    try:
        users = conn.execute(
            "SELECT DISTINCT u.id, u.username, u.email FROM users u "
            "JOIN watchlist w ON w.user_id = u.id "
            "WHERE u.email IS NOT NULL AND u.email != ''"
        ).fetchall()

        for user in users:
            tickers = conn.execute(
                "SELECT ticker FROM watchlist WHERE user_id = ?", (user["id"],)
            ).fetchall()

            summaries = []
            for t in tickers:
                ticker = t["ticker"]
                articles = conn.execute(
                    "SELECT sentiment_score, sentiment_label FROM news WHERE ticker = ? "
                    "ORDER BY fetched_at DESC LIMIT 20",
                    (ticker,),
                ).fetchall()

                if not articles:
                    continue

                avg = sum(a["sentiment_score"] for a in articles) / len(articles)
                label = "positive" if avg >= 0.05 else "negative" if avg <= -0.05 else "neutral"
                summaries.append({
                    "ticker": ticker,
                    "avg": avg,
                    "label": label,
                    "count": len(articles),
                })

            if not summaries:
                continue

            html = build_digest_html(user["username"], summaries)
            send_email(user["email"], "Your Weekly Heatmapper Digest", html)
            print(f"Sent digest to {user['username']} ({user['email']})")
    finally:
        conn.close()


if __name__ == "__main__":
    run_weekly_digest()
