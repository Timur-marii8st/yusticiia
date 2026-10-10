# Запуск языковой модели (vLLM) на сервере Ubuntu

Модель нужна для необязательного шага: она дополняет факты, найденные
паттернами. По решению НТ-02 ([TZ.md](TZ.md), раздел 2.10) работает только
своя модель на той же машине или в том же периметре, а не облачный сервис.
Без неё система полностью работоспособна (`SO_LLM_PROVIDER=mock`).

## Что проверено и что нет

Проверено: файл `docker-compose.yml` разбирается для профилей `vllm` и
`vllm-cpu`; у образов `vllm/vllm-openai` и `vllm/vllm-openai-cpu` точка
входа `vllm serve`, у обычного тега CUDA 13.0, у тега `-cu129` CUDA 12.9
(метаданные реестра Docker Hub, 06.10.2026); названия флагов и переменных
сверены с документацией vLLM; протокол запросов проходит на Ollama
(`scripts/check_llm_contract.py`).

Не проверено: запуск на видеокарте, скорость, расход памяти, работа
образа `v0.31.0-cu129` с конкретным драйвером. Это нужно сделать на
сервере, шаг 6.

## Что нужно

| Что | Требование |
|---|---|
| ОС | Ubuntu 22.04 или 24.04, x86_64 |
| Видеокарта | NVIDIA, не меньше 24 ГБ памяти для `Qwen2.5-7B-Instruct` в bf16. Это оценка: веса около 15 ГБ, остальное кэш контекста |
| Драйвер NVIDIA | поддерживает CUDA 12.9 и выше (драйвер 575 и новее) |
| Docker | Docker Engine с плагином `docker compose` |
| nvidia-container-toolkit | настроен как рантайм Docker |
| Диск | не меньше 50 ГБ свободно: образ около 11 ГБ, веса около 15 ГБ |
| Сеть | при первом запуске доступ к Docker Hub и huggingface.co; далее можно без интернета (раздел «Закрытый контур») |

## Шаг 1. Драйвер NVIDIA

```bash
nvidia-smi
```

В таблице должна быть видеокарта, в правом верхнем углу `CUDA Version`.
Если команды нет:

```bash
sudo ubuntu-drivers install
sudo reboot
```

По значению `CUDA Version` выбирается образ:

| `CUDA Version` | `VLLM_IMAGE_TAG` |
|---|---|
| 13.0 и выше | `v0.31.0` |
| 12.9 | `v0.31.0-cu129` (значение по умолчанию) |
| ниже 12.9 | обновить драйвер |

Метаданные образов допускают и старые ветки драйвера (535-570) на картах
для дата-центров через пакеты совместимости; на практике это не
проверялось.

## Шаг 2. Docker Engine

Установка по официальной инструкции для Ubuntu:
<https://docs.docker.com/engine/install/ubuntu/> (репозиторий Docker и пакеты
`docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`,
`docker-compose-plugin`). Затем:

```bash
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

После `usermod` нужно выйти и войти заново.

## Шаг 3. NVIDIA Container Toolkit

По инструкции NVIDIA:
<https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>.

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Проверка, что контейнер видит видеокарту:

```bash
docker run --rm --gpus all ubuntu:24.04 nvidia-smi
```

Должна вывестись та же таблица, что и на хосте.

## Шаг 4. Код и настройки

```bash
git clone <адрес репозитория> second-opinion
cd second-opinion
cp .env.example .env
```

В `.env` нужно изменить существующие строки, а не добавлять вторые: при
повторе действует последнее значение, а в конце файла стоят пустые
`SO_OPENAI_BASE_URL=`, `SO_OPENAI_API_KEY=`, `SO_LLM_MODEL=`.

```text
SO_LLM_PROVIDER=openai_compatible
SO_OPENAI_BASE_URL=http://vllm:8000/v1
SO_OPENAI_API_KEY=<вывод команды: openssl rand -hex 24>
SO_LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
VLLM_IMAGE_TAG=v0.31.0-cu129
```

`SO_OPENAI_API_KEY` один на обе стороны: им vLLM закрывает адреса `/v1`
(`--api-key`), им же приложение подписывает запросы.

Проверка сервера перед запуском (ничего не меняет):

```bash
set -a; . ./.env; set +a
scripts/vllm_preflight.sh --deep
```

Строки `FAIL` нужно исправить до запуска, `WARN` прочитать. С `--deep`
скрипт сам запускает контейнер с видеокартой.

## Шаг 5. Запуск

```bash
docker compose --profile vllm up -d
```

Сначала скачается образ (около 11 ГБ), затем при первом старте vLLM
скачает веса модели (около 15 ГБ) в том `hf-cache`. Ход загрузки:

```bash
docker compose logs -f vllm
```

Сервер готов, когда в журнале появилась строка `Application startup
complete`, а `docker compose ps` показывает `healthy`. Пока идёт загрузка,
статус `starting`: это нормально, проверка здоровья ждёт до 30 минут.

## Шаг 6. Проверка

Сервер отвечает (адрес `/health` ключа не требует):

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/health
curl -s -H "Authorization: Bearer $SO_OPENAI_API_KEY" http://127.0.0.1:8001/v1/models
```

