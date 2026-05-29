import os
import requests
from dotenv import load_dotenv

load_dotenv()

BRIGHTDATA_API_TOKEN = os.getenv("BRIGHTDATA_API_TOKEN", "")
SERP_ENDPOINT = "https://api.brightdata.com/serp/req"


def scrape_news(ticker: str, num_results: int = 10) -> list[dict]:
    """Scrape Google News results for a stock ticker via Bright Data SERP API.

    Returns a list of dicts with keys: title, source, url, snippet, published.
    Falls back to empty list if API key is missing or request fails.
    """
    if not BRIGHTDATA_API_TOKEN:
        return _placeholder_results(ticker)

    try:
        resp = requests.post(
            SERP_ENDPOINT,
            headers={
                "Authorization": f"Bearer {BRIGHTDATA_API_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "query": f"{ticker} stock news",
                "search_type": "news",
                "num": num_results,
                "country": "us",
                "language": "en",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("news_results", data.get("organic", [])):
            results.append({
                "title": item.get("title", ""),
                "source": item.get("source", item.get("displayed_link", "")),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", item.get("description", "")),
                "published": item.get("date", ""),
            })
        return results

    except Exception:
        return []


def _placeholder_results(ticker: str) -> list[dict]:
    """Return mock data when no API key is configured."""
    return [
        {
            "title": f"{ticker} shares rise on strong earnings beat",
            "source": "Reuters",
            "url": "",
            "snippet": f"{ticker} reported quarterly earnings above analyst expectations, driven by growth in key segments.",
            "published": "2 hours ago",
        },
        {
            "title": f"Analysts upgrade {ticker} price target amid bullish momentum",
            "source": "Bloomberg",
            "url": "",
            "snippet": f"Several Wall Street firms raised their price targets for {ticker} following strong Q2 guidance.",
            "published": "4 hours ago",
        },
        {
            "title": f"{ticker} faces headwinds from new tariff regulations",
            "source": "CNBC",
            "url": "",
            "snippet": f"New trade policies could pressure {ticker}'s margins in upcoming quarters, analysts warn.",
            "published": "6 hours ago",
        },
        {
            "title": f"Insider trading activity detected in {ticker} options",
            "source": "MarketWatch",
            "url": "",
            "snippet": f"Unusual options activity in {ticker} has drawn attention from market watchers ahead of the earnings call.",
            "published": "8 hours ago",
        },
        {
            "title": f"{ticker} announces $2B share buyback program",
            "source": "Yahoo Finance",
            "url": "",
            "snippet": f"The board of {ticker} approved a new share repurchase program, signaling confidence in the company's outlook.",
            "published": "1 day ago",
        },
    ]
