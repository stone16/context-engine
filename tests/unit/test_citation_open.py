from __future__ import annotations

from contextlib import nullcontext
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import Engine

import engine.persistence.citation as persistence_citation
from engine.persistence.citation import PostgreSQLCitationOpenRetentionPort
from engine.runtime.citation import (
    CITATION_OPEN_DIGEST_PROFILE,
    CITATION_OPEN_RETENTION_CLASS,
    CitationAuthorityUnavailable,
    CitationLocatorNotAvailable,
    CitationOpenIssue,
    CitationOpenProfile,
    CitationOpenRedemption,
    CitationOpenRetention,
    CitationOpenTarget,
    CitationOpenTargetLineage,
    _close_citation_authority_scope,
    _construct_citation_open_session,
    _open_citation_authority_scope,
    issue_citation_open_ref,
    redeem_citation_open_ref,
)
from engine.runtime.contracts import CitationOpenRef
from engine.runtime.evidence import CandidateRef

NOW = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)
ORGANIZATION_ID = UUID("7e74ff30-a3d5-4655-b70d-c792beb874be")
REVISION_ID = UUID("841caaf5-8898-4894-843f-ad8982dc710a")
REFERENCE = "cor_" + "a" * 64


def _profile() -> CitationOpenProfile:
    return CitationOpenProfile(
        profile_ref="private-citation-open-v1",
        retention_policy_ref="citation-locator-retention-v1",
        maximum_ttl=timedelta(minutes=10),
        retention_period=timedelta(days=30),
    )


def _issue() -> CitationOpenIssue:
    return CitationOpenIssue(
        organization_id=ORGANIZATION_ID,
        package_ref="pkg_" + "1" * 32,
        evidence_ref="ev_" + "2" * 64,
        resource_ref="resource:file:handbook",
        revision_id=REVISION_ID,
        fragment_ref="fragment:paragraph:1",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )


class RecordingCitationPort:
    def __init__(self) -> None:
        self.issue_calls: list[dict[str, object]] = []
        self.redemption_calls: list[CitationOpenRedemption] = []
        self.target: CitationOpenTarget | None = CitationOpenTarget(
            candidate_ref=CandidateRef(
                organization_id=ORGANIZATION_ID,
                source_ref="7cc3242f-53fa-46dc-a861-82b8bd85103c",
                resource_ref="resource:file:handbook",
                revision_ref=str(REVISION_ID),
                fragment_ref="fragment:paragraph:1",
            ),
            lineage=CitationOpenTargetLineage(
                package_ref="pkg_" + "1" * 32,
                evidence_ref="ev_" + "2" * 64,
            ),
        )

    def issue(
        self,
        *,
        request: CitationOpenIssue,
        locator_digest: bytes,
        digest_profile: str,
        profile: CitationOpenProfile,
        retain_until: datetime,
    ) -> bool:
        self.issue_calls.append(
            {
                "request": request,
                "locator_digest": locator_digest,
                "digest_profile": digest_profile,
                "profile": profile,
                "retain_until": retain_until,
            }
        )
        return True

    def redeem(
        self,
        request: CitationOpenRedemption,
    ) -> CitationOpenTarget | None:
        self.redemption_calls.append(request)
        return self.target


class RecordingCitationRetentionPort:
    def __init__(self) -> None:
        self.organization_ids: list[UUID] = []

    def delete_expired_lineage(self, organization_id: UUID) -> int:
        self.organization_ids.append(organization_id)
        return 3


class RoleGuardFailureEngine:
    def begin(self) -> object:
        return nullcontext(object())


def test_issue_returns_opaque_locator_and_persists_digest_only() -> None:
    port = RecordingCitationPort()
    scope = _open_citation_authority_scope()
    try:
        issued = issue_citation_open_ref(
            _construct_citation_open_session(authority_scope=scope, port=port),
            _issue(),
            profile=_profile(),
            reference_factory=lambda: REFERENCE,
        )
    finally:
        _close_citation_authority_scope(scope)

    assert issued == CitationOpenRef(REFERENCE)
    assert port.issue_calls == [
        {
            "request": _issue(),
            "locator_digest": sha256(REFERENCE.encode()).digest(),
            "digest_profile": CITATION_OPEN_DIGEST_PROFILE,
            "profile": _profile(),
            "retain_until": NOW + timedelta(days=30),
        }
    ]
    assert REFERENCE not in repr(port.issue_calls[0])


