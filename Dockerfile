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

# Hypercorn matches local `hypercorn src.api.main:app` (access logs to stdout)
CMD ["hypercorn", "src.api.main:app", "--bind", "0.0.0.0:8000", "--workers", "1", "--access-logfile", "-", "--error-logfile", "-"]
