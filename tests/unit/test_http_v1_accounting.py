from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from uvicorn import Config, Server

from adapters.embeddings import DeterministicEmbeddingTwin
from adapters.http.app import create_app
from adapters.http.contracts import ContextPackageV1Wire
from adapters.pgvector import PostgreSQLVectorCandidateIndex
from engine.persistence.membership_context import MembershipIdentity
from engine.runtime.actor import (
    CurrentMembershipVerification,
    _close_membership_authority_scope,
    _construct_current_membership_verification,
    _open_membership_authority_scope,
)
from engine.runtime.budget import PackageBudgetMeter
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.context_run import ContextRunRecord
from engine.runtime.contracts import Acquire
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import (
    MaterializedProjectionPort,
    VectorDiscoveryRequest,
    _close_materialized_projection_scope,
    _construct_materialized_projection_session,
    _open_materialized_projection_scope,
)
from engine.runtime.policy_epoch import (
    _close_policy_epoch_authority_scope,
    _construct_policy_epoch_session,
    _observe_current_policy_epoch,
    _open_policy_epoch_authority_scope,
)
from engine.runtime.release_lineage import (
    PACKAGE_SCHEMA_REF_V1,
    RUNTIME_PROFILE_DIGEST_V1,
    RUNTIME_PROFILE_REF_V1,
    RUNTIME_TOKENIZER_REF_V1,
)
from engine.runtime.scope import (
    CandidateDiscoveryScope,
    ScopeSet,
    ScopeTarget,
    TrustedScopeOperands,
)
from engine.supply import DETERMINISTIC_TWIN_EMBEDDING_PROFILE
from engine.tokenizer_accounting import UNICODE_SCALAR_TOKENIZER_PROFILE
from tests.support.context_run import (
    TEST_QUERY_DIGEST_KEYRING,
    RecordingContextRunPort,
    recording_context_run_session,
)
from tests.support.releases import active_runtime_release
from tests.unit.test_http_effective_scope import DeterministicScopeAuthority
from tests.unit.test_http_trust_boundary import (
    INTERNAL_ORGANIZATION_REF,
    VALID_TOKEN,
    DeterministicAuthenticator,
    DeterministicOrganizationAuthority,
)
from tests.unit.test_runtime_authorized_evidence import (
    AS_OF,
    AUTHORIZED,
    RecordingMaterializedPort,
)

QUERY = "account 世界"
ROOT = Path(__file__).parents[2]


