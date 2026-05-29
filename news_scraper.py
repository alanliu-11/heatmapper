import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

BRIGHTDATA_API_TOKEN = os.getenv("BRIGHTDATA_API_TOKEN", "")
BRIGHTDATA_ZONE = os.getenv("BRIGHTDATA_ZONE", "serp_api1")
API_URL = "https://api.brightdata.com/request"


def _brightdata_request(google_url: str) -> dict:
    resp = requests.post(
        API_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {BRIGHTDATA_API_TOKEN}",
        },
        json={"zone": BRIGHTDATA_ZONE, "url": google_url, "format": "json"},
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    body = data.get("body", "{}")
    if isinstance(body, str):
        body = json.loads(body)
    return body


def scrape_news(ticker: str, num_results: int = 10) -> list[dict]:
    if not BRIGHTDATA_API_TOKEN:
        return []

    try:
        url = f"https://www.google.com/search?q={ticker}+stock+news&tbm=nws&num={num_results}&hl=en&gl=us"
        body = _brightdata_request(url)

        articles = []
        for item in body.get("news", []):
            articles.append({
                "title": item.get("title", ""),
                "source": item.get("source", ""),
                "url": item.get("link", ""),
                "snippet": item.get("description", ""),
                "published": item.get("date", ""),
            })
        return articles
    except Exception:
        return []


def scrape_twitter(ticker: str, num_results: int = 10) -> list[dict]:
    if not BRIGHTDATA_API_TOKEN:
        return []

    try:
        url = f"https://www.google.com/search?q={ticker}+stock+site%3Ax.com&num={num_results}&hl=en&gl=us&tbs=qdr:d"
        body = _brightdata_request(url)

        tweets = []
        for item in body.get("organic", []):
            title = item.get("title", "")
            link = item.get("link", "")
            if "x.com" not in link and "twitter.com" not in link:
                continue
            description = item.get("description", "")
            source = "X (Twitter)"
            handle = ""
            if "x.com/" in link:
                parts = link.split("x.com/")
                if len(parts) > 1:
                    handle = "@" + parts[1].split("/")[0]

            tweets.append({
                "title": title,
                "source": f"{source} {handle}".strip(),
                "url": link,
                "snippet": description,
                "published": item.get("date", ""),
            })
        return tweets
    except Exception:
        return []


def scrape_all(ticker: str, news_count: int = 8, tweet_count: int = 5) -> list[dict]:
    articles = scrape_news(ticker, news_count)
    tweets = scrape_twitter(ticker, tweet_count)
    return articles + tweets
