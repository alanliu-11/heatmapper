FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install system deps for building some Python packages and Postgres client libs
RUN apt-get update \
    && apt-get install -y build-essential libpq-dev gcc musl-dev --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip setuptools wheel && pip install -r requirements.txt

COPY . .
EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
