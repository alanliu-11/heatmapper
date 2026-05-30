from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_analyzer = SentimentIntensityAnalyzer()


def analyze(text: str) -> dict:
    compound = _analyzer.polarity_scores(text)["compound"]
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
        text = f"{article['title']}. {article.get('snippet', '')}"
        scores = analyze(text)
        results.append({**article, "sentiment": scores})
    return results
