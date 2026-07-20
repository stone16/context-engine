.PHONY: install build lint typecheck test catalog smoke check

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

smoke:
	uv run pytest -q tests/process

check: build lint typecheck test catalog smoke
