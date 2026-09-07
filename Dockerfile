FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt
RUN useradd --create-home --uid 10001 app
COPY config config
COPY listings listings
COPY collectors collectors
COPY manage.py .
RUN DJANGO_SECRET_KEY=build-only-static-key python manage.py collectstatic --noinput

FROM base AS web
USER app
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "2", "--timeout", "60"]

FROM base AS collector
RUN mkdir -p /app/.avito-state /app/.cian-state /app/.domclick-state \
    && chown app:app /app/.avito-state /app/.cian-state /app/.domclick-state \
    && chmod 700 /app/.avito-state /app/.cian-state /app/.domclick-state
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY requirements-collector.txt .
RUN pip install --no-cache-dir -r requirements-collector.txt \
    && playwright install --with-deps chromium \
    && apt-get update && apt-get install -y --no-install-recommends xauth openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /ms-playwright
USER app
CMD ["python", "manage.py", "collect_listings", "--source", "avito", "--mode", "fast"]
