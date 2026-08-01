from __future__ import annotations

import html
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from adapters.file_source import FileReadLimits, FileRootRegistry
from adapters.http.app import create_app
from adapters.http.authentication import VerifiedAuthenticationContext
from adapters.http.ui_api import PostgreSQLUiApi
from adapters.parsers.markdown import compile_markdown
from engine.control import ControlOperation, PreparedFileImport
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLMembershipAuthority,
    PostgreSQLWorkerLeaseIssuer,
    create_database_engine,
)
from engine.supply import MarkdownCompilerConfig, ParsedDocument
from tests.support.file_imports import (
    NOW,
    FileImportScenario,
    delete_file_import_scenario,
    prepare_file_import_scenario,
    run_file_import,
)
from tests.support.ui import authenticate_ui, ui_control_authority

pytestmark = pytest.mark.integration
TOKEN = "ui-import-confirm-token"
CONTROL_TOKEN = "ui-import-control-token"


class _Authenticator:
    def __init__(self, scenario: FileImportScenario, user_id: UUID) -> None:
        self._scenario = scenario
        self._user_id = user_id

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        assert opaque_credential == TOKEN
        return VerifiedAuthenticationContext(
            organization_ref=str(self._scenario.organization_id),
            user_ref=str(self._user_id),
            principal_ref="principal:file-reader",
            membership_ref=str(self._scenario.membership_id),
            membership_version=1,
            agent_version_ref="agent:ui-import",
            authenticated_application_ref="application:ui-import",
            authentication_binding_ref="binding:ui-import",
        )


@contextmanager
def _ui_import_scenario(
    *,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    payload: bytes,
) -> Iterator[tuple[FileImportScenario, TestClient, Engine]]:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=payload,
        issue_lease=False,
    )
    migration_engine = create_database_engine(migration_configuration)
    roots = FileRootRegistry(
        {scenario.root_ref: scenario.root},
        limits=FileReadLimits(max_file_bytes=4096),
    )
    try:
        with migration_engine.connect() as connection:
            user_id = connection.execute(
                text(
                    "SELECT user_id FROM membership "
                    "WHERE organization_id = :organization_id "
                    "AND membership_id = :membership_id"
                ),
                {
                    "organization_id": scenario.organization_id,
                    "membership_id": scenario.membership_id,
                },
            ).scalar_one()
        control_authority, control_gate = ui_control_authority(
            organization_id=scenario.organization_id,
            credential=CONTROL_TOKEN,
            operations=frozenset({ControlOperation.IMPORT_FILE}),
            clock=lambda: NOW,
        )
        client = TestClient(
            create_app(
                authenticator=_Authenticator(scenario, user_id),
                ui_bearer_token=TOKEN,
                ui_control_authority=control_authority,
                ui_api=PostgreSQLUiApi(
                    PostgreSQLMembershipAuthority(guarded_runtime_engine),
                    guarded_control_engine,
                    preview_key=b"i" * 32,
                    control_gate=control_gate,
                    roots=roots,
                    file_import_service_principal_id=(
                        scenario.receiver.service_principal_id
                    ),
                    clock=lambda: NOW,
                ),
            )
        )
        authenticate_ui(client, TOKEN)
        yield scenario, client, migration_engine
    finally:
        roots.close()
        migration_engine.dispose()
        delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )


