import os
import requests
from dotenv import load_dotenv

load_dotenv()

BRIGHTDATA_HOST = "brd.superproxy.io"
BRIGHTDATA_PORT = 22225
BRIGHTDATA_USER = os.environ["BRIGHTDATA_USER"]
BRIGHTDATA_PASS = os.environ["BRIGHTDATA_PASS"]

proxies = {
    "http":  f"http://{BRIGHTDATA_USER}:{BRIGHTDATA_PASS}@{BRIGHTDATA_HOST}:{BRIGHTDATA_PORT}",
    "https": f"http://{BRIGHTDATA_USER}:{BRIGHTDATA_PASS}@{BRIGHTDATA_HOST}:{BRIGHTDATA_PORT}",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def fetch_options_chain(ticker: str, expiry_timestamp: int = None) -> dict:
    url = f"https://query1.finance.yahoo.com/v7/finance/options/{ticker.upper()}"
    if expiry_timestamp:
        url += f"?date={expiry_timestamp}"

    resp = requests.get(url, proxies=proxies, headers=HEADERS, verify=False, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_all_expirations(ticker: str, max_expirations: int = 6) -> list[dict]:
    base = fetch_options_chain(ticker)
    result = base["optionChain"]["result"][0]
    expiry_timestamps = result["expirationDates"][:max_expirations]

    chains = [base]
    for ts in expiry_timestamps[1:]:
        chains.append(fetch_options_chain(ticker, ts))

    return chains
