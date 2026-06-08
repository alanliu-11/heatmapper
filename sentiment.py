import re
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_analyzer = SentimentIntensityAnalyzer()

# Question detection — these titles express no directional sentiment
_QUESTION_START = re.compile(
    r"^\s*(what|why|how|when|where|who|is|are|does|do|did|has|have|should|would|could|can|will)\b",
    re.IGNORECASE,
)
_BEGINNER_PHRASES = re.compile(
    r"\b(beginner|newbie|new to|noob|help me|anyone know|eli5|explain|advice)\b",
    re.IGNORECASE,
)

# Insider selling — detect sell verb + financial quantity anywhere in the title
_INSIDER_SELL_VERB = re.compile(
    r"\b(sell|sells|sold|offload|dump|dumps|dumped)\b", re.IGNORECASE
)
_INSIDER_SELL_AMOUNT = re.compile(
    r"\$[\d.,]+[kmb]*(\s*(million|billion|thousand))?"
    r"|\d[\d,]*\s*\w*\s*(shares?|warrants?|units?|options?)"
    r"|\b(half|quarter|several|hundreds?\s+of\s+thousands?)\s+(a\s+)?(million|thousand|hundred)?\s*(shares?|warrants?|units?|options?)",
    re.IGNORECASE,
)

# Finance-domain word adjustments applied after VADER
# Positive boosts (VADER underweights these in financial context)
_FINANCE_POSITIVE = re.compile(
    r"\b(upgrade|upgraded|outperform|buy rating|strong buy|beats?|beat estimates?|"
    r"record earnings|raised guidance|dividend increase|buyback|rally|surges?|breakout)\b",
    re.IGNORECASE,
)
# Negative boosts (VADER misses or underweights these)
_FINANCE_NEGATIVE = re.compile(
    r"\b(downgrade|downgraded|underperform|sell rating|misses?|missed estimates?|"
    r"tanks?|tanked|plunges?|plunged|crashes?|crashed|dilut|layoff|recall|"
    r"investigation|lawsuit|sec|subpoena|bankruptcy|default|debt|short seller|"
    r"insider sell|insider selling|hype)\b",
    re.IGNORECASE,
)


def _is_question(title: str) -> bool:
    t = title.strip()
    return t.endswith("?") or bool(_QUESTION_START.match(t)) or bool(_BEGINNER_PHRASES.search(t))


def _finance_adjustment(title: str) -> float:
    """Return a score delta in [-0.4, 0.4] based on finance-domain patterns."""
    if _INSIDER_SELL_VERB.search(title) and _INSIDER_SELL_AMOUNT.search(title):
        return -0.5
    delta = 0.0
    if _FINANCE_NEGATIVE.search(title):
        delta -= 0.4
    if _FINANCE_POSITIVE.search(title):
        delta += 0.2
    return delta


def analyze(text: str, title: str = "") -> dict:
    if title and _is_question(title):
        return {"compound": 0.0, "label": "neutral"}

    compound = _analyzer.polarity_scores(text)["compound"]
    compound = max(-1.0, min(1.0, compound + _finance_adjustment(title or text)))

    if compound >= 0.05:
        label = "positive"
    elif compound <= -0.05:
        label = "negative"
    else:
        label = "neutral"
    return {"compound": round(compound, 4), "label": label}


def analyze_articles(articles: list[dict]) -> list[dict]:
    results = []
    for article in articles:
        title = article.get("title", "")
        text = f"{title}. {article.get('snippet', '')}"
        scores = analyze(text, title=title)
        results.append({**article, "sentiment": scores})
    return results