Первая команда должна вернуть `200`, вторая назвать модель
`Qwen/Qwen2.5-7B-Instruct`.

Контракт с адаптером приложения и замер на золотом наборе выполняются на
хосте: в образ приложения каталоги `scripts/` и `evaluation/` не входят.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
export SO_OPENAI_BASE_URL=http://127.0.0.1:8001/v1
.venv/bin/python scripts/check_llm_contract.py
.venv/bin/python -m evaluation.llm_extraction_probe
```

Первый скрипт выводит три шага: список моделей, обычный запрос, запрос
через `OpenAICompatibleProvider`. Все три должны пройти, код возврата 0.
Второй сравнивает паттерны и паттерны с моделью на 15 образцах.
Эталон на Ollama и `qwen2.5:7b-instruct` (06.10.2026): обе оценки 1.000,
около 30 секунд на документ. На vLLM с видеокартой цифры не измерялись,
время должно быть заметно меньше; если оценка с моделью ниже, чем у одних
паттернов, это нужно разобрать до включения модели в работу.

Сквозная проверка: открыть `http://127.0.0.1:8000`, нажать «Демо-образец»,
«Проанализировать». В `data/store/audit.jsonl` должна появиться запись
`llm_fact_extraction` с `"found": true`. Если там `"found": false`,
приложение продолжает работу на одних паттернах, причину покажет
`docker compose logs app`.

## Параметры

Все задаются в `.env`.

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `SO_LLM_MODEL` | `Qwen/Qwen2.5-7B-Instruct` | модель Hugging Face. Другая модель требует нового замера `evaluation.llm_extraction_probe` |
| `VLLM_IMAGE_TAG` | `v0.31.0-cu129` | тег образа `vllm/vllm-openai`, версия закреплена намеренно, `latest` не используется |
| `VLLM_MAX_MODEL_LEN` | `16384` | длина контекста. Самый длинный документ (20 000 символов) вместе с промптом занимает около 8 300 токенов (замер на токенизаторе Qwen2.5), остальное запас под ответ |
| `VLLM_GPU_MEMORY_UTILIZATION` | `0.90` | доля памяти видеокарты (в самом vLLM по умолчанию 0.92) |
| `VLLM_GENERATION_CONFIG` | `vllm` | `vllm`: не применять `generation_config.json` модели. Иначе подставятся её значения по умолчанию (температура, top_p, штраф за повторы), а извлечению нужны дословные цитаты |
| `VLLM_LOAD_FORMAT` | `safetensors` | формат весов: `safetensors` не исполняет код при загрузке (в отличие от `pt` на pickle); модель без файлов `safetensors` не запустится, это намеренно. `--no-trust-remote-code` задан всегда |
| `HF_HUB_OFFLINE` | `0` | `1` запрещает обращаться к Hugging Face, веса берутся из тома |
| `HF_ENDPOINT` | `https://huggingface.co` | зеркало Hugging Face, если основной адрес недоступен |

