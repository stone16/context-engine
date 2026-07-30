from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from sqlalchemy import Engine

from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.runtime.authorized_ranking import AuthorizedRerankItem
from engine.runtime.budget import PackageBudget, PackageBudgetMeter
from engine.runtime.egress import (
    EgressGrantRedemption,
    ModelEgressGrant,
    ModelEgressProfile,
)
from engine.runtime.evidence import AuthorizedProjection, CandidateRef
from engine.runtime.model_inference import (
    ModelInferenceEgressBinding,
    ModelInferenceOperation,
    ModelInferencePort,
    ModelInferenceProfile,
    ModelInferenceRetryPolicy,
    RerankModelRequest,
    RerankModelResult,
)
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


class _AcceptingModelAuthority:
    def __init__(self) -> None:
        self.redemptions: list[EgressGrantRedemption] = []

    def redeem(self, redemption: EgressGrantRedemption) -> bool:
        self.redemptions.append(redemption)
        return True


def _rerank_profile() -> ModelInferenceProfile:
    profile_ref = "runtime-rerank-http-seam-v1"
    return ModelInferenceProfile(
        profile_ref=profile_ref,
        profile_version=1,
        operation=ModelInferenceOperation.RERANK,
        egress_profile=ModelEgressProfile(
            profile_ref=profile_ref,
            retention_policy_ref="no-provider-retention-v1",
            sensitivity_policy_ref="authorized-runtime-input-v1",
            issuer_ref="context-runtime",
            consumer_ref="runtime-model-inference",
            provider_ref="deterministic-http-seam-twin",
            model_ref="rerank-http-seam-twin-v1",
            region_ref="local",
            maximum_ttl=timedelta(seconds=30),
        ),
        tokenizer_ref="utf8-byte-token-v1",
        maximum_input_tokens=4_096,
        maximum_output_tokens=64,
        maximum_input_items=8,
        maximum_output_items=8,
        maximum_provider_calls=1,
        maximum_cost_microunits=8_192,
        maximum_elapsed_ms=500,
        timeout_ms=250,
        retry_policy=ModelInferenceRetryPolicy(maximum_attempts=1),
        input_token_cost_microunits=1,
        output_token_cost_microunits=1,
    )


def test_http_candidate_port_seals_raw_refs_before_content_consumer(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    guarded_control_engine: Engine,
    guarded_operator_engine: Engine,
    query_digest_keyring: object,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
) -> None:
    """Real HTTP/PG proof: CandidateRef -> Kernel -> inference projection only."""

    fixture = _new_fixture()
    consumed: list[AuthorizedRerankItem] = []
    inference_results: list[RerankModelResult] = []
    authority = _AcceptingModelAuthority()
    profile = _rerank_profile()
    inference_port = ModelInferencePort(
        profiles=(profile,),
        authority=authority,
        gateway=lambda _payload, *, timeout_ms: b'{"order":[0]}',
        trace_observer=lambda _receipt: None,
        monotonic_ms=iter((100, 101)).__next__,
    )
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
        if not inference_results:
            inference_results.append(
                inference_port.rerank(
                    RerankModelRequest(
                        profile=profile,
                        query="public seam authorization proof",
                        projections=(projection,),
                    ),
                    grant=ModelEgressGrant("egrm_" + "1" * 64),
                    egress=ModelInferenceEgressBinding(
                        organization_id=projection.candidate_ref.organization_id,
                        package_digest="2" * 64,
                        purpose="context.answer",
                        audience_digest="3" * 64,
                        policy_epoch=1,
                    ),
                    budget=PackageBudgetMeter(
                        PackageBudget(
                            max_tokens=8_192,
                            max_provider_calls=1,
                            max_cost_microunits=8_192,
                            max_elapsed_ms=500,
                        )
                    ),
                )
            )

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
        assert consumed
        assert inference_results
        assert len(authority.redemptions) == 1
        assert inference_results[0].projections == (consumed[0].projection,)
        assert all(
            item.projection.candidate_ref == fixture.org_a.authorized
            and item.projection.projected_body == fixture.org_a.authorized_body
            for item in consumed
        )
        assert all(
            item.projection.candidate_ref
            not in {
                fixture.org_a.denied,
                fixture.org_b.authorized,
            }
            for item in consumed
        )
        record_property("candidate_port_http_seam", "PASS")
        record_property("model_inference_authorized_projection_seam", "PASS")
    finally:
        try:
            _cleanup_fixture(migration_engine, fixture)
        finally:
            migration_engine.dispose()
