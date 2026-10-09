# Второе мнение

Верифицируемая система юридического аудита и поддержки принятия решений для
конкурса Верховного Суда РФ «Алгоритм правосудия. Второе мнение».

## Описание системы

Система «второго мнения» для судьи: помогает проверить проект судебного акта
по формальным ограничениям назначения наказания и подобрать сопоставимые
материалы.

Конвейер:

```text
Судебный документ
  - извлечение юридически значимых фактов (с цитатами-доказательствами)
  - детерминированные проверки правил (пределы санкций, ст. 62/66/73 УК РФ)
  - нормы в редакции, действовавшей на юридически значимую дату
  - поиск норм по базе источников с цитированием (/api/v1/search)
  - сопоставимая практика + описательная статистика
  - проверяемое второе мнение с полной цепочкой обоснования
```

## Быстрый старт

Требования: Python 3.11+.

```bash
# Windows:
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
# Linux/macOS:
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# запуск сервера (по умолчанию офлайн: LLM-провайдер — мок)
.venv\Scripts\second-opinion serve          # Windows
.venv/bin/second-opinion serve              # Linux/macOS
# - http://127.0.0.1:8000
```

Эквивалент через make (Linux/macOS): `make install && make dev`; если `python3` в системе старше 3.11,
интерпретатор задаётся явно: `make install PYTHON=python3.12`.

Запуск модулем: `python -m second_opinion.cli serve` (после установки зависимостей;
все команды CLI доступны и так).

Внешний LLM по умолчанию ВЫКЛЮЧЕН. Включение — только явно через переменные
окружения (см. `.env.example`), реальные документы во внешний контур без
осознанного решения оператора не отправляются.

### Личный кабинет

Демо-сценарий работает без входа. Персональные учётные записи (JWT, роли
`judge`/`clerk`/`admin`) заводит и меняет только оператор сервера командами: ни создать запись,
ни сменить пароль через интерфейс или HTTP-API нельзя. Первый администратор:

```bash
export SO_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
second-opinion create-admin --email admin@court.example.ru
# пароль будет запрошен в терминале (или SO_ADMIN_PASSWORD / --password)
```

Пароля по умолчанию нет: его задаёт оператор (администратору не короче 12 символов).
Пользователи сохраняются в `<data>/store/auth`, поэтому переживают перезапуск.
`SO_JWT_SECRET` — отдельный секрет от `SO_AUTH_TOKEN`; если он не задан, ключ подписи
случаен и выданные токены действуют только до перезапуска.

Пароль забыт: оператор сервера задаёт новый командой, пароль вводится дважды в терминале
(или `SO_NEW_PASSWORD` / `--password`), блокировка входа снимается:

```bash
second-opinion reset-password --email admin@court.example.ru
```

Остальные учётные записи (пароль вводится дважды в терминале, либо `--password`, либо
`SO_NEW_PASSWORD`):

```bash
second-opinion create-user --email judge@court.example.ru --full-name "Иванов И.И." --role judge
second-opinion update-user --email judge@court.example.ru --role clerk
second-opinion update-user --email judge@court.example.ru --active no
second-opinion delete-user --email judge@court.example.ru
second-opinion list-users
```

