.PHONY: install build lint typecheck test catalog security-gate smoke db-up db-down db-reset integration check

install:
	uv sync --frozen

build:
	uv build

lint:
	uv run ruff check .

typecheck:
	uv run mypy

test:
	uv run pytest -q tests/unit

catalog:
	uv run pytest -q tests/catalog
	uv run python scripts/validate_security_catalog.py

security-gate:
	uv run python scripts/run_m0_security_gate.py --output-dir .context-engine/security-gate

smoke:
	uv run pytest -q tests/process

db-up:
	./scripts/database_harness.sh up

db-down:
	./scripts/database_harness.sh down

db-reset:
	./scripts/database_harness.sh reset

integration:
	./scripts/database_harness.sh integration

check: build lint typecheck test catalog smoke integration security-gate
