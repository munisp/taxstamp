SHELL := /bin/bash
PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: venv install fmt lint type test test-unit test-integration test-e2e audit-deps security package migrate run worker evidence clean

venv:
	python3 -m venv .venv

install: venv
	$(PIP) install --require-virtualenv -r requirements-dev.txt
	$(PIP) install --require-virtualenv -e . --no-deps

fmt:
	.venv/bin/ruff format src tests scripts migrations

lint:
	.venv/bin/ruff format --check src tests scripts migrations
	.venv/bin/ruff check src tests scripts migrations

type:
	.venv/bin/mypy

security:
	.venv/bin/bandit -q -c pyproject.toml -r src

audit-deps:
	.venv/bin/pip-audit -r requirements.txt --strict

package:
	rm -rf /tmp/taxstamp-package-dist
	.venv/bin/python -m build --wheel --outdir /tmp/taxstamp-package-dist

test-unit:
	.venv/bin/pytest -m unit -q

test-integration:
	.venv/bin/pytest -m "integration or concurrency" -q

test-e2e:
	.venv/bin/pytest -m "e2e or fault" -q

test:
	.venv/bin/pytest -q

migrate:
	.venv/bin/alembic upgrade head

run:
	.venv/bin/uvicorn taxstamp.api.app:create_app --factory --host 0.0.0.0 --port 8080

worker:
	$(PY) -m taxstamp.worker.main

evidence:
	bash scripts/run_assurance.sh

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache evidence
