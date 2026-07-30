from __future__ import annotations

import re
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
from engine.control import PreparedFileImport
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

pytestmark = pytest.mark.integration
TOKEN = "ui-import-confirm-token"


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


def test_import_preview_requires_confirm(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
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
        api = PostgreSQLUiApi(
            PostgreSQLMembershipAuthority(guarded_runtime_engine),
            guarded_control_engine,
            preview_key=b"i" * 32,
            roots=roots,
            file_import_service_principal_id=scenario.receiver.service_principal_id,
            clock=lambda: NOW,
        )
        client = TestClient(
            create_app(
                authenticator=_Authenticator(scenario, user_id),
                ui_bearer_token=TOKEN,
                ui_api=api,
            )
        )

        preview = client.post(
            "/ui/import/preview",
            content=(f"sourceRef={scenario.source_ref.value}&path=handbook.md"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert preview.status_code == 200
        assert "Actual Fragment preview" in preview.text
        assert "ContextEngine delivers context." in preview.text
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
            content=f"previewToken={preview_token}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert confirmed.status_code == 200
        assert "Import queued" in confirmed.text
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
    finally:
        roots.close()
        migration_engine.dispose()
        delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )
