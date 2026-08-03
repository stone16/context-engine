.PHONY: install install-runtime build lint typecheck test catalog third-party-check third-party-artifacts security-gate smoke db-up db-down db-reset integration dogfood-eval eval-v1 eval-v1-execute openapi-generate openapi-check openapi-breaking-check sdk-generate sdk-check sdk-build sdk-test sdk-pack action-typecheck action-build action-test bot-typecheck bot-build bot-test ui-build ui-test check

install:
	uv sync --frozen --extra mcp
	npm --prefix sdk/typescript ci --ignore-scripts
	npm --prefix action_plane/typescript ci --ignore-scripts
	npm --prefix bot_delivery/typescript ci --ignore-scripts

install-runtime:
	uv sync --frozen
	npm --prefix sdk/typescript ci --ignore-scripts
	npm --prefix action_plane/typescript ci --ignore-scripts
	npm --prefix bot_delivery/typescript ci --ignore-scripts

build:
	uv build

lint:
	uv run ruff check .

typecheck: sdk-build action-build
	uv run mypy
	npm --prefix action_plane/typescript run typecheck
	npm --prefix bot_delivery/typescript run typecheck

test: bot-build
	uv run pytest -q tests/unit

catalog:
	uv run pytest -q tests/catalog
	uv run python scripts/validate_security_catalog.py
	$(MAKE) third-party-check

third-party-check:
	uv run python scripts/third_party_governance.py validate
	uv run python scripts/third_party_governance.py generate --check

third-party-artifacts:
	uv run python scripts/third_party_governance.py artifacts

security-gate:
	CONTEXT_ENGINE_DELIBERATE_RED_RUN_PROBE=1 uv run python scripts/run_m0_security_gate.py --output-dir .context-engine/security-gate

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

dogfood-eval:
	uv run context-engine-dogfood-eval run \
		--output .context-engine/dogfood-eval/golden-v0-report.json

eval-v1:
	uv run context-engine-eval report \
		--golden-set "$(GOLDEN_SET)" \
		--lock "$(GOLDEN_LOCK)" \
		--run "$(EVAL_RUN)" \
		$(if $(GOLDEN_LINEAGE_MAP),--lineage-map "$(GOLDEN_LINEAGE_MAP)",) \
		--output .context-engine/eval/golden-v1-report.json \
		--generated-at "$(GENERATED_AT)"

eval-v1-execute:
	uv run context-engine-eval execute \
		--golden-set "$(GOLDEN_SET)" \
		--lock "$(GOLDEN_LOCK)" \
		--judgments "$(EVAL_JUDGMENTS)" \
		$(if $(GOLDEN_LINEAGE_MAP),--lineage-map "$(GOLDEN_LINEAGE_MAP)",) \
		--output .context-engine/eval/golden-v1-report.json \
		--generated-at "$(GENERATED_AT)"

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

bot-typecheck:
	npm --prefix bot_delivery/typescript run typecheck

bot-build: sdk-build action-build
	npm --prefix bot_delivery/typescript run build

bot-test: bot-build
	npm --prefix bot_delivery/typescript run test:runtime
	npm --prefix bot_delivery/typescript run test:package

ui-build:
	uv run python -m py_compile ui/*.py

ui-test: ui-build
	uv run pytest -q \
		tests/unit/test_ui_uses_public_seam_only.py \
		tests/unit/test_source_overview_matches_promoted_release.py \
		tests/unit/test_citation_lineage_resolvable.py \
		tests/unit/test_profile_change_surfaces_reembed.py \
		tests/unit/test_feedback_has_no_publication_authority.py \
		tests/unit/test_fail_closed_renders_refusal.py

check: build lint typecheck openapi-check sdk-check sdk-build sdk-test sdk-pack action-build action-test bot-build bot-test ui-build ui-test test catalog smoke integration security-gate third-party-artifacts
