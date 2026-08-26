FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
# Нормативные фикстуры входят в образ; пользовательские данные — только томом.
COPY data/fixtures ./data/fixtures
RUN pip install --no-cache-dir ".[pg]"

# Данные (документы, отчёты, аудит) монтируются томом; WORKDIR=/app,
# поэтому каталог данных по умолчанию — /app/data (фикстуры уже внутри).
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["second-opinion", "serve", "--host", "0.0.0.0", "--port", "8000"]