Сбор статистики vLLM отключён (`VLLM_NO_USAGE_STATS`, `DO_NOT_TRACK`).
Кэш префиксов vLLM остаётся включённым (поведение по умолчанию): запросы разных
судей идут через один сервер, и кэш сопоставляет повторяющиеся начала запросов.
Для закрытого контура одного суда это принято; если модель обслуживает
несколько изолированных групп, добавьте в `command` флаг `--no-enable-prefix-caching`.
Порт публикуется только на `127.0.0.1:8001`; менять этот адрес на внешний
нельзя без обратного прокси и TLS ([SECURITY.md](SECURITY.md)).

## Закрытый контур без интернета

Образ и веса переносятся один раз с машины с доступом в сеть.

На машине с интернетом:

```bash
docker pull vllm/vllm-openai:v0.31.0-cu129
docker save vllm/vllm-openai:v0.31.0-cu129 | gzip > vllm-image.tgz
docker compose --profile vllm up -d vllm
docker compose logs -f vllm
docker volume ls
docker run --rm -v <проект>_hf-cache:/data -v "$PWD":/backup ubuntu:24.04 tar czf /backup/hf-cache.tgz -C /data .
```

Имя тома строится из имени каталога проекта (`<проект>_hf-cache`), точное
имя показывает `docker volume ls`. На сервере без интернета:

```bash
gunzip -c vllm-image.tgz | docker load
docker volume create <проект>_hf-cache
docker run --rm -v <проект>_hf-cache:/data -v "$PWD":/backup ubuntu:24.04 tar xzf /backup/hf-cache.tgz -C /data
```

В `.env` сервера поставить `HF_HUB_OFFLINE=1` и запустить как в шаге 5.
Образы `ubuntu:24.04` и `app` на сервере тоже должны быть загружены заранее.

## Обслуживание

```bash
docker compose --profile vllm ps
docker compose logs --tail 100 vllm
docker compose --profile vllm stop vllm
docker compose --profile vllm up -d --force-recreate
nvidia-smi
```

Контейнеры стартуют после перезагрузки сами (`restart: unless-stopped`
при включённом сервисе `docker`). Смена версии: изменить `VLLM_IMAGE_TAG`,
выполнить `docker compose --profile vllm pull` и `up -d`, затем повторить
шаг 6.

## Если не запускается

| Симптом | Причина и действие |
|---|---|
| `could not select device driver "" with capabilities: [[gpu]]` | Docker не знает про видеокарту: выполнить `sudo nvidia-ctk runtime configure --runtime=docker` и `sudo systemctl restart docker` |
| Контейнер завершается с сообщением про память кэша или про `max seq len` больше возможного | уменьшить `VLLM_MAX_MODEL_LEN` (не ниже 12288 для документа в 20 000 символов) или поднять `VLLM_GPU_MEMORY_UTILIZATION` до 0.95 |
| Контейнер завершается с нехваткой памяти при загрузке весов | видеокарта меньше 24 ГБ: взять модель меньше, например `Qwen/Qwen2.5-3B-Instruct`, и повторить замер |
| Статус `starting` дольше 30 минут | веса не скачались: проверить доступ к `huggingface.co` из контейнера, задать `HF_ENDPOINT` с зеркалом; загрузка продолжается с места обрыва |
| Образ не запускается с ошибкой про версию CUDA | драйвер старее, чем нужно образу: обновить драйвер или выбрать другой `VLLM_IMAGE_TAG` (шаг 1) |
| Ответ 401 от vLLM | ключ в `app` и в `vllm` разный, потому что `.env` менялся без пересоздания: `docker compose --profile vllm up -d --force-recreate` |
| В `audit.jsonl` `"found": false` | модель недоступна или её ответ не прошёл проверку схемы; приложение продолжает на паттернах, детали в `docker compose logs app` |

## Без видеокарты

Профиль `vllm-cpu` запускает тот же сервер на процессоре и маленькой
модели. Он нужен только для проверки протокола, не для работы: документ
обрабатывается минутами.

```bash
VLLM_CPU_TAG=v0.31.0-x86_64 docker compose --profile vllm-cpu up -d
```

На Apple Silicon тег `v0.31.0-arm64` (значение по умолчанию). Загрузка
этого образа на машине разработки 05-06.10.2026 не завершилась, поэтому
профиль живьём не проверялся.
