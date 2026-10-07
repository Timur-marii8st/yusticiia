#!/usr/bin/env bash
set -u

MODEL="${SO_LLM_MODEL:-Qwen/Qwen2.5-7B-Instruct}"
TAG="${VLLM_IMAGE_TAG:-v0.31.0-cu129}"
MIN_GPU_MIB="${VLLM_MIN_GPU_MIB:-22000}"
MIN_DISK_GB="${VLLM_MIN_DISK_GB:-50}"
PORT="${VLLM_HOST_PORT:-8001}"
DEEP=0
[ "${1:-}" = "--deep" ] && DEEP=1

fail=0
ok()   { printf 'OK    %s\n' "$1"; }
warn() { printf 'WARN  %s\n' "$1"; }
bad()  { printf 'FAIL  %s\n' "$1"; fail=1; }

version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }

printf 'Модель: %s, образ: vllm/vllm-openai:%s\n\n' "$MODEL" "$TAG"

if [ "$(uname -s)" = "Linux" ]; then
    if [ -r /etc/os-release ]; then
        . /etc/os-release
        ok "ОС: ${PRETTY_NAME:-Linux}"
    else
        ok "ОС: Linux"
    fi
else
    bad "это не Linux ($(uname -s)): образ vllm-openai с CUDA работает только на Linux"
fi

if command -v nvidia-smi >/dev/null 2>&1; then
    gpus="$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>/dev/null)"
    if [ -n "$gpus" ]; then
        best=0
        while IFS=',' read -r name mem driver; do
            name="$(echo "$name" | sed 's/^ *//;s/ *$//')"
            mem="$(echo "$mem" | sed 's/^ *//;s/ *$//')"
            driver="$(echo "$driver" | sed 's/^ *//;s/ *$//')"
            ok "видеокарта: ${name}, ${mem} МиБ, драйвер ${driver}"
            [ "$mem" -gt "$best" ] 2>/dev/null && best="$mem"
        done <<< "$gpus"
        if [ "$best" -ge "$MIN_GPU_MIB" ]; then
            ok "памяти видеокарты хватает для модели 7B в bf16 (порог проверки ${MIN_GPU_MIB} МиБ)"
        else
            warn "в самой большой видеокарте ${best} МиБ: 7B в bf16 не поместится, выберите модель меньше или квантованную"
        fi
        cuda="$(nvidia-smi 2>/dev/null | grep -o 'CUDA Version: [0-9.]*' | head -n1 | awk '{print $3}')"
        need="13.0"
        case "$TAG" in *cu129*) need="12.9" ;; esac
        if [ -n "$cuda" ] && version_ge "$cuda" "$need"; then
            ok "драйвер поддерживает CUDA ${cuda}, образу нужна ${need}"
        elif [ -n "$cuda" ]; then
            warn "драйвер поддерживает CUDA ${cuda}, образу ${TAG} нужна ${need}: обновите драйвер или смените VLLM_IMAGE_TAG"
        fi
    else
        bad "nvidia-smi не видит видеокарт"
    fi
else
    bad "nvidia-smi не найден: не установлен драйвер NVIDIA"
fi

if command -v docker >/dev/null 2>&1; then
    ok "docker: $(docker --version 2>/dev/null)"
    if docker compose version >/dev/null 2>&1; then
        ok "docker compose: $(docker compose version --short 2>/dev/null)"
    else
        bad "нет плагина docker compose"
    fi
    if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
        ok "docker знает рантайм nvidia"
    else
        bad "в docker нет рантайма nvidia: установите nvidia-container-toolkit и выполните nvidia-ctk runtime configure"
    fi
    root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
    free_gb="$(df -Pk "$root" 2>/dev/null | awk 'NR==2 {print int($4 / 1024 / 1024)}')"
    if [ -n "$free_gb" ] && [ "$free_gb" -ge "$MIN_DISK_GB" ]; then
        ok "свободно ${free_gb} ГБ в ${root} (образ около 11 ГБ, веса модели около 15 ГБ)"
    else
        warn "свободно ${free_gb:-?} ГБ в ${root}, нужно не меньше ${MIN_DISK_GB}"
    fi
else
    bad "docker не найден"
fi

if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${PORT} "; then
    warn "порт ${PORT} уже занят"
else
    ok "порт ${PORT} свободен"
fi

key="${SO_OPENAI_API_KEY:-}"
if [ -z "$key" ] || [ "$key" = "local" ]; then
    warn "SO_OPENAI_API_KEY пуст или равен local: задайте длинный ключ (openssl rand -hex 24)"
else
    ok "SO_OPENAI_API_KEY задан"
fi

if [ "$DEEP" = 1 ] && command -v docker >/dev/null 2>&1; then
    if docker run --rm --gpus all ubuntu:24.04 nvidia-smi -L >/dev/null 2>&1; then
        ok "контейнер видит видеокарту"
    else
        bad "контейнер не видит видеокарту (docker run --gpus all ... nvidia-smi завершился ошибкой)"
    fi
fi

printf '\n'
if [ "$fail" = 0 ]; then
    echo "Готово к запуску: docker compose --profile vllm up -d"
else
    echo "Есть FAIL: сначала исправьте их, подробности в docs/DEPLOYMENT_VLLM.md"
fi
exit "$fail"
