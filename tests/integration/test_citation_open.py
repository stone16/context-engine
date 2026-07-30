from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from time import sleep
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLCitationOpenRetentionPort,
    create_database_engine,
)
from engine.persistence.file_imports import PublishedFileImport
from engine.persistence.membership_context import (
    MembershipIdentity,
    PostgreSQLMembershipAuthority,
)
from engine.runtime.citation import (
    CITATION_OPEN_DIGEST_PROFILE,
    CitationLocatorNotAvailable,
    CitationOpenIssue,
    CitationOpenProfile,
    CitationOpenRedemption,
    CitationOpenRetention,
    issue_citation_open_ref,
    redeem_citation_open_ref,
)
from engine.runtime.contracts import CitationOpenRef
from tests.support.file_imports import (
    FileImportScenario as _FileImportScenario,
)
from tests.support.file_imports import (
    delete_file_import_scenario as _delete_file_import_scenario,
)
from tests.support.file_imports import (
    prepare_file_import_scenario as _prepare_file_import_scenario,
)
from tests.support.file_imports import (
    run_file_import as _run_file_import,
)

pytestmark = pytest.mark.integration
REFERENCE = "cor_" + "7" * 64


@pytest.fixture
def citation_file_scenario(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> Iterator[tuple[_FileImportScenario, PublishedFileImport, UUID, Engine]]:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    published = _run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            user_id = connection.execute(
                text(
                    "SELECT user_id FROM membership "
                    "WHERE organization_id = :org AND membership_id = :membership"
                ),
                {
                    "org": scenario.organization_id,
                    "membership": scenario.membership_id,
                },
            ).scalar_one()
        yield scenario, published, user_id, migration_engine
    finally:
        with migration_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM citation_open_locator WHERE organization_id = :org"),
                {"org": scenario.organization_id},
            )
        migration_engine.dispose()
        _delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )


def _identity(
    scenario: _FileImportScenario,
    user_id: UUID,
    *,
    request_id: str,
    checked_at: datetime,
) -> MembershipIdentity:
    return MembershipIdentity(
        organization_id=scenario.organization_id,
        user_id=user_id,
        membership_id=scenario.membership_id,
        membership_version=1,
        principal_ref="principal:file-tracer",
        request_id=request_id,
        authentication_binding_ref="binding:file-tracer",
        checked_at=checked_at,
    )


