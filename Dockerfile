FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Request lines: AegisASGILogMiddleware only (avoids duplicate Hypercorn access lines).
CMD ["hypercorn", "src.api.main:app", "--bind", "0.0.0.0:8000", "--workers", "1", "--error-logfile", "-"]
