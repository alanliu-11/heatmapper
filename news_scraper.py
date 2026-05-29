import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

BRIGHTDATA_API_TOKEN = os.getenv("BRIGHTDATA_API_TOKEN", "")
BRIGHTDATA_ZONE = os.getenv("BRIGHTDATA_ZONE", "serp_api1")


def scrape_news(ticker: str, num_results: int = 10) -> list[dict]:
    if not BRIGHTDATA_API_TOKEN:
        return []

    try:
        url = f"https://www.google.com/search?q={ticker}+stock+news&tbm=nws&num={num_results}&hl=en&gl=us"

        resp = requests.post(
            "https://api.brightdata.com/request",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {BRIGHTDATA_API_TOKEN}",
            },
            json={"zone": BRIGHTDATA_ZONE, "url": url, "format": "json"},
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()

        body = data.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)

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