@pytest.mark.security_evidence(id="PG-CITATION-AUTH-010", layer="postgres")
def test_citation_locator_is_digest_only_multi_use_and_function_only(
    citation_file_scenario: tuple[
        _FileImportScenario, PublishedFileImport, UUID, Engine
    ],
    guarded_runtime_engine: Engine,
) -> None:
    scenario, published, user_id, migration_engine = citation_file_scenario
    candidate = published.candidate_ref
    now = datetime.now(UTC)
    authority = PostgreSQLMembershipAuthority(guarded_runtime_engine)
    profile = CitationOpenProfile(
        profile_ref="private-citation-open-v1",
        retention_policy_ref="citation-locator-retention-v1",
        maximum_ttl=timedelta(minutes=10),
        retention_period=timedelta(days=30),
    )
    issue = CitationOpenIssue(
        organization_id=scenario.organization_id,
        package_ref="pkg_" + "1" * 32,
        evidence_ref="ev_" + "2" * 64,
        resource_ref=candidate.resource_ref,
        revision_id=UUID(candidate.revision_ref),
        fragment_ref=candidate.fragment_ref,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )

    with authority.current_user_actor(
        _identity(scenario, user_id, request_id="citation-issue", checked_at=now)
    ) as verification:
        assert verification.citation_open_session is not None
        issued = issue_citation_open_ref(
            verification.citation_open_session,
            issue,
            profile=profile,
            reference_factory=lambda: REFERENCE,
        )

    with authority.current_user_actor(
        _identity(
            scenario,
            user_id,
            request_id="citation-open-1",
            checked_at=now + timedelta(seconds=1),
        )
    ) as verification:
        assert verification.citation_open_session is not None
        first = redeem_citation_open_ref(
            verification.citation_open_session,
            CitationOpenRedemption(
                citation_open_ref=issued,
                organization_id=scenario.organization_id,
                opened_at=now + timedelta(seconds=1),
            ),
        )
    with authority.current_user_actor(
        _identity(
            scenario,
            user_id,
            request_id="citation-open-2",
            checked_at=now + timedelta(seconds=2),
        )
    ) as verification:
        assert verification.citation_open_session is not None
        second = redeem_citation_open_ref(
            verification.citation_open_session,
            CitationOpenRedemption(
                citation_open_ref=issued,
                organization_id=scenario.organization_id,
                opened_at=now + timedelta(seconds=2),
            ),
        )

    assert first == second
    assert first.candidate_ref == candidate
    assert first.lineage.package_ref == issue.package_ref
    assert first.lineage.evidence_ref == issue.evidence_ref

    with migration_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT locator_digest, digest_profile, package_ref, evidence_ref, "
                "resource_ref, revision_id, fragment_ref, issued_at, expires_at, "
                "profile_ref, retention_policy_ref, retain_until "
                "FROM citation_open_locator WHERE organization_id = :org"
            ),
            {"org": scenario.organization_id},
        ).one()
        columns = {
            str(item.name)
            for item in connection.execute(
                text(
                    "SELECT column_name AS name FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "AND table_name = 'citation_open_locator'"
                )
            )
        }
    assert bytes(row.locator_digest) == sha256(REFERENCE.encode()).digest()
    assert row.digest_profile == CITATION_OPEN_DIGEST_PROFILE
    assert REFERENCE not in repr(row)
    prior_authorization_columns = {
        "principal_ref",
        "membership_id",
        "audience_digest",
        "purpose",
        "decision_ref",
    }
    assert prior_authorization_columns.isdisjoint(columns)
    assert "first_redeemed_at" not in columns
    assert "consumed_at" not in columns
    with (
        guarded_runtime_engine.connect() as connection,
        pytest.raises(ProgrammingError),
    ):
        connection.execute(text("SELECT * FROM citation_open_locator"))


def test_citation_locator_lineage_is_retained_then_cleaned_by_operator_clock(
    citation_file_scenario: tuple[
        _FileImportScenario, PublishedFileImport, UUID, Engine
    ],
    guarded_runtime_engine: Engine,
    guarded_operator_engine: Engine,
) -> None:
    scenario, published, user_id, migration_engine = citation_file_scenario
    candidate = published.candidate_ref
    now = datetime.now(UTC)
    authority = PostgreSQLMembershipAuthority(guarded_runtime_engine)
    with authority.current_user_actor(
        _identity(scenario, user_id, request_id="citation-retained", checked_at=now)
    ) as verification:
        assert verification.citation_open_session is not None
        current_ref = issue_citation_open_ref(
            verification.citation_open_session,
            CitationOpenIssue(
                organization_id=scenario.organization_id,
                package_ref="pkg_" + "5" * 32,
                evidence_ref="ev_" + "6" * 64,
                resource_ref=candidate.resource_ref,
                revision_id=UUID(candidate.revision_ref),
                fragment_ref=candidate.fragment_ref,
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
            ),
            profile=CitationOpenProfile(
                profile_ref="private-citation-open-v1",
                retention_policy_ref="citation-locator-retention-v1",
                maximum_ttl=timedelta(minutes=10),
                retention_period=timedelta(days=30),
            ),
            reference_factory=lambda: "cor_" + "9" * 64,
        )
    expired_digest = sha256(b"expired-citation-lineage").digest()
    with migration_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO citation_open_locator (organization_id, "
                "locator_digest, digest_profile, package_ref, evidence_ref, "
                "resource_ref, revision_id, fragment_ref, issued_at, expires_at, "
                "profile_ref, retention_policy_ref, retain_until) VALUES ("
                ":org, :digest, 'citation-open-ref-sha256-v1', :package, "
                ":evidence, :resource, :revision, :fragment, :issued, :expires, "
                "'private-citation-open-v1', "
                "'citation-locator-retention-v1', :retain_until)"
            ),
            {
                "org": scenario.organization_id,
                "digest": expired_digest,
                "package": "pkg_" + "7" * 32,
                "evidence": "ev_" + "8" * 64,
                "resource": candidate.resource_ref,
                "revision": UUID(candidate.revision_ref),
                "fragment": candidate.fragment_ref,
                "issued": now - timedelta(days=31),
                "expires": now - timedelta(days=31) + timedelta(minutes=5),
                "retain_until": now - timedelta(days=1),
            },
        )

    retention = CitationOpenRetention(
        PostgreSQLCitationOpenRetentionPort(guarded_operator_engine)
    )

    assert retention.delete_expired(scenario.organization_id) == 1
    with migration_engine.connect() as connection:
        digests = {
            bytes(item)
            for item in connection.execute(
                text(
                    "SELECT locator_digest FROM citation_open_locator "
                    "WHERE organization_id = :org"
                ),
                {"org": scenario.organization_id},
            ).scalars()
        }
    assert expired_digest not in digests
    assert sha256(current_ref.value.encode()).digest() in digests