def test_locator_is_multi_use_and_redemption_carries_no_prior_authorization() -> None:
    port = RecordingCitationPort()
    request = CitationOpenRedemption(
        citation_open_ref=CitationOpenRef(REFERENCE),
        organization_id=ORGANIZATION_ID,
        opened_at=NOW + timedelta(seconds=1),
    )

    scope = _open_citation_authority_scope()
    try:
        session = _construct_citation_open_session(authority_scope=scope, port=port)
        first = redeem_citation_open_ref(session, request)
        second = redeem_citation_open_ref(session, request)
    finally:
        _close_citation_authority_scope(scope)

    assert first == second == port.target
    assert len(port.redemption_calls) == 2
    assert first is not None
    lineage_document = {
        item.name: getattr(first.lineage, item.name) for item in fields(first.lineage)
    }
    assert lineage_document == {
        "package_ref": "pkg_" + "1" * 32,
        "evidence_ref": "ev_" + "2" * 64,
    }
    protected_old_decision_facts = (
        "principal_ref",
        "membership_id",
        "audience_digest",
        "purpose",
        "decision_ref",
        "policy_epoch",
    )
    for field_name in protected_old_decision_facts:
        assert not hasattr(first.lineage, field_name)


def test_retention_deletes_only_lineage_past_its_profile_window() -> None:
    port = RecordingCitationRetentionPort()

    deleted = CitationOpenRetention(port).delete_expired(ORGANIZATION_ID)

    assert deleted == 3
    assert port.organization_ids == [ORGANIZATION_ID]


def test_postgres_operator_role_guard_failure_is_an_opaque_authority_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_operator_role(connection: object) -> None:
        del connection
        raise AssertionError("private operator facts")

    monkeypatch.setattr(
        persistence_citation,
        "assert_security_operator_role",
        reject_operator_role,
    )
    retention = PostgreSQLCitationOpenRetentionPort(
        cast(Engine, RoleGuardFailureEngine())
    )

    with pytest.raises(CitationAuthorityUnavailable) as error:
        retention.delete_expired_lineage(ORGANIZATION_ID)

    assert "private operator facts" not in str(error.value)


@pytest.mark.parametrize(
    "reference",
    [
        "egrm_" + "a" * 64,
        "der_" + "a" * 64,
        "cor_" + "a" * 63,
        "cor_" + "A" * 64,
    ],
)
@pytest.mark.security_evidence(id="PROP-CITATION-AUTH-010", layer="property")
def test_cross_kind_and_forged_locator_are_generic_not_available(
    reference: str,
) -> None:
    port = RecordingCitationPort()
    port.target = None
    scope = _open_citation_authority_scope()
    try:
        session = _construct_citation_open_session(authority_scope=scope, port=port)
        with pytest.raises(CitationLocatorNotAvailable, match="not available"):
            redeem_citation_open_ref(
                session,
                CitationOpenRedemption(
                    citation_open_ref=CitationOpenRef(reference),
                    organization_id=ORGANIZATION_ID,
                    opened_at=NOW,
                ),
            )
    finally:
        _close_citation_authority_scope(scope)


def test_issuer_rejects_invalid_profile_lifetime_without_calling_port() -> None:
    port = RecordingCitationPort()
    issue = CitationOpenIssue(
        organization_id=ORGANIZATION_ID,
        package_ref="pkg_" + "1" * 32,
        evidence_ref="ev_" + "2" * 64,
        resource_ref="resource:file:handbook",
        revision_id=REVISION_ID,
        fragment_ref="fragment:paragraph:1",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=11),
    )

    scope = _open_citation_authority_scope()
    try:
        session = _construct_citation_open_session(authority_scope=scope, port=port)
        with pytest.raises(CitationLocatorNotAvailable):
            issue_citation_open_ref(
                session,
                issue,
                profile=_profile(),
                reference_factory=lambda: REFERENCE,
            )
    finally:
        _close_citation_authority_scope(scope)

    assert port.issue_calls == []
    assert _profile().retention_class == CITATION_OPEN_RETENTION_CLASS


def test_port_failures_are_opaque_authority_failures() -> None:
    class FailingPort(RecordingCitationPort):
        def issue(self, **values: object) -> bool:
            del values
            raise RuntimeError("private database detail")

        def redeem(
            self,
            request: CitationOpenRedemption,
        ) -> CitationOpenTarget | None:
            del request
            raise RuntimeError("private database detail")

    port = FailingPort()
    scope = _open_citation_authority_scope()
    try:
        session = _construct_citation_open_session(authority_scope=scope, port=port)
        with pytest.raises(CitationAuthorityUnavailable) as issue_error:
            issue_citation_open_ref(
                session,
                _issue(),
                profile=_profile(),
                reference_factory=lambda: REFERENCE,
            )
        with pytest.raises(CitationAuthorityUnavailable) as redemption_error:
            redeem_citation_open_ref(
                session,
                CitationOpenRedemption(
                    citation_open_ref=CitationOpenRef(REFERENCE),
                    organization_id=ORGANIZATION_ID,
                    opened_at=NOW,
                ),
            )
    finally:
        _close_citation_authority_scope(scope)
    assert "private database detail" not in str(issue_error.value)
    assert "private database detail" not in str(redemption_error.value)
