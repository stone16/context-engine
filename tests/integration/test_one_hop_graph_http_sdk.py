from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from typing import cast

import pytest
from sqlalchemy import Engine, text
from uvicorn import Config, Server

from adapters.http.app import create_app
from adapters.http.scope_authority import ScopeAuthorityIdentity
from engine.control import FileImportPath
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLMembershipAuthority,
    create_database_engine,
)
from engine.persistence.file_imports import PublishedFileImport
from engine.runtime.authorized_ranking import RankerWeights
from engine.runtime.candidate_ranking import (
    CandidateQuery,
    RankedCandidate,
    RankedCandidateList,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import CandidateIndex, exact_phrase_digest
from engine.runtime.contracts import Acquire
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import ExactPhraseDiscoveryRequest
from engine.runtime.package_digest import QueryDigestKeyring
from engine.runtime.scope import CandidateDiscoveryScope, ScopeSet, ScopeTarget
from engine.runtime.scope_authority import (
    TrustedScopeSnapshot,
    _close_scope_authority_scope,
    _construct_trusted_scope_snapshot,
    _open_scope_authority_scope,
)
from tests.integration.test_file_import_tracer import (
    _OrganizationAuthority,
    _RuntimeAuthenticator,
)
from tests.integration.test_z_egress_grant_file import (
    _pack_and_install_resolve_sdk,
    _run_sdk_process,
    _unused_port,
    _wait_for_tcp,
)
from tests.support.file_imports import (
    FileImportScenario,
    delete_file_import_scenario,
    prepare_file_import_scenario,
    prepare_repeat_file_import,
    run_file_import,
)
from tests.support.releases import (
    clear_test_runtime_release,
    ensure_test_runtime_release,
)

pytestmark = pytest.mark.integration

QUERY = "synthetic graph answer"
DENIED_MARKER = "SYNTHETIC-DENIED-NEIGHBOUR-MUST-BE-INVISIBLE"
TWO_HOP_MARKER = "SYNTHETIC-TWO-HOP-MUST-NOT-BE-GENERATED"


class _RootOnlyCandidateIndex:
    """Submit only the main-path root; graph structure is not index authority."""

    def __init__(self, root: CandidateRef) -> None:
        self.root = root

    def prepare_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> ExactPhraseDiscoveryRequest:
        del effective_scope
        return ExactPhraseDiscoveryRequest(exact_phrase_digest(request.need.query))

    def discover(
        self,
        request: Acquire,
        discovery_session: object,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> CandidateQuery:
        del request, discovery_session, effective_scope
        return CandidateQuery(
            ranked_lists=(
                RankedCandidateList(
                    ranker_ref="synthetic_main",
                    candidates=(RankedCandidate(candidate_ref=self.root),),
                ),
            )
        )


class _ArticleScopeAuthority:
    """Trusted fixture authority allowing exact Articles, never graph edges."""

    def __init__(self, allowed: tuple[CandidateRef, ...]) -> None:
        self.allowed = allowed

    @contextmanager
    def current_scope(
        self,
        identity: ScopeAuthorityIdentity,
    ) -> Iterator[TrustedScopeSnapshot]:
        authority_scope = _open_scope_authority_scope()
        articles = ScopeSet(
            frozenset(
                ScopeTarget(
                    identity.organization_id,
                    candidate.source_ref,
                    candidate.resource_ref,
                )
                for candidate in self.allowed
            )
        )
        try:
            yield _construct_trusted_scope_snapshot(
                authority_scope=authority_scope,
                organization_id=identity.organization_id,
                user_id=identity.user_id,
                membership_id=identity.membership_id,
                membership_version=identity.membership_version,
                policy_epoch=identity.policy_epoch,
                principal_ref=identity.principal_ref,
                agent_version_ref=identity.agent_version_ref,
                purpose=identity.purpose,
                request_id=identity.request_id,
                authentication_binding_ref=identity.authentication_binding_ref,
                checked_at=identity.checked_at,
                organization_boundary=articles,
                membership_rights=articles,
                principal_grants=articles,
                agent_ceiling=articles,
                source_native_acl=articles,
                resource_acl=articles,
                purpose_policy=articles,
            )
        finally:
            _close_scope_authority_scope(authority_scope)


def _paragraph(published: PublishedFileImport) -> CandidateRef:
    candidates = published.candidate_refs
    return next(
        candidate
        for candidate in candidates
        if candidate.fragment_ref == "fragment:paragraph:1"
    )


def _publish_path(
    scenario: FileImportScenario,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    *,
    path: str,
    payload: bytes,
) -> CandidateRef:
    (scenario.root / path).write_bytes(payload)
    prepared, token = prepare_repeat_file_import(
        scenario,
        guarded_control_engine,
        idempotency_key=f"synthetic-one-hop-{path}",
        path=FileImportPath(path),
    )
    return _paragraph(
        run_file_import(
            scenario,
            prepared,
            token,
            guarded_worker_engine,
            config_version="markdown-config-v3",
        )
    )


@pytest.mark.security_evidence(id="SDK-ONE-HOP-GRAPH-151", layer="runtime")
@pytest.mark.security_evidence(id="PG-ONE-HOP-GRAPH-151", layer="postgres")
def test_generated_sdk_one_hop_reauthorizes_and_leaves_no_denied_trace(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=(
            b"# Synthetic root\n\n"
            b"Main anchor links [[adjacent]] and [[denied]].\n"
        ),
    )
    assert scenario.token is not None
    migration_engine = create_database_engine(migration_configuration)
    server: Server | None = None
    server_thread: Thread | None = None
    try:
        root = _paragraph(
            run_file_import(
                scenario,
                scenario.prepared,
                scenario.token,
                guarded_worker_engine,
                config_version="markdown-config-v3",
            )
        )
        adjacent = _publish_path(
            scenario,
            guarded_control_engine,
            guarded_worker_engine,
            path="adjacent.md",
            payload=(
                b"# Synthetic adjacent\n\n"
                b"Synthetic graph answer from an outgoing neighbour. "
                b"Continue at [[second]].\n"
            ),
        )
        backlink = _publish_path(
            scenario,
            guarded_control_engine,
            guarded_worker_engine,
            path="backlink.md",
            payload=(
                b"# Synthetic backlink\n\n"
                b"Synthetic graph answer from a backlink to [[handbook]].\n"
            ),
        )
        denied = _publish_path(
            scenario,
            guarded_control_engine,
            guarded_worker_engine,
            path="denied.md",
            payload=(
                f"# {DENIED_MARKER}\n\n".encode()
                + b"\n\n".join(
                    f"{QUERY} fragment {index}.".encode()
                    for index in range(65)
                )
                + b"\n"
            ),
        )
        two_hop = _publish_path(
            scenario,
            guarded_control_engine,
            guarded_worker_engine,
            path="second.md",
            payload=f"# Synthetic second hop\n\n{TWO_HOP_MARKER} {QUERY}.\n".encode(),
        )
        clear_test_runtime_release(scenario.organization_id)
        ensure_test_runtime_release(
            scenario.organization_id,
            active_revision_refs=tuple(
                sorted(
                    {
                        root.revision_ref,
                        adjacent.revision_ref,
                        backlink.revision_ref,
                        denied.revision_ref,
                        two_hop.revision_ref,
                    }
                )
            ),
        )
        with migration_engine.connect() as connection:
            denied_fragment_count = connection.execute(
                text(
                    "SELECT count(*) FROM context_fragment "
                    "WHERE organization_id = :organization_id "
                    "AND resource_ref = :resource_ref"
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": denied.resource_ref,
                },
            ).scalar_one()
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
        assert denied_fragment_count > 64

        observed: list[object] = []
        app = create_app(
            authenticator=_RuntimeAuthenticator(
                scenario.organization_id,
                user_id,
                scenario.membership_id,
                token="synthetic-sdk-token",
            ),
            organization_authority=_OrganizationAuthority(),
            membership_authority=PostgreSQLMembershipAuthority(
                guarded_runtime_engine
            ),
            scope_authority=_ArticleScopeAuthority((root, adjacent, backlink, two_hop)),
            runtime=Runtime(
                required_kernel_dependencies(),
                candidate_index=cast(CandidateIndex, _RootOnlyCandidateIndex(root)),
                ranker_weights=RankerWeights(
                    {"synthetic_main": 1.0, "graph": 2.0}
                ),
                clock=lambda: datetime.now(UTC).replace(microsecond=0),
                query_digest_keyring=query_digest_keyring,
            ),
            resolution_observer=observed.append,
            clock=lambda: datetime.now(UTC).replace(microsecond=0),
        )
        port = _unused_port()
        server = Server(
            Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="warning",
                lifespan="off",
            )
        )
        server_thread = Thread(target=server.run, daemon=True)
        server_thread.start()
        _wait_for_tcp(port)

        consumer_root = tmp_path / "installed-one-hop-sdk"
        consumer_root.mkdir()
        _pack_and_install_resolve_sdk(consumer_root)
        completed = _run_sdk_process(
            ["node", "live-empty-consumer.mjs"],
            cwd=consumer_root,
            env={
                **os.environ,
                "CONTEXT_ENGINE_SDK_BASE_URL": f"http://127.0.0.1:{port}",
                "CONTEXT_ENGINE_SDK_QUERY": QUERY,
                "CONTEXT_ENGINE_SDK_REQUEST_ID": "synthetic-one-hop-sdk",
                "CONTEXT_ENGINE_SDK_TEST_AUTHENTICATION": "synthetic-sdk-token",
            },
        )
        document = json.loads(completed.stdout)
        package = document["package"]
        serialized = json.dumps(document, sort_keys=True)

        delivered_lineage = {
            (
                item["resourceRef"],
                item["revisionRef"],
                item["fragmentRef"],
            )
            for item in package["evidence"]
        }
        assert (
            adjacent.resource_ref,
            adjacent.revision_ref,
            adjacent.fragment_ref,
        ) in delivered_lineage
        assert (
            backlink.resource_ref,
            backlink.revision_ref,
            backlink.fragment_ref,
        ) in delivered_lineage
        for invisible in (denied, two_hop):
            assert invisible.resource_ref not in serialized
            assert invisible.revision_ref not in serialized
            assert (
                invisible.resource_ref,
                invisible.revision_ref,
                invisible.fragment_ref,
            ) not in delivered_lineage
        assert DENIED_MARKER not in serialized
        assert TWO_HOP_MARKER not in serialized
        assert package["gaps"] == []
        assert package["coverage"] == {"status": "sufficient"}

        decision_ref = package["decisionRef"]
        with migration_engine.connect() as connection:
            run = connection.execute(
                text(
                    """
                    SELECT authorized_evidence_refs, outcome
                    FROM context_run
                    WHERE organization_id = :organization_id
                      AND decision_ref = :decision_ref
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "decision_ref": decision_ref,
                },
            ).one()
            audits = connection.execute(
                text(
                    """
                    SELECT count(*) FROM decision_audit
                    WHERE organization_id = :organization_id
                      AND decision_ref = :decision_ref
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "decision_ref": decision_ref,
                },
            ).scalar_one()
        assert run.outcome == "delivered_authorized"
        assert tuple(run.authorized_evidence_refs) == tuple(
            item["evidenceRef"] for item in package["evidence"]
        )
        assert audits == 0
        assert len(observed) == 1
        assert DENIED_MARKER not in repr(observed[0])
        assert denied.resource_ref not in repr(observed[0])
    finally:
        if server is not None:
            server.should_exit = True
        if server_thread is not None:
            server_thread.join(timeout=10)
        clear_test_runtime_release(scenario.organization_id)
        migration_engine.dispose()
        delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )
