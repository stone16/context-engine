.PHONY: install build lint typecheck test catalog security-gate smoke db-up db-down db-reset integration openapi-generate openapi-check openapi-breaking-check sdk-generate sdk-check sdk-build sdk-test sdk-pack action-typecheck action-build action-test check

install:
	uv sync --frozen
	npm --prefix sdk/typescript ci --ignore-scripts
	npm --prefix action_plane/typescript ci --ignore-scripts

build:
	uv build

lint:
	uv run ruff check .

typecheck:
	uv run mypy
	npm --prefix action_plane/typescript run typecheck

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

openapi-generate:
	uv run python scripts/freeze_openapi.py generate

openapi-check:
	uv run python scripts/freeze_openapi.py check $(if $(OPENAPI_BASELINE_REF),--baseline-ref $(OPENAPI_BASELINE_REF),)

openapi-breaking-check:
	uv run pytest -q tests/unit/test_openapi_v0_snapshot.py

sdk-generate:
	npm --prefix sdk/typescript run generate

sdk-check:
	npm --prefix sdk/typescript run check:generated

sdk-build:
	npm --prefix sdk/typescript run build

sdk-test:
	npm --prefix sdk/typescript test

sdk-pack:
	npm --prefix sdk/typescript run pack:artifact

action-typecheck:
	npm --prefix action_plane/typescript run typecheck

action-build:
	npm --prefix action_plane/typescript run build

action-test: action-build
	npm --prefix action_plane/typescript run test:runtime
	npm --prefix action_plane/typescript run test:package

check: build lint typecheck openapi-check sdk-check sdk-build sdk-test sdk-pack action-build action-test test catalog smoke integration security-gate
