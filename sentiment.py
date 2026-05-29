from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_analyzer = SentimentIntensityAnalyzer()


def analyze(text: str) -> dict:
    """Return sentiment scores for a piece of text.

    Returns dict with: compound (-1 to 1), label (positive/negative/neutral).
    """
    scores = _analyzer.polarity_scores(text)
    compound = scores["compound"]

    if compound >= 0.05:
        label = "positive"
    elif compound <= -0.05:
        label = "negative"
    else:
        label = "neutral"

    return {"compound": compound, "label": label}


def analyze_articles(articles: list[dict]) -> list[dict]:
    """Add sentiment scores to a list of news articles.

    Analyzes title + snippet combined for better accuracy.
    """
    results = []
    for article in articles:
        text = f"{article['title']}. {article.get('snippet', '')}"
        scores = analyze(text)
        results.append({**article, "sentiment": scores})
    return results