class _RecordingEmbeddingProvider:
    profile = DeterministicEmbeddingTwin().profile
    provider_profile = DETERMINISTIC_TWIN_EMBEDDING_PROFILE

    def __init__(self) -> None:
        self.calls = 0
        self.bytes_sent = 0

    def embed(self, inputs: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        self.bytes_sent += sum(len(value.encode("utf-8")) for value in inputs)
        return DeterministicEmbeddingTwin().embed(inputs)

    def embed_documents(
        self,
        inputs: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        return self.embed(inputs)


class _AuthorizedMaterializedPort(RecordingMaterializedPort):
    def discover_vector(  # type: ignore[override]
        self,
        query_embedding: tuple[float, ...],
        embedding_profile_digest: str,
        limit: int,
        source_refs: tuple[str, ...] | None,
        resource_refs: tuple[str, ...] | None,
        effective_scope: object,
    ) -> tuple[CandidateRef, ...]:
        del (
            query_embedding,
            embedding_profile_digest,
            source_refs,
            resource_refs,
            effective_scope,
        )
        return (AUTHORIZED,)[:limit]


class _MixedGenerationVectorIndex(PostgreSQLVectorCandidateIndex):
    def prepare_generation_bound_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
        budget: PackageBudgetMeter,
        active_embedding_profile_digest: str,
        active_release_generation: int,
    ) -> VectorDiscoveryRequest:
        return super().prepare_generation_bound_discovery(
            request,
            effective_scope=effective_scope,
            budget=budget,
            active_embedding_profile_digest=active_embedding_profile_digest,
            active_release_generation=active_release_generation + 1,
        )


class _CurrentEpochPort:
    def read_current_epoch(self, organization_id: UUID) -> object:
        assert organization_id == UUID(INTERNAL_ORGANIZATION_REF)
        return 7


class _V1MembershipAuthority:
    def __init__(
        self,
        materialized: _AuthorizedMaterializedPort,
        context_runs: RecordingContextRunPort,
        *,
        corrupt_tokenizer_digest: bool = False,
    ) -> None:
        self._materialized = materialized
        self._context_runs = context_runs
        self._corrupt_tokenizer_digest = corrupt_tokenizer_digest

    @contextmanager
    def current_user_actor(
        self,
        identity: MembershipIdentity,
    ) -> Iterator[CurrentMembershipVerification]:
        membership_scope = _open_membership_authority_scope()
        projection_scope = _open_materialized_projection_scope()
        epoch_scope = _open_policy_epoch_authority_scope()
        try:
            release = active_runtime_release(
                identity.organization_id,
                suffix="test-v1-accounting",
                active_revision_refs=(AUTHORIZED.revision_ref,),
                runtime_profile_ref=RUNTIME_PROFILE_REF_V1,
                runtime_profile_digest=RUNTIME_PROFILE_DIGEST_V1,
                tokenizer_ref=RUNTIME_TOKENIZER_REF_V1,
                package_schema_ref=PACKAGE_SCHEMA_REF_V1,
            )
            if self._corrupt_tokenizer_digest:
                object.__setattr__(release, "tokenizer_profile_digest", "0" * 64)
            epoch = _observe_current_policy_epoch(
                _construct_policy_epoch_session(
                    authority_scope=epoch_scope,
                    organization_id=identity.organization_id,
                    port=_CurrentEpochPort(),
                )
            )
            projection = _construct_materialized_projection_session(
                authority_scope=projection_scope,
                port=cast(MaterializedProjectionPort, self._materialized),
            )
            with recording_context_run_session(port=self._context_runs) as (
                persistence,
                _,
            ):
                yield _construct_current_membership_verification(
                    authority_scope=membership_scope,
                    organization_id=identity.organization_id,
                    user_id=identity.user_id,
                    membership_id=identity.membership_id,
                    membership_version=identity.membership_version,
                    principal_ref=identity.principal_ref,
                    request_id=identity.request_id,
                    authentication_binding_ref=identity.authentication_binding_ref,
                    checked_at=identity.checked_at,
                    policy_epoch_verification=epoch,
                    active_runtime_release=release,
                    materialized_projection_session=projection,
                    context_run_persistence_session=persistence,
                )
        finally:
            _close_policy_epoch_authority_scope(epoch_scope)
            _close_materialized_projection_scope(projection_scope)
            _close_membership_authority_scope(membership_scope)


def _scope_authority() -> DeterministicScopeAuthority:
    exact = ScopeSet(
        frozenset(
            {
                ScopeTarget(
                    UUID(INTERNAL_ORGANIZATION_REF),
                    AUTHORIZED.source_ref,
                    AUTHORIZED.resource_ref,
                )
            }
        )
    )
    return DeterministicScopeAuthority(
        TrustedScopeOperands(
            organization_boundary=exact,
            membership_rights=exact,
            principal_grants=exact,
            agent_ceiling=exact,
            source_native_acl=exact,
            resource_acl=exact,
            purpose_policy=exact,
        )
    )


def _application(
    provider: _RecordingEmbeddingProvider,
    context_runs: RecordingContextRunPort,
    *,
    corrupt_tokenizer_digest: bool = False,
    mixed_generation_carrier: bool = False,
) -> FastAPI:
    materialized = _AuthorizedMaterializedPort()
    index_type = (
        _MixedGenerationVectorIndex
        if mixed_generation_carrier
        else PostgreSQLVectorCandidateIndex
    )
    index = index_type(provider, monotonic_ms=iter((25, 32)).__next__)
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=index,
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )
    return create_app(
        authenticator=DeterministicAuthenticator(),
        organization_authority=DeterministicOrganizationAuthority(),
        membership_authority=_V1MembershipAuthority(
            materialized,
            context_runs,
            corrupt_tokenizer_digest=corrupt_tokenizer_digest,
        ),
        scope_authority=_scope_authority(),
        runtime=runtime,
        clock=lambda: datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
        public_contract_version="v1",
    )


def _client(
    provider: _RecordingEmbeddingProvider,
    context_runs: RecordingContextRunPort,
    *,
    corrupt_tokenizer_digest: bool = False,
    mixed_generation_carrier: bool = False,
) -> TestClient:
    return TestClient(
        _application(
            provider,
            context_runs,
            corrupt_tokenizer_digest=corrupt_tokenizer_digest,
            mixed_generation_carrier=mixed_generation_carrier,
        )
    )


def _unused_port() -> int:
    with closing(socket.socket()) as listener:
        listener.bind(("127.0.0.1", 0))
        return cast(int, listener.getsockname()[1])


