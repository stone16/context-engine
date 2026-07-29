from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import Engine

from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.runtime.authorized_ranking import AuthorizedRerankItem
from engine.runtime.evidence import AuthorizedProjection, CandidateRef
from tests.integration.test_runtime_authorized_evidence_integration import (
    _assert_exact_authorized_http_resolve,
    _cleanup_fixture,
    _new_fixture,
    _seed_fixture,
)
from tests.support.releases import (
    ensure_test_runtime_release,
)

pytestmark = pytest.mark.integration


def test_http_candidate_port_seals_raw_refs_before_content_consumer(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    guarded_control_engine: Engine,
    guarded_operator_engine: Engine,
    query_digest_keyring: object,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
) -> None:
    """Real HTTP/PG proof: CandidateRef -> Kernel -> AuthorizedProjection only."""

    fixture = _new_fixture()
    consumed: list[AuthorizedRerankItem] = []
    original_init = AuthorizedRerankItem.__init__

    def observe_consumer(
        self: AuthorizedRerankItem,
        projection: AuthorizedProjection,
        rank_evidence: object = None,
    ) -> None:
        assert type(projection) is AuthorizedProjection
        assert not isinstance(projection, CandidateRef)
        original_init(self, projection, rank_evidence)  # type: ignore[arg-type]
        consumed.append(self)

    monkeypatch.setattr(AuthorizedRerankItem, "__init__", observe_consumer)
    migration_engine = create_database_engine(migration_configuration)
    try:
        _seed_fixture(migration_engine, fixture)
        ensure_test_runtime_release(fixture.org_a.organization_id)
        observations = _assert_exact_authorized_http_resolve(
            active=fixture.org_a,
            other=fixture.org_b,
            guarded_runtime_engine=guarded_runtime_engine,
            guarded_control_engine=guarded_control_engine,
            guarded_operator_engine=guarded_operator_engine,
            query_digest_keyring=query_digest_keyring,  # type: ignore[arg-type]
        )

        assert observations == (0, 0, 0)
        assert len(consumed) == 1
        assert consumed[0].projection.candidate_ref == fixture.org_a.authorized
        assert consumed[0].projection.projected_body == fixture.org_a.authorized_body
        assert all(
            item.projection.candidate_ref not in {
                fixture.org_a.denied,
                fixture.org_b.authorized,
            }
            for item in consumed
        )
        record_property("candidate_port_http_seam", "PASS")
    finally:
        try:
            _cleanup_fixture(migration_engine, fixture)
        finally:
            migration_engine.dispose()
