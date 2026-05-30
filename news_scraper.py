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


def scrape_reddit(ticker: str, num_results: int = 10) -> list[dict]:
    if not BRIGHTDATA_API_TOKEN:
        return []

    try:
        url = (
            f"https://www.google.com/search?q={ticker}+stock+site%3Areddit.com"
            f"&num={num_results}&hl=en&gl=us&tbs=qdr:w"
        )
        body = _brightdata_request(url)

        posts = []
        for item in body.get("organic", []):
            link = item.get("link", "")
            if "reddit.com" not in link:
                continue
            subreddit = ""
            if "/r/" in link:
                subreddit = "r/" + link.split("/r/")[1].split("/")[0]
            posts.append({
                "title": item.get("title", ""),
                "source": f"Reddit {subreddit}".strip(),
                "url": link,
                "snippet": item.get("description", ""),
                "published": item.get("date", ""),
            })
        return posts
    except Exception:
        return []


WEB_UNLOCKER_ZONE = os.getenv("BRIGHTDATA_WEB_UNLOCKER_ZONE", "web_unlocker1")


def scrape_company_info(ticker: str) -> dict:
    if not BRIGHTDATA_API_TOKEN:
        return {}

    try:
        resp = requests.post(
            API_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {BRIGHTDATA_API_TOKEN}",
            },
            json={
                "zone": WEB_UNLOCKER_ZONE,
                "url": f"https://stockanalysis.com/stocks/{ticker.lower()}/",
                "format": "raw",
            },
            timeout=60,
        )
        resp.raise_for_status()
        html = resp.text
        if len(html) < 500:
            return {}
        return _parse_stockanalysis_html(html)
    except Exception:
        return {}


def _extract_sa(html: str, label: str) -> str:
    import re
    pattern = rf'{re.escape(label)}</(?:a|td)>(?:<!--[^>]*-->)*(?:</td>)?\s*<td[^>]*>([\s\S]*?)</td>'
    m = re.search(pattern, html)
    if m:
        val = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        val = re.sub(r'<!--.*?-->', '', val).strip()
        val = val.split("\n")[0].strip()
        return val
    return ""


def _clean_value(val: str) -> str:
    import re
    return re.split(r'\s+[+\-]', val)[0].strip() if val else ""


def _parse_stockanalysis_html(html: str) -> dict:
    import re
    analyst_match = re.search(r'Analyst (?:Rating|Consensus).*?<td[^>]*>([\s\S]*?)</td>', html)
    analyst = ""
    if analyst_match:
        analyst = re.sub(r'<[^>]+>', '', analyst_match.group(1)).strip().split("\n")[0].strip()

    return {
        "market_cap": _clean_value(_extract_sa(html, "Market Cap")),
        "pe_ratio": _extract_sa(html, "PE Ratio") or _extract_sa(html, "Forward P/E"),
        "eps": _clean_value(_extract_sa(html, "EPS")),
        "week_52_range": _extract_sa(html, "52-Week Range"),
        "avg_volume": _extract_sa(html, "Volume"),
        "dividend_yield": _extract_sa(html, "Dividend Yield"),
        "beta": _extract_sa(html, "Beta"),
        "earnings_date": _extract_sa(html, "Earnings Date"),
        "recommendation": analyst,
        "target_price": _extract_sa(html, "Price Target") or _extract_sa(html, "Analyst Target"),
        "source": "brightdata_web_unlocker",
    }


def scrape_all(ticker: str, news_count: int = 8, tweet_count: int = 5, reddit_count: int = 5) -> list[dict]:
    articles = scrape_news(ticker, news_count)
    tweets = scrape_twitter(ticker, tweet_count)
    reddit = scrape_reddit(ticker, reddit_count)
    return articles + tweets + reddit
