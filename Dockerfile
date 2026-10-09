FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY src ./src
COPY data/fixtures /opt/fixtures
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN pip install --no-cache-dir --no-deps .

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/* || echo "OCR system deps skipped (offline build)"

RUN mkdir -p /app/data \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app \
    && chown -R app:app /app/data \
    && chmod +x /usr/local/bin/docker-entrypoint.sh
VOLUME ["/app/data"]

USER app

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["second-opinion", "serve", "--host", "0.0.0.0", "--port", "8000"]
