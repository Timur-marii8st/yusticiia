FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY src ./src
# Нормативные фикстуры входят в образ; пользовательские данные — только томом.
COPY data/fixtures ./data/fixtures
RUN pip install --no-cache-dir --no-deps .

# Системные зависимости для OCR (сканы PDF): tesseract + poppler.
# Не критично для сборки — отсутствие OCR лишь отключает фолбэк.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/* || echo "OCR system deps skipped (offline build)"

# Данные (документы, отчёты, аудит) монтируются томом; WORKDIR=/app,
# поэтому каталог данных по умолчанию — /app/data (фикстуры уже внутри).
RUN mkdir -p /app/data \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app \
    && chown -R app:app /app/data
VOLUME ["/app/data"]

# Приложение работает без прав root; на сервере каталог данных на хосте должен
# принадлежать тому же пользователю (SO_UID/SO_GID в docker-compose.yml).
USER app

EXPOSE 8000

CMD ["second-opinion", "serve", "--host", "0.0.0.0", "--port", "8000"]
