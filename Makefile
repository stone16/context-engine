.PHONY: install build lint typecheck test smoke check

install:
	uv sync --frozen

build:
	uv build

lint:
	uv run ruff check .

typecheck:
	uv run mypy

test:
	uv run pytest -q

smoke:
	uv run pytest -q tests/process

check: build lint typecheck test smoke
