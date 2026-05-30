from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

_tokenizer = AutoTokenizer.from_pretrained("ProsusAI/finbert")
_model = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert")
_model.eval()

_LABELS = ["positive", "negative", "neutral"]


def analyze(text: str) -> dict:
    inputs = _tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        logits = _model(**inputs).logits
    probs = torch.softmax(logits, dim=1).squeeze()
    idx = probs.argmax().item()
    label = _LABELS[idx]
    compound = float(probs[0] - probs[1])
    return {"compound": round(compound, 4), "label": label}


def analyze_articles(articles: list[dict]) -> list[dict]:
    results = []
    for article in articles:
        text = f"{article['title']}. {article.get('snippet', '')}"
        scores = analyze(text)
        results.append({**article, "sentiment": scores})
    return results
