PYTHON ?= python3

.PHONY: install dev test lint fmt types seal eval check

install:
	$(PYTHON) -m venv .venv
	.venv/bin/pip install -e ".[dev]"

dev:
	.venv/bin/second-opinion serve

test:
	.venv/bin/pytest --cov

lint:
	.venv/bin/ruff check .

fmt:
	.venv/bin/ruff check --fix .

types:
	.venv/bin/mypy

seal:
	.venv/bin/second-opinion norms-seal --check

eval:
	.venv/bin/second-opinion eval

check: lint types test seal eval
