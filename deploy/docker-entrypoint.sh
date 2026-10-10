#!/bin/sh
set -e

DATA_DIR="${SO_DATA_DIR:-/app/data}"
IMAGE_FIXTURES="${SO_IMAGE_FIXTURES:-/opt/fixtures}"

if [ ! -d "$DATA_DIR/fixtures/norms" ]; then
    echo "В $DATA_DIR/fixtures нет норм: копирую нормы и дела из образа" >&2
    mkdir -p "$DATA_DIR"
    cp -R "$IMAGE_FIXTURES" "$DATA_DIR/fixtures"
fi

exec "$@"