@pytest.mark.parametrize("mutation", ["reference", "organization", "expiry"])
def test_citation_locator_mutations_are_identical_not_available(
    mutation: str,
    citation_file_scenario: tuple[
        _FileImportScenario, PublishedFileImport, UUID, Engine
    ],
    guarded_runtime_engine: Engine,
) -> None:
    scenario, published, user_id, _ = citation_file_scenario
    now = datetime.now(UTC)
    authority = PostgreSQLMembershipAuthority(guarded_runtime_engine)
    with authority.current_user_actor(
        _identity(
            scenario,
            user_id,
            request_id=f"citation-{mutation}-issue",
            checked_at=now,
        )
    ) as verification:
        assert verification.citation_open_session is not None
        issued = issue_citation_open_ref(
            verification.citation_open_session,
            CitationOpenIssue(
                organization_id=scenario.organization_id,
                package_ref="pkg_" + "3" * 32,
                evidence_ref="ev_" + "4" * 64,
                resource_ref=published.candidate_ref.resource_ref,
                revision_id=UUID(published.candidate_ref.revision_ref),
                fragment_ref=published.candidate_ref.fragment_ref,
                issued_at=now,
                expires_at=now
                + (
                    timedelta(milliseconds=300)
                    if mutation == "expiry"
                    else timedelta(seconds=2)
                ),
            ),
            profile=CitationOpenProfile(
                profile_ref="private-citation-open-v1",
                retention_policy_ref="citation-locator-retention-v1",
                maximum_ttl=timedelta(minutes=10),
                retention_period=timedelta(days=30),
            ),
            reference_factory=lambda: REFERENCE,
        )
    values = {
        "citation_open_ref": issued,
        "organization_id": scenario.organization_id,
        "opened_at": now + timedelta(seconds=1),
    }
    if mutation == "reference":
        values["citation_open_ref"] = CitationOpenRef("cor_" + "8" * 64)
    elif mutation == "organization":
        values["organization_id"] = UUID("7e74ff30-a3d5-4655-b70d-c792beb874be")
    else:
        sleep(0.4)
        values["opened_at"] = datetime.now(UTC)

    with authority.current_user_actor(
        _identity(
            scenario,
            user_id,
            request_id=f"citation-{mutation}-open",
            checked_at=now + timedelta(seconds=1),
        )
    ) as verification:
        assert verification.citation_open_session is not None
        with pytest.raises(CitationLocatorNotAvailable):
            redeem_citation_open_ref(
                verification.citation_open_session,
                CitationOpenRedemption(**values),  # type: ignore[arg-type]
            )
