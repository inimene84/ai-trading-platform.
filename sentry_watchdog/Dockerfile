FROM python:3.11-slim

WORKDIR /watchdog

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY sentry_watchdog/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sentry_watchdog/ .

ENV PYTHONUNBUFFERED=1

CMD ["python", "watchdog.py"]
