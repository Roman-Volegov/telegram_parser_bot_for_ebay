FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    CAMOUFOX_CACHE=/opt/camoufox

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && playwright install --with-deps chromium \
    && playwright install-deps firefox \
    && python -m camoufox fetch

COPY bot ./bot
COPY webapp ./webapp

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app /ms-playwright \
    && mkdir -p /opt/camoufox \
    && (cp -a /root/.cache/camoufox /opt/camoufox/cache 2>/dev/null || true) \
    && (cp -a /root/.cache/camoufox /home/appuser/.cache/camoufox 2>/dev/null || true) \
    && chown -R appuser:appuser /home/appuser /opt/camoufox || true

USER appuser

EXPOSE 8080

CMD ["python", "-m", "bot"]