Команды работают с тем хранилищем, которое задано (`SO_STORAGE_BACKEND`): файлы или та же
PostgreSQL, где лежат документы и отчёты (таблица `users`, читаемое представление
`user_accounts` с адресом, ролью и состоянием). Последнего активного администратора понизить,
заблокировать и удалить нельзя. Права по ролям: правку корпуса норм («Состояние нормативного
корпуса») видит и делает только администратор. Как устроены пароли целиком —
[docs/SECURITY.md](docs/SECURITY.md#пароли).

## Docker

```bash
docker build -t second-opinion .
docker run --rm -p 127.0.0.1:8000:8000 -v "${PWD}/data:/app/data" second-opinion
# или:
docker compose up
```

Контейнер слушает 8000 внутри; публикация только на loopback хоста.
Каталог `data/` монтируется томом — отчёты и аудит переживают рестарт.
Процесс в контейнере работает не от root (uid 10001). На Linux каталог `data/` на
хосте должен принадлежать тому же пользователю: `mkdir -p data && sudo chown 10001:10001 data`
либо запускать с `SO_UID=$(id -u) SO_GID=$(id -g) docker compose up`.

### Развёртывание на сервере (Ubuntu), пошагово

1. Поставьте Docker Engine с плагином `docker compose` (шаг 2 в
   [docs/DEPLOYMENT_VLLM.md](docs/DEPLOYMENT_VLLM.md) или инструкция на docs.docker.com).
2. Скопируйте репозиторий на сервер и перейдите в него:
   `git clone <адрес репозитория> /opt/second-opinion && cd /opt/second-opinion`.
3. Задайте настройки файлом `.env` (режим личных учётных записей, ключ подписи токенов,
   `Secure` на cookie за TLS):

   ```bash
   printf 'SO_AUTH_MODE=accounts\nSO_JWT_SECRET=%s\nSO_COOKIE_SECURE=1\n' "$(openssl rand -hex 32)" > .env
   ```

4. Подготовьте каталог данных (контейнер работает от пользователя 10001):
   `mkdir -p data && sudo chown 10001:10001 data`.
5. Запустите: `docker compose up -d --build`. При первом запуске нормы и дела копируются из
   образа в `data/fixtures`.
6. Заведите администратора (пароль не короче 12 символов, вводится дважды):
   `docker compose exec app second-opinion create-admin --email admin@court.example.ru`.
7. Заведите судей и помощников:
   `docker compose exec app second-opinion create-user --email judge@court.example.ru --full-name "Иванов И.И." --role judge`.
   Пароль пользователю передаёт оператор: сменить его через интерфейс нельзя.
8. Поставьте перед приложением обратный прокси с TLS (nginx или Caddy, например
   `reverse_proxy 127.0.0.1:8000`) и ограничьте доступ сетью суда. Порт 8000 публикуется
   только на loopback сервера.
9. Проверьте: `curl -s http://127.0.0.1:8000/health` отвечает `"auth_mode":"accounts"`, а
   через прокси открывается страница входа.
10. Обновление: `git pull && docker compose up -d --build` (данные и учётные записи
    остаются в `data/`). Журнал: `docker compose logs -f app`. Резервные копии:
    [docs/PRIVACY.md](docs/PRIVACY.md#резервные-копии).

Нужна PostgreSQL вместо файлов: добавьте в `.env` строки `SO_STORAGE_BACKEND=postgres`,
`SO_DB_ADMIN_PASSWORD`, `SO_DB_APP_PASSWORD` и `SO_DATABASE_URL=postgresql://so_app:<пароль
приложения>@db:5432/second_opinion` и запускайте `docker compose --profile postgres up -d
--build`; учётные записи тогда лежат в той же базе, команды из шагов 6 и 7 те же.

### PostgreSQL (опционально)

Файловый бэкенд — по умолчанию; postgres включается конфигурацией:

```bash
# Локально:
SO_STORAGE_BACKEND=postgres SO_DATABASE_URL=postgresql://so_app:second_opinion@127.0.0.1:5432/second_opinion \
  .venv/bin/second-opinion serve

# Docker (профиль postgres поднимает db + app):
docker compose --profile postgres up
```

Приложение подключается ролью `so_app` без прав суперпользователя: её создаёт
`deploy/postgres-init/01-app-role.sh` при первом запуске пустого тома (пароль
`SO_DB_APP_PASSWORD`, пароль администратора БД `SO_DB_ADMIN_PASSWORD`; значения по
умолчанию годятся только для демонстрации на loopback, на сервере задайте свои).
Скрипт инициализации не выполняется повторно на уже созданном томе: роль в
существующей базе заводится вручную (`CREATE ROLE so_app LOGIN ...`).

Таблицы `documents`/`analyses` (JSONB) и `norm_vectors` (pgvector) создаются
автоматически (образ `pgvector/pgvector:pg16` включает расширение `vector`).
Интеграционные тесты против реального PostgreSQL —
`SO_TEST_DATABASE_URL=... pytest -k postgres` (и `-k pgvector`). Для
семантического поиска: `SO_RAG_MODE=hybrid_pgvector` хранит эмбеддинги норм
в `norm_vectors` и ранжирует через ANN (`<=>`).

Нормы по умолчанию читаются из файлов (`SO_NORMS_BACKEND=file`). Чтобы хранить
их в PostgreSQL (`SO_NORMS_BACKEND=postgres`, нужен `SO_DATABASE_URL`), один раз
перенесите файловый корпус командой `second-opinion norms-import`: она читает
`data/fixtures/norms` и записывает нормы в базу.

### Языковая модель в закрытом контуре (vLLM, опционально)

По умолчанию модель выключена (`SO_LLM_PROVIDER=mock`). Если она нужна, то
по решению НТ-02 ([docs/TZ.md](docs/TZ.md), раздел 2.10) только своя, на
той же машине, а не облачный API. В `docker-compose.yml` для этого два
профиля: `vllm` (Linux с видеокартой NVIDIA) и `vllm-cpu` (без видеокарты,
только проверка протокола). Порт модели публикуется только на loopback,
`app` обращается к ней по имени `vllm` во внутренней сети.

```bash
SO_LLM_PROVIDER=openai_compatible SO_OPENAI_BASE_URL=http://vllm:8000/v1 \
SO_OPENAI_API_KEY=$(openssl rand -hex 24) SO_LLM_MODEL=Qwen/Qwen2.5-7B-Instruct \
  docker compose --profile vllm up -d
```

Установка драйвера, Docker и NVIDIA Container Toolkit на Ubuntu, проверка
сервера (`scripts/vllm_preflight.sh`), закрытый контур без интернета и
разбор ошибок описаны в [docs/DEPLOYMENT_VLLM.md](docs/DEPLOYMENT_VLLM.md).
Проверка связки на запущенной модели - `scripts/check_llm_contract.py` и
`python -m evaluation.llm_extraction_probe`.

### Сетевое развёртывание (не loopback)

Для многопользовательского доступа обязательно:

1. задать `SO_AUTH_TOKEN` (общий секрет) **и** `SO_JWT_SECRET` (ключ подписи
   персональных токенов, ≥32 байт) — это два **разных** секрета, знание
   общего Bearer не должно позволять подписать токен с ролью `admin`;
2. обратный прокси (nginx/Caddy) с TLS поверх токена;
3. ограничение доступа сетью суда (VPN/intranet);
4. регулярное зашифрованное резервное копирование `data/store/` и базы
   ([порядок](docs/PRIVACY.md#резервные-копии));
5. выделенный сервисный пользователь ОС с правами только на каталог данных.

При `SO_REQUIRE_AUTH=1` сервер откажется стартовать на внешнем адресе с
пустым `SO_AUTH_TOKEN`. Без выполнения пп. 1–3 сетевое развёртывание
запрещено моделью угроз ([docs/SECURITY.md](docs/SECURITY.md)).

## Пример рабочего сценария

1. Откройте `http://127.0.0.1:8000` и нажмите «Демо-образец»
   (синтетический приговор по ч. 2 ст. 158 УК РФ, особый порядок).
2. Нажмите «Проанализировать».
3. Раскройте любое обстоятельство — увидите точную цитату и оффсеты в тексте.
4. Раскройте проверку норм — увидите цепочку: правило; факты; норма;
   редакция; объяснение.
5. Посмотрите сопоставимые дела и описательную статистику.

Консольный анализ файла:

```bash
second-opinion analyze data/fixtures/sample_documents/sample_228_attempt_violation.txt
```

## Переменные окружения

Полный образец — [.env.example](.env.example). Самое важное:

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `SO_DATA_DIR` | каталог хранения документов/отчётов/аудита | `./data` |
| `SO_FIXTURES_DIR` | каталог нормативных фикстур | `./data/fixtures` |
| `SO_LLM_PROVIDER` | `mock` \| `gigachat` \| `openai_compatible` | `mock` |
| `SO_MAX_UPLOAD_BYTES` | лимит размера документа | 5242880 (5 МБ) |
| `SO_RAG_MODE` | `lexical` \| `hybrid` \| `hybrid_pgvector` | `lexical` |
| `SO_AUTH_TOKEN` | Bearer-токен для `/api/*` (loopback-MVP) | пусто |
| `SO_JWT_SECRET` | ключ подписи JWT личных учёток, ≥32 байт | пусто (эфемерный) |
| `SO_REQUIRE_AUTH` | `1` — не стартовать на внешнем адресе без `SO_AUTH_TOKEN` | `0` |
| `SO_COOKIE_SECURE` | `1` — ставить `Secure` на cookie refresh-токена (за прокси с TLS обязательно) | `0` |
| `SO_ADMIN_PASSWORD` | пароль для `create-admin` (разовый, бутстрап) | пусто |
| `SO_STORAGE_BACKEND` | `file` \| `postgres` | `file` |
| `SO_DATABASE_URL` | DSN PostgreSQL при `postgres` | пусто |
| `SO_METRICS_ENABLED` | `1` — включить `/metrics` | `0` |
| `SO_ANALYZE_RATE_LIMIT` | запусков анализа на пользователя за `SO_ANALYZE_RATE_WINDOW_SECONDS` (`0` — без ограничения) | 30 за 60 с |
| `SO_ANALYZE_MAX_CONCURRENCY` | одновременных анализов (`0` — без ограничения); сверх лимита ответ `503` через `SO_ANALYZE_QUEUE_SECONDS` | 4, ожидание 30 с |
| `SO_LLM_MAX_TOKENS` | предел длины ответа модели в одном запросе | 3000 |

Секреты задаются через окружение/секрет-хранилище, не через репозиторий.

## Проверка после развёртывания

```bash
curl http://127.0.0.1:8000/health          # {"status":"ok",...}
curl http://127.0.0.1:8000/metrics        # {"enabled": false} или {"enabled": true, "metrics": {...}}
curl -G http://127.0.0.1:8000/api/v1/search --data-urlencode "q=покушение"
```

и смоук-сценарий выше («Демо-образец» -> «Проанализировать»). Кириллицу в адресе curl
передаёт сырыми байтами, и сервер отвечает 400: запрос кодируется через `--data-urlencode`.

## Структура проекта

Модульный монолит: `src/second_opinion/` — код, `tests/` — проверки,
`docs/` — документация, `data/fixtures/` — нормы и синтетические дела,
`evaluation/` — метрики и датасеты.

Карта модулей с назначением каждого —
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#раскладка-пакета).

## Разработка

Python 3.11 и новее. Дополнительные наборы зависимостей: `pg` — драйвер PostgreSQL и
pgvector, `ocr` — распознавание сканов; всё сразу: `pip install -e ".[dev,pg,ocr]"`.

| Действие | Linux/macOS | Windows |
|---|---|---|
| Запуск приложения | `make dev` | `.venv\Scripts\second-opinion serve` |
| Тесты | `make test` | `.venv\Scripts\pytest` |
| Линтер | `make lint` | `.venv\Scripts\ruff check .` |
| Автоисправление | `make fmt` | `.venv\Scripts\ruff check --fix .` |
| Типы | `make types` | `.venv\Scripts\mypy` |
| Оценка качества | `make eval` | `.venv\Scripts\second-opinion eval` |
| Всё сразу | `make check` | команды выше по очереди |

`make check` — линтер, типы, тесты с покрытием, печать корпуса норм, метрики. Те же проверки
выполняет непрерывная интеграция на двух версиях Python
([.github/workflows/ci.yml](.github/workflows/ci.yml)). Отдельные шаги:

```bash
ruff check .                        # линтер
mypy                                # типы
pytest --cov                        # тесты и порог покрытия 80%
second-opinion norms-seal --check   # если менялся корпус норм
python -m evaluation.run            # если менялись извлечение, поиск или правила
```

Шестнадцать тестов работают против настоящего PostgreSQL и **без переменной окружения
молча уходят в пропуск**: прогон выглядит зелёным, ничего не проверив. Поднять базу:

```bash
docker run -d --name so-test-pg -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=second_opinion_test \
  -p 55432:5432 pgvector/pgvector:pg16

SO_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/second_opinion_test \
  pytest --cov
```

Образ ставит зависимости из `requirements.lock`: версии закреплены, хэши проверяются
(`pip install --require-hashes`). После изменения зависимостей в `pyproject.toml` файл
пересобирается (`pip-compile --extra pg --extra ocr --strip-extras`, затем к каждой строке
добавляются `--hash=sha256:...` для всех файлов этой версии на PyPI); тест
`test_lock_pins_every_runtime_dependency` следит, чтобы у каждой прямой зависимости была
закреплённая версия с хэшами.

Правила проекта, которые нельзя нарушать: цитата факта дословно находится в документе (если
текст нужно изменить, замена сохраняет длину); норма применяется в редакции на юридически
значимую дату; статистика не основание для наказания (запрещённые формулировки проверяет
тест); машинно полученный текст нормы остаётся черновиком, пока его не сверит юрист;
документы не уходят во внешний контур без явного решения оператора. Полный список
инвариантов — раздел 5 [docs/TZ.md](docs/TZ.md). Комментарии `#` в коде не используются,
кроме директив инструментов (`noqa`, `pragma: no cover`, `type: ignore`): пояснения лежат в
докстрингах и в `docs/`. Если правка меняет поведение, документация правится в том же
коммите.

## Оценка качества (evaluation)

```bash
second-opinion eval     # или: make eval
```

Три раздела метрик: извлечение фактов (precision/recall/F1,
`unsupported_claim_rate` -> 0), поиск по источникам (Recall@5, MRR, nDCG@10,
целостность цитирования), временна́я корректность (выбор редакции нормы на
дату). Отчёты — в `evaluation/reports/`.

## Документация

Указатель по документам — [docs/README.md](docs/README.md): что читать,
смотря какая у вас задача.

Самое нужное сразу: [docs/CONCEPT.md](docs/CONCEPT.md) — концепция проекта;
[docs/TZ.md](docs/TZ.md) — требования и статус каждого;
[docs/API.md](docs/API.md) — справочник HTTP API.

## Статус данных (важно)

- Нормы в `data/fixtures/norms/` — сверены юристом по КонсультантПлюс
  03.09.2026 (`verification_status: verified`; синтетическая демо-норма
  ст. 999 — исключение). Это точные изложения со ссылкой на первоисточник,
  а не официальный текст: перед судебной демонстрацией — контрольная
  сверка с КП. Что ещё требует сверки —
  [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md#покрытие-редакционной-истории-и-сверка).
- Дела в `data/fixtures/cases/` — преимущественно синтетические
  (`synthetic: true`) плюс несколько реальных приговоров с `source_url`
  на текст; оба вида явно маркируются, подмена одного другим отклоняется
  моделью.

## Обновление развёртывания

1. Остановить процесс.
2. Обновить код (`git pull`) / пересобрать образ.
3. Прогнать `make test` и `make eval` — пороги метрик должны выполняться.
4. Запустить и проверить `/health` + смоук-сценарий.

## Лицензия

[MIT](LICENSE). Код можно использовать, изменять и распространять при
сохранении указания авторства; гарантии не предоставляются.

Выбор лицензии не отменяет условий конкурса о правах на результат: если
Положение требует иного, лицензия приводится в соответствие с ним.

## Что сделано и что впереди

Требования пронумерованы в [docs/TZ.md](docs/TZ.md); там же, в разделе 7,
очереди работ с указанием, что именно мешает закрыть каждую. Честная карта покрытия —
в [docs/CONCEPT.md](docs/CONCEPT.md).
