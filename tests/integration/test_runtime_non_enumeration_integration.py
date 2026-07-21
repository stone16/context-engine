from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from json import dumps, loads
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, text

from adapters.http.app import create_app
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLMembershipAuthority,
    assert_runtime_role,
    create_database_engine,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import CandidateIndex
from engine.runtime.contracts import Acquire, Resolved
from engine.runtime.evidence import CandidateRef
from tests.integration.test_runtime_authorized_evidence_integration import (
    ExactScopeAuthority,
    OrganizationEvidenceFixture,
    SeededAuthenticator,
    SeededOrganizationAuthority,
    _candidate_wire_values,
    _cleanup_fixture,
    _new_fixture,
    _seed_fixture,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).parents[2]
CATALOG_PATH = ROOT / "eval/catalogs/security-invariants.yaml"
RECEIVED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
QUERY = "same non-enumeration probe"
TOKEN = "non-enumeration-integration-token"

NORMALIZATION_ALLOWLIST = (
    "body.package.organizationRef",
    "body.package.decisionRef",
    "body.package.asOf",
    "body.package.expiresAt",
    "headers.X-Context-Request-Id",
)
RELEVANT_HEADERS = (
    "Content-Type",
    "Cache-Control",
    "X-Context-Request-Id",
)


class SequencedCandidateIndex:
    """Return one preregistered hostile ranking for each identical Acquire."""

    def __init__(
        self,
        rankings: tuple[tuple[CandidateRef, ...], ...],
    ) -> None:
        self.rankings = rankings
        self.calls: list[Acquire] = []

    def discover(self, request: Acquire) -> tuple[CandidateRef, ...]:
        call_index = len(self.calls)
        self.calls.append(request)
        if call_index >= len(self.rankings):
            raise AssertionError("unexpected extra CandidateIndex discovery")
        return self.rankings[call_index]


class SequencedRequestIdFactory:
    def __init__(self, count: int) -> None:
        self._request_ids = iter(
            f"non-enumeration-request-{ordinal}" for ordinal in range(count)
        )

    def __call__(self) -> str:
        return next(self._request_ids)


@dataclass(frozen=True, slots=True)
class NormalizedExternalResponse:
    status: int
    body: bytes
    headers: bytes


def _external_response_document(response: Response) -> dict[str, object]:
    return {
        "status": response.status_code,
        "body": response.json(),
        "headers": {
            header: response.headers[header]
            for header in RELEVANT_HEADERS
        },
    }


def _differing_paths(
    left: object,
    right: object,
    *,
    prefix: str = "",
) -> set[str]:
    if type(left) is not type(right):
        return {prefix}
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return {prefix}
        differences: set[str] = set()
        for key in sorted(left):
            child_path = f"{prefix}.{key}" if prefix else key
            differences.update(
                _differing_paths(
                    left[key],
                    right[key],
                    prefix=child_path,
                )
            )
        return differences
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return {prefix}
        differences = set()
        for index, (left_item, right_item) in enumerate(
            zip(left, right, strict=True)
        ):
            differences.update(
                _differing_paths(
                    left_item,
                    right_item,
                    prefix=f"{prefix}.{index}",
                )
            )
        return differences
    return set() if left == right else {prefix}


def _missing_candidate(active: OrganizationEvidenceFixture) -> CandidateRef:
    suffix = uuid4()
    return CandidateRef(
        organization_id=active.organization_id,
        source_ref=f"source:missing:{suffix}",
        resource_ref=f"resource:missing:{suffix}",
        revision_ref=str(uuid4()),
        fragment_ref=f"fragment:missing:{uuid4()}",
    )


def _catalog_accept_011() -> dict[str, object]:
    catalog = cast(
        dict[str, object],
        loads(CATALOG_PATH.read_text(encoding="utf-8")),
    )
    fixtures = cast(list[dict[str, object]], catalog["fixtures"])
    return next(fixture for fixture in fixtures if fixture["id"] == "ACCEPT-011")


def _replace_body_path(
    body: dict[str, object],
    allowlisted_path: str,
) -> None:
    segments = allowlisted_path.split(".")
    assert segments[0] == "body"
    current = body
    for segment in segments[1:-1]:
        child = current[segment]
        assert isinstance(child, dict)
        current = cast(dict[str, object], child)
    current[segments[-1]] = "<normalized-per-run-value>"


