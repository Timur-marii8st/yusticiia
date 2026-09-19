# Единый набор команд разработчика.
# Windows: те же команды напрямую, см. CONTRIBUTING.md.

.PHONY: install dev test lint fmt types seal eval check

install:
	python -m venv .venv
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

# Полный набор проверок перед коммитом — тот же, что выполняет CI.
# Тесты PostgreSQL без SO_TEST_DATABASE_URL молча уходят в пропуск:
# как поднять базу, написано в CONTRIBUTING.md.
check: lint types test seal eval