def _wait_for_tcp(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with closing(socket.socket()) as probe:
            probe.settimeout(0.1)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise AssertionError("live v1 SDK fixture did not become reachable")


@pytest.mark.security_evidence(id="ACCOUNTING-PACKAGE-RUN-BINDING-217", layer="runtime")
def test_v1_http_package_and_context_run_publish_one_digest_bound_usage() -> None:
    provider = _RecordingEmbeddingProvider()
    context_runs = RecordingContextRunPort()

    response = _client(provider, context_runs).post(
        "/v1/resolve",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "X-Context-Request-Id": "v1-accounting-request",
        },
        json={"kind": "acquire", "need": {"query": QUERY}},
    )

    assert response.status_code == 200
    package = response.json()["package"]
    assert package["tokenizerRef"] == RUNTIME_TOKENIZER_REF_V1
    assert (
        package["tokenizerProfileDigest"]
        == UNICODE_SCALAR_TOKENIZER_PROFILE.profile_digest
    )
    assert package["budgetUsage"] == {
        "tokens": len(QUERY) + 2_066 + len("A-safe"),
        "providerCalls": 1,
        "costMicrounits": 1,
        "elapsedMs": 7,
    }
    assert provider.calls == 1
    assert provider.bytes_sent == len(QUERY.encode("utf-8"))
    assert len(context_runs.calls) == 1
    run, audit = context_runs.calls[0]
    assert type(run) is ContextRunRecord
    assert audit is None
    assert (
        run.usage_tokens,
        run.usage_provider_calls,
        run.usage_cost_microunits,
        run.usage_elapsed_ms,
    ) == tuple(package["budgetUsage"].values())
    assert run.package_digest == package["packageDigest"]

    mutated = dict(package)
    mutated["budgetUsage"] = {**package["budgetUsage"], "tokens": 1}
    with pytest.raises(ValidationError, match="packageDigest"):
        ContextPackageV1Wire.model_validate(mutated)


@pytest.mark.security_evidence(id="ACCOUNTING-INGRESS-MISMATCH-217", layer="runtime")
def test_v1_http_tokenizer_digest_mismatch_refuses_before_provider_bytes() -> None:
    provider = _RecordingEmbeddingProvider()
    context_runs = RecordingContextRunPort()

    response = _client(
        provider,
        context_runs,
        corrupt_tokenizer_digest=True,
    ).post(
        "/v1/resolve",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "X-Context-Request-Id": "v1-tokenizer-mismatch",
        },
        json={"kind": "acquire", "need": {"query": QUERY}},
    )

    assert response.status_code == 503
    assert response.content == b'{"code":"service_unavailable"}'
    assert provider.calls == 0
    assert provider.bytes_sent == 0
    assert context_runs.calls == []


@pytest.mark.security_evidence(id="ACCOUNTING-MIXED-GENERATION-217", layer="runtime")
def test_v1_http_mixed_generation_refuses_before_provider_bytes() -> None:
    provider = _RecordingEmbeddingProvider()
    context_runs = RecordingContextRunPort()

    response = _client(
        provider,
        context_runs,
        mixed_generation_carrier=True,
    ).post(
        "/v1/resolve",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "X-Context-Request-Id": "v1-mixed-generation",
        },
        json={"kind": "acquire", "need": {"query": QUERY}},
    )

    assert response.status_code == 503
    assert response.content == b'{"code":"service_unavailable"}'
    assert provider.calls == 0
    assert provider.bytes_sent == 0
    assert context_runs.calls == []


def test_generated_v1_sdk_observes_cumulative_usage_over_live_http() -> None:
    provider = _RecordingEmbeddingProvider()
    context_runs = RecordingContextRunPort()
    port = _unused_port()
    server = Server(
        Config(
            _application(provider, context_runs),
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="off",
        )
    )
    server_thread = Thread(target=server.run, daemon=True)
    server_thread.start()
    try:
        _wait_for_tcp(port)
        result = subprocess.run(
            ["node", "test/live-empty-consumer.mjs"],
            cwd=ROOT / "sdk/typescript-v1",
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "CONTEXT_ENGINE_SDK_BASE_URL": f"http://127.0.0.1:{port}",
                "CONTEXT_ENGINE_SDK_QUERY": QUERY,
                "CONTEXT_ENGINE_SDK_REQUEST_ID": "v1-generated-sdk-accounting",
                "CONTEXT_ENGINE_SDK_TEST_AUTHENTICATION": VALID_TOKEN,
            },
            timeout=30,
        )
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)
        assert not server_thread.is_alive()

    assert result.returncode == 0, result.stdout + result.stderr
    outcome = json.loads(result.stdout)
    package = outcome["package"]
    assert package["budgetUsage"] == {
        "tokens": len(QUERY) + 2_066 + len("A-safe"),
        "providerCalls": 1,
        "costMicrounits": 1,
        "elapsedMs": 7,
    }
    assert package["tokenizerProfileDigest"] == (
        UNICODE_SCALAR_TOKENIZER_PROFILE.profile_digest
    )
    assert provider.calls == 1
    assert len(context_runs.calls) == 1
    assert context_runs.calls[0][0].package_digest == package["packageDigest"]