def _normalize_captured_body(response: Response) -> bytes:
    captured = response.content
    document = cast(dict[str, object], response.json())
    package = cast(dict[str, object], document["package"])
    for field_name in (
        "organizationRef",
        "decisionRef",
        "asOf",
        "expiresAt",
    ):
        encoded_value = dumps(
            package[field_name],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        assert captured.count(encoded_value) == 1
        captured = captured.replace(
            encoded_value,
            b'"<normalized-per-run-value>"',
            1,
        )
    return captured


def _normalize_external_response(response: Response) -> NormalizedExternalResponse:
    external = _external_response_document(response)
    body = deepcopy(cast(dict[str, object], external["body"]))
    headers = deepcopy(cast(dict[str, str], external["headers"]))
    used_paths: list[str] = []
    for allowlisted_path in NORMALIZATION_ALLOWLIST:
        if allowlisted_path.startswith("body."):
            _replace_body_path(body, allowlisted_path)
        else:
            assert allowlisted_path == "headers.X-Context-Request-Id"
            headers["X-Context-Request-Id"] = "<normalized-per-run-value>"
        used_paths.append(allowlisted_path)
    assert tuple(used_paths) == NORMALIZATION_ALLOWLIST
    return NormalizedExternalResponse(
        status=response.status_code,
        body=_normalize_captured_body(response),
        headers=dumps(
            headers,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"),
    )


def _assert_empty_non_enumerating_response(
    response: Response,
    *,
    active: OrganizationEvidenceFixture,
    other: OrganizationEvidenceFixture,
    missing: CandidateRef,
) -> None:
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    document = response.json()
    assert set(document) == {"kind", "package"}
    assert document["kind"] == "resolved"

    package = document["package"]
    assert set(package) == {
        "organizationRef",
        "purpose",
        "ttlSeconds",
        "asOf",
        "expiresAt",
        "decisionRef",
        "blocks",
        "evidence",
        "gaps",
        "budgetUsage",
        "coverage",
    }
    assert package["purpose"] == "context.answer"
    assert package["blocks"] == []
    assert package["evidence"] == []
    assert package["gaps"] == []
    assert package["budgetUsage"] == {
        "tokens": 0,
        "providerCalls": 0,
        "costMicrounits": 0,
        "elapsedMs": 0,
    }
    assert package["coverage"] == {
        "status": "empty",
        "reason": "no_authorized_evidence",
    }

    response_text = response.text
    forbidden_values = (
        *_candidate_wire_values(
            active.authorized,
            body=active.authorized_body,
        ),
        *_candidate_wire_values(active.denied, body=active.denied_body),
        *_candidate_wire_values(other.authorized, body=other.authorized_body),
        *_candidate_wire_values(other.denied, body=other.denied_body),
        *_candidate_wire_values(missing, body="missing-body-must-not-exist"),
        str(active.organization_id),
        str(other.organization_id),
    )
    assert all(value not in response_text for value in forbidden_values)
    folded_response = response_text.casefold()
    assert "denied" not in folded_response
    assert "candidate" not in folded_response
    assert "denialreason" not in folded_response
    assert "resourceid" not in folded_response
    assert "resourcename" not in folded_response


def _assert_empty_runtime_outcome(outcome: Resolved) -> None:
    assert outcome.kind == "resolved"
    assert outcome.package.blocks == ()
    assert outcome.package.evidence == ()
    assert outcome.package.gaps == ()
    assert outcome.package.coverage.status == "empty"
    assert outcome.package.coverage.reason == "no_authorized_evidence"
    assert outcome.package.budget_usage.tokens == 0
    assert outcome.scope_decision.target_count == 1
    assert outcome.scope_decision.is_empty is False


def _normalized_domain_outcome(outcome: Resolved) -> bytes:
    document = cast(dict[str, object], asdict(outcome))
    package = cast(dict[str, object], document["package"])
    package["organization_ref"] = "<normalized-per-run-value>"
    package["decision_ref"] = "<normalized-per-run-value>"
    package["as_of"] = "<normalized-per-run-value>"
    package["expires_at"] = "<normalized-per-run-value>"
    return dumps(
        document,
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _assert_non_owner_force_rls(engine: Engine) -> None:
    with engine.connect() as connection:
        assert_runtime_role(connection)
        rows = connection.execute(
            text(
                """
                SELECT
                    table_name,
                    relrowsecurity,
                    relforcerowsecurity
                FROM (
                    VALUES
                        ('context_resource'),
                        ('context_revision'),
                        ('context_fragment')
                ) AS required(table_name)
                JOIN pg_class
                  ON pg_class.oid = required.table_name::regclass
                ORDER BY table_name
                """
            )
        ).all()
    assert len(rows) == 3
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in rows)


def test_real_postgres_http_denied_and_missing_are_externally_equivalent(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
) -> None:
    """NON-ENUMERATION-009 compares content semantics, not response timing."""

    fixture = _new_fixture()
    active = fixture.org_a
    other = fixture.org_b
    missing = _missing_candidate(active)
    rankings = (
        (other.authorized,),
        (active.denied,),
        (active.denied, missing),
        (missing,),
        (active.authorized,),
    )
    index = SequencedCandidateIndex(rankings)
    scope_authority = ExactScopeAuthority(active.authorized)
    outcomes: list[Resolved] = []
    runtime = Runtime(
        required_kernel_dependencies(),
        package_ttl_seconds=30,
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: RECEIVED_AT,
    )
    client = TestClient(
        create_app(
            authenticator=SeededAuthenticator(active, token=TOKEN),
            organization_authority=SeededOrganizationAuthority(
                active.organization_id
            ),
            membership_authority=PostgreSQLMembershipAuthority(
                guarded_runtime_engine
            ),
            scope_authority=scope_authority,
            runtime=runtime,
            resolution_observer=outcomes.append,
            clock=lambda: RECEIVED_AT,
            request_id_factory=SequencedRequestIdFactory(len(rankings)),
        )
    )

    accept_011 = _catalog_accept_011()
    operation = cast(dict[str, object], accept_011["operation"])
    expected = cast(dict[str, object], accept_011["expected"])
    external_response = cast(dict[str, object], expected["externalResponse"])
    normalization_allowlist = cast(
        list[str], operation["normalizationAllowlist"]
    )
    assert tuple(normalization_allowlist) == NORMALIZATION_ALLOWLIST
    assert external_response["timingEqualityClaimed"] is False

    migration_engine = create_database_engine(migration_configuration)
    try:
        _seed_fixture(migration_engine, fixture)
        _assert_non_owner_force_rls(guarded_runtime_engine)

        responses = tuple(
            client.post(
                "/v1/context:resolve",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={"kind": "acquire", "need": {"query": QUERY}},
            )
            for _ in rankings
        )

        probe_responses = responses[:4]
        for response in probe_responses:
            _assert_empty_non_enumerating_response(
                response,
                active=active,
                other=other,
                missing=missing,
            )
        normalized = tuple(
            _normalize_external_response(response)
            for response in probe_responses
        )
        assert normalized == (normalized[0],) * len(normalized)
        baseline_document = _external_response_document(probe_responses[0])
        raw_difference_paths = set().union(
            *(
                _differing_paths(
                    baseline_document,
                    _external_response_document(response),
                )
                for response in probe_responses[1:]
            )
        )
        assert raw_difference_paths <= set(NORMALIZATION_ALLOWLIST)
        assert raw_difference_paths == {
            "body.package.organizationRef",
            "body.package.decisionRef",
            "headers.X-Context-Request-Id",
        }
        content_lengths = {
            response.headers["content-length"] for response in probe_responses
        }
        assert len(content_lengths) == 1

        assert len(outcomes) == len(rankings)
        for outcome in outcomes[:4]:
            _assert_empty_runtime_outcome(outcome)
        normalized_domain_outcomes = tuple(
            _normalized_domain_outcome(outcome) for outcome in outcomes[:4]
        )
        assert normalized_domain_outcomes == (
            normalized_domain_outcomes[0],
        ) * len(normalized_domain_outcomes)
        assert len({outcome.scope_decision.digest for outcome in outcomes}) == 1

        authorized_response = responses[4]
        assert authorized_response.status_code == 200
        authorized_package = authorized_response.json()["package"]
        assert authorized_package["coverage"] == {"status": "sufficient"}
        assert len(authorized_package["blocks"]) == 1
        assert len(authorized_package["evidence"]) == 1
        assert authorized_package["blocks"][0]["text"] == active.authorized_body
        assert authorized_package["evidence"][0]["sourceRef"] == (
            active.authorized.source_ref
        )
        authorized_outcome = outcomes[4]
        assert len(authorized_outcome.package.blocks) == 1
        assert len(authorized_outcome.package.evidence) == 1
        assert authorized_outcome.package.coverage.status == "sufficient"
        assert authorized_outcome.package.coverage.reason is None

        assert len(index.calls) == len(rankings)
        assert all(call.need.query == QUERY for call in index.calls)
        assert index.rankings[2][0] == active.denied
        assert len(scope_authority.identities) == len(rankings)
        caller_signatures = {
            (
                identity.organization_id,
                identity.user_id,
                identity.membership_id,
                identity.membership_version,
                identity.principal_ref,
                identity.agent_version_ref,
                identity.purpose,
                identity.authentication_binding_ref,
            )
            for identity in scope_authority.identities
        }
        assert len(caller_signatures) == 1
    finally:
        try:
            _cleanup_fixture(migration_engine, fixture)
        finally:
            migration_engine.dispose()
