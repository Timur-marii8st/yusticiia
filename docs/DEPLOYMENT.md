# DEPLOYMENT

## Профили развёртывания

### A. Локальный (поддерживаемый для MVP)

Один процесс, файловое хранилище, офлайн-LLM.

```bash
python -m venv .venv
.venv\Scripts\pip install -e .        # Windows
.venv/bin/pip install -e .            # Linux/macOS
.venv\Scripts\second-opinion serve --host 127.0.0.1 --port 8000
```

Требования: Python 3.11+, ~50 МБ на диске. Данные — в `./data`.

### B. Docker (одноконтейнерный)

```bash
docker build -t second-opinion .
docker run --rm -p 127.0.0.1:8000:8000 -v "${PWD}/data:/app/data" second-opinion
```

или

```bash
docker compose up
```

Контейнер слушает 8000 внутри; публикация только на loopback хоста.
Каталог `data/` монтируется томом — отчёты и аудит переживают рестарт.

#### PostgreSQL (опционально, ADR-006)

Файловый бэкенд — по умолчанию; postgres включается конфигурацией:

```bash
# Локально:
SO_STORAGE_BACKEND=postgres SO_DATABASE_URL=postgresql://second_opinion:second_opinion@127.0.0.1:5432/second_opinion \
  .venv/Scripts/second-opinion serve

# Docker (профиль postgres поднимает db + app):
docker compose --profile postgres up
# или: SO_STORAGE_BACKEND=postgres SO_DATABASE_URL=postgresql://... docker compose --profile postgres up --build
```

Таблицы `documents`/`analyses` (JSONB) и `norm_vectors` (pgvector) создаются
автоматически (образ `pgvector/pgvector:pg16` включает расширение `vector`);
доменные модели не меняются. Интеграционные тесты против реального
PostgreSQL — `SO_TEST_DATABASE_URL=... pytest -k postgres` (и `-k pgvector`).
Для семантики: `SO_RAG_MODE=hybrid_pgvector` (требует `postgres` + `DATABASE_URL`)
хранит эмбеддинги норм в `norm_vectors` и ранжирует через ANN (`<=>`).

### C. Сетевое развертывание (требует доработок)

Для многопользовательского доступа обязательно:

1. задать `SO_AUTH_TOKEN` (общий секрет) **и** `SO_JWT_SECRET` (ключ
   подписи персональных токенов, ≥32 байт): все `/api/*` требуют
   `Authorization: Bearer <токен>`; `/health`, `/metrics`, статика и UI
   остаются открытыми — токен вводится в форме один раз за сессию
   браузера. Это два **разных** секрета: общий Bearer раздаётся
   операторам, и знание его не должно позволять подписать токен с ролью
   `admin`. Без `SO_JWT_SECRET` ключ подписи случаен и живёт до
   перезапуска — выданные токены после рестарта недействительны.
   Пользователи хранятся в файлах (`<data>/store/auth`) либо в
   PostgreSQL при `SO_STORAGE_BACKEND=postgres`;
2. обратный прокси (nginx/Caddy) с TLS поверх токена;
3. ограничение доступа сетью суда (VPN/intranet);
4. регулярное резервное копирование `data/store/`;
5. выделенный сервисный пользователь ОС с правами только на каталог данных.

При `SO_REQUIRE_AUTH=1` сервер откажется стартовать на внешнем адресе
с пустым `SO_AUTH_TOKEN`; без этого флага выводится предупреждение.

Без выполнения п. 1–3 сетевое развертывание запрещено моделью угроз.
Токен-режим закрывает базовый уровень доступа, но не заменяет
персональные учётные записи.

## Конфигурация

Только переменные окружения (см. `.env.example`):

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `SO_DATA_DIR` | каталог хранения документов/отчётов/аудита | `./data` |
| `SO_FIXTURES_DIR` | каталог нормативных фикстур | `./data/fixtures` |
| `SO_LLM_PROVIDER` | `mock` \| `openai_compatible` | `mock` |
| `SO_OPENAI_BASE_URL` | базовый URL OpenAI-совместимого API | `https://api.openai.com/v1` |
| `SO_OPENAI_API_KEY` | ключ внешнего контура (никогда не коммитится) | пусто |
| `SO_LLM_MODEL` | имя модели внешнего контура | `gpt-4o-mini` |
| `SO_MAX_UPLOAD_BYTES` | лимит размера документа | 5242880 (5 МБ) |
| `SO_RAG_MODE` | `lexical` \| `hybrid` \| `hybrid_pgvector` | `lexical` |
| `SO_EMBEDDINGS_PROVIDER` | `hashing` \| `openai_compatible` | `hashing` |
| `SO_EMBEDDING_MODEL` | модель эмбеддингов (openai_compatible) | пусто |
| `SO_LLM_PROVIDER` | `mock` · `gigachat` · `openai_compatible` | `mock` |
| `SO_GIGACHAT_AUTH_KEY` | ключ авторизации GigaChat (Base64), меняется на временный токен | пусто |
| `SO_GIGACHAT_SCOPE` | область токена: PERS, CORP или B2B | `GIGACHAT_API_PERS` |
| `SO_GIGACHAT_CA_BUNDLE` | сертификаты НУЦ Минцифры, если контур их требует | пусто |
| `SO_LLM_AUTH_SCHEME` | схема в заголовке Authorization; Yandex AI Studio ждёт `Api-Key` | `Bearer` |
| `SO_AUTH_TOKEN` | Bearer-токен для `/api/*` (loopback-MVP) | пусто |
| `SO_JWT_SECRET` | ключ подписи JWT личных учёток, ≥32 байт | пусто (эфемерный) |
| `SO_REQUIRE_AUTH` | `1` — не стартовать на внешнем адресе без `SO_AUTH_TOKEN` | `0` |
| `SO_ADMIN_PASSWORD` | пароль для `create-admin` (разовый, для бутстрапа) | пусто |
| `SO_LOGIN_RATE_LIMIT` | попыток входа с одного клиента за окно (0 — без лимита) | 20 |
| `SO_LOGIN_RATE_WINDOW_SECONDS` | ширина окна для лимита входа, секунды | 60 |
| `SO_STORAGE_BACKEND` | `file` \| `postgres` (ADR-006) | `file` |
| `SO_DATABASE_URL` | DSN PostgreSQL при `postgres` | пусто |
| `SO_METRICS_ENABLED` | `1` — включить сбор метрик (`/metrics`); иначе `{"enabled": false}` | `0` |

Секреты задаются через окружение/секрет-хранилище, не через репозиторий.

## Проверка после развёртывания

```bash
curl http://127.0.0.1:8000/health          # {"status":"ok",...}
curl http://127.0.0.1:8000/metrics        # {"enabled": false} или {"enabled": true, "metrics": {...}}
curl "http://127.0.0.1:8000/api/v1/search?q=покушение"
```

и смоук-сценарий из README («Демо-образец» → «Проанализировать»).

## Обновление

1. Остановить процесс.
2. Обновить код (`git pull`) / пересобрать образ.
3. Прогнать `make test` и `make eval` — пороги метрик должны выполняться.
4. Запустить и проверить `/health` + смоук-сценарий.
