# Единый developer interface (Windows: используйте команды напрямую, см. README)

.PHONY: install dev test lint fmt eval seed

install:
	python -m venv .venv
	.venv/bin/pip install -e ".[dev]"

dev:
	.venv/bin/second-opinion serve

test:
	.venv/bin/pytest

lint:
	.venv/bin/ruff check .

fmt:
	.venv/bin/ruff check --fix .

eval:
	.venv/bin/second-opinion eval