@pytest.mark.parametrize(
    "payload",
    [
        b"# Handbook\n\nRead [the private note](private-runbook.md).\n",
        b"# Handbook\n\nRead [[private-runbook]].\n",
        b"# Handbook\n\nEmbed ![[private-runbook]].\n",
        b"# Handbook\n\nRead <https://private.invalid/runbook>.\n",
        b"# Handbook\n\nFirst paragraph.\n\nRead [[private-runbook]].\n",
        b"# Handbook\n\n- Read [[private-runbook]].\n",
        b"# Handbook\n\nOnly *emphasis*.\n",
        b"# Handbook\n\nOnly `inline code`.\n",
        b"# Handbook\n\nOnly ~~strikethrough~~.\n",
        b"# Handbook\n\nOnly *emphasis*.\n\n---\n",
        b"*Emphasized heading*\n---\n",
    ],
)
def test_v3_only_import_preview_hands_off_to_the_leased_scan_path(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    payload: bytes,
) -> None:
    with _ui_import_scenario(
        tmp_path=tmp_path,
        migration_configuration=migration_configuration,
        guarded_control_engine=guarded_control_engine,
        guarded_runtime_engine=guarded_runtime_engine,
        payload=payload,
    ) as (scenario, client, migration_engine):
        with migration_engine.connect() as connection:
            job_count_before = connection.execute(
                text(
                    "SELECT count(*) FROM file_import_job "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": scenario.organization_id},
            ).scalar_one()
        response = client.post(
            "/ui/import/preview",
            content=(
                f"sourceRef={scenario.source_ref.value}&path=handbook.md&"
                f"controlCredential={CONTROL_TOKEN}"
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert response.status_code == 200
        assert "Rich Markdown requires the leased scan path" in response.text
        assert str(scenario.source_ref.value) in response.text
        source_arguments = (
            '--organization-id "$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID" '
            f'--source-ref "{scenario.source_ref.value}"'
        )
        rendered_commands = tuple(
            html.unescape(command)
            for command in re.findall(
                r'<div class="block-body"><code>([^<]+)</code></div>',
                response.text,
            )
        )
        assert rendered_commands == (
            "uv run context-engine-control activate-change-feed "
            + source_arguments,
            "uv run context-engine-control activate-delete-observations "
            + source_arguments,
            "uv run context-engine-control scan " + source_arguments,
            "uv run context-engine-worker --dispatch-file-once",
        )
        assert "until it reports" in response.text
        assert "no_work" in response.text
        assert "previewToken" not in response.text
        assert "private-runbook" not in response.text
        assert CONTROL_TOKEN not in response.text
        with migration_engine.connect() as connection:
            job_count = connection.execute(
                text(
                    "SELECT count(*) FROM file_import_job "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": scenario.organization_id},
            ).scalar_one()
            published_count = connection.execute(
                text(
                    "SELECT count(*) FROM context_resource "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": scenario.organization_id},
            ).scalar_one()
        assert job_count == job_count_before
        assert published_count == 0


@pytest.mark.parametrize(
    "payload",
    [
        b"# Handbook\n\n[[private-runbook]]\xffprivate malformed body\n",
        b"# Handbook\n\nOnly `private malformed body.\n",
        b"# Handbook\n\nOnly ~~private malformed body.\n",
        b"# Handbook\n\n*Accepted* plus ~~private malformed body.\n",
        b"# Handbook\n\n`Accepted` plus ~~private malformed body.\n",
        b"# Handbook\n\n~~Accepted~~ plus `private malformed body.\n",
        b"# Handbook\n\n[Accepted](note.md) plus ~~private malformed body.\n",
        b"# Handbook\n\n[[Accepted]] plus `private malformed body.\n",
        b"# Handbook\n\n- [Accepted](note.md) plus `private malformed body.\n",
        b"# Handbook\n\n> [[Accepted]] plus `private malformed body.\n",
        b"# Handbook\n\n## [Accepted](note.md) plus `private malformed body.\n",
        (
            b"# Handbook\n\n[Accepted](note.md)\n\n```"
            + b"x" * 65
            + b"\nbody\n```\n"
        ),
        b"# Handbook\n\n[Accepted](note.md)\n\n```text\n\n```\n",
        b"# Handbook\n\n*Accepted*\n\n<private malformed body\n",
        b"# Handbook\n\n*Accepted*\n\n<div>unclosed\n",
    ],
)
def test_malformed_import_refusal_stays_content_free_without_scan_handoff(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    payload: bytes,
) -> None:
    with _ui_import_scenario(
        tmp_path=tmp_path,
        migration_configuration=migration_configuration,
        guarded_control_engine=guarded_control_engine,
        guarded_runtime_engine=guarded_runtime_engine,
        payload=payload,
    ) as (scenario, client, _migration_engine):
        response = client.post(
            "/ui/import/preview",
            content=(
                f"sourceRef={scenario.source_ref.value}&path=handbook.md&"
                f"controlCredential={CONTROL_TOKEN}"
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert response.status_code == 503
        assert "Request refused" in response.text
        assert "provider_unavailable" in response.text
        assert "private malformed body" not in response.text
        assert "context-engine-control scan" not in response.text
        assert "previewToken" not in response.text
        assert CONTROL_TOKEN not in response.text


def test_import_preview_requires_confirm(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    with _ui_import_scenario(
        tmp_path=tmp_path,
        migration_configuration=migration_configuration,
        guarded_control_engine=guarded_control_engine,
        guarded_runtime_engine=guarded_runtime_engine,
        payload=b"# Handbook\n\nContextEngine delivers context.\n",
    ) as (scenario, client, migration_engine):
        preview = client.post(
            "/ui/import/preview",
            content=(
                f"sourceRef={scenario.source_ref.value}&path=handbook.md&"
                f"controlCredential={CONTROL_TOKEN}"
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert preview.status_code == 200
        assert "Actual Fragment preview" in preview.text
        assert "ContextEngine delivers context." in preview.text
        assert CONTROL_TOKEN not in preview.text
        match = re.search(
            r'name="previewToken" value="([A-Za-z0-9_.-]+)"',
            preview.text,
        )
        assert match is not None
        preview_token = match.group(1)

        cancelled = client.get("/ui/import")
        assert cancelled.status_code == 200
        with migration_engine.connect() as connection:
            before_confirm = connection.execute(
                text(
                    "SELECT count(*) FROM file_import_job "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": scenario.organization_id},
            ).scalar_one()
            published_before_confirm = connection.execute(
                text(
                    "SELECT count(*) FROM context_resource "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": scenario.organization_id},
            ).scalar_one()
        assert before_confirm == 1
        assert published_before_confirm == 0

        confirmed = client.post(
            "/ui/import/confirm",
            content=(
                f"previewToken={preview_token}&controlCredential={CONTROL_TOKEN}"
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert confirmed.status_code == 200
        assert "Import queued" in confirmed.text
        assert CONTROL_TOKEN not in confirmed.text
        job_match = re.search(r"Exact job <code>([0-9a-f-]+)</code>", confirmed.text)
        assert job_match is not None
        job_id = job_match.group(1)

        with migration_engine.connect() as connection:
            exact = connection.execute(
                text(
                    """
                    SELECT acquisition.expected_content_sha256,
                           acquisition.expected_content_length,
                           acquisition.expected_fragment_digest,
                           acquisition.compiler_config_version,
                           job.service_principal_id
                    FROM file_import_job AS job
                    JOIN file_acquisition AS acquisition
                      ON acquisition.organization_id = job.organization_id
                     AND acquisition.acquisition_id = job.acquisition_id
                    WHERE job.organization_id = :organization_id
                      AND job.job_id = :job_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "job_id": job_id,
                },
            ).one()
        assert exact.expected_content_sha256 is not None
        assert exact.expected_fragment_digest is not None
        assert exact.compiler_config_version == "markdown-config-v1"
        prepared = PreparedFileImport(
            organization_id=scenario.organization_id,
            job_id=(exact_job_id := UUID(job_id)),
            source_ref=scenario.source_ref,
            service_principal_id=exact.service_principal_id,
        )
        token = PostgreSQLWorkerLeaseIssuer(
            guarded_control_engine,
            scenario.codec,
            lease_ttl_seconds=300,
        ).issue_file_import_lease(prepared)
        published = run_file_import(
            scenario,
            prepared,
            token,
            guarded_worker_engine,
            config_version="markdown-config-v3",
        )
        expected = compile_markdown(
            (scenario.root / "handbook.md").read_bytes(),
            MarkdownCompilerConfig("markdown-config-v1"),
        )
        assert type(expected) is ParsedDocument
        assert exact_job_id == prepared.job_id
        assert tuple(
            candidate.fragment_ref for candidate in published.candidate_refs
        ) == tuple(fragment.fragment_ref for fragment in expected.fragments)
