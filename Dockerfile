FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
# Нормативные фикстуры входят в образ; пользовательские данные — только томом.
COPY data/fixtures ./data/fixtures
RUN pip install --no-cache-dir ".[pg,ocr]"

# Системные зависимости для OCR (сканы PDF): tesseract + poppler.
# Не критично для сборки — отсутствие OCR лишь отключает фолбэк.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/* || echo "OCR system deps skipped (offline build)"

# Данные (документы, отчёты, аудит) монтируются томом; WORKDIR=/app,
# поэтому каталог данных по умолчанию — /app/data (фикстуры уже внутри).
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["second-opinion", "serve", "--host", "0.0.0.0", "--port", "8000"]
