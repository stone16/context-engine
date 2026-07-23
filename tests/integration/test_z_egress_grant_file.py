from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from adapters.exact_phrase import PostgreSQLExactPhraseCandidateIndex
from adapters.http.app import create_app
from bot_delivery.egress import (
    DeterministicModelGatewaySpy,
    ModelEgressBoundary,
    prepare_authorized_model_input,
)
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLEgressGrantRedemptionAuthority,
    PostgreSQLMembershipAuthority,
    create_database_engine,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.contracts import Resolved
from engine.runtime.egress import (
    EgressGrantNotAvailable,
    ModelEgressGrant,
    ModelEgressProfile,
    direct_egress_audience_digest,
)
from engine.runtime.package_digest import QueryDigestKeyring
from tests.integration.test_file_import_tracer import (
    NOW,
    _ExactScopeAuthority,
    _OrganizationAuthority,
    _prepare_file_import_scenario,
    _run_file_import,
    _RuntimeAuthenticator,
)

pytestmark = pytest.mark.integration


def _file_model_profile() -> ModelEgressProfile:
    return ModelEgressProfile(
        profile_ref="file-model-egress-integration-v1",
        retention_policy_ref="no-provider-retention-v1",
        sensitivity_policy_ref="authorized-package-only-v1",
        issuer_ref="context-runtime-integration",
        consumer_ref="model-gateway-integration",
        provider_ref="deterministic-provider-spy",
        model_ref="deterministic-model-spy",
        region_ref="local-test-region",
        maximum_ttl=timedelta(minutes=1),
    )


@pytest.mark.security_evidence(id="RUNTIME-EGRESS-011", layer="runtime")
def test_file_http_package_redeems_exact_model_grant_before_gateway_bytes(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    egress_configuration: DatabaseConfiguration,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
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
    egress_engine = create_database_engine(egress_configuration)
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
        observed: list[Resolved] = []
        response = TestClient(
            create_app(
                authenticator=_RuntimeAuthenticator(
                    scenario.organization_id,
                    user_id,
                    scenario.membership_id,
                ),
                organization_authority=_OrganizationAuthority(),
                membership_authority=PostgreSQLMembershipAuthority(
                    guarded_runtime_engine
                ),
                scope_authority=_ExactScopeAuthority(
                    published.candidate_ref.source_ref,
                    published.candidate_ref.resource_ref,
                ),
                runtime=Runtime(
                    required_kernel_dependencies(),
                    candidate_index=PostgreSQLExactPhraseCandidateIndex(),
                    egress_profile=_file_model_profile(),
                    clock=lambda: NOW,
                    query_digest_keyring=query_digest_keyring,
                ),
                resolution_observer=observed.append,
                clock=lambda: NOW,
                request_id_factory=lambda: "file-egress-http",
            )
        ).post(
            "/v1/context:resolve",
            headers={"Authorization": "Bearer runtime-secret"},
            json={
                "kind": "acquire",
                "need": {"query": "ContextEngine delivers context."},
            },
        )

        assert response.status_code == 200
        assert response.json()["package"]["blocks"][0]["text"] == (
            "ContextEngine delivers context."
        )
        wire_grant = response.json()["egressGrant"]
        assert wire_grant["kind"] == "model"
        assert len(observed) == 1
        outcome = observed[0]
        assert type(outcome.egress_grant) is ModelEgressGrant
        assert wire_grant["value"] == outcome.egress_grant.value

        audience_digest = direct_egress_audience_digest(
            organization_id=scenario.organization_id,
            membership_id=scenario.membership_id,
            membership_version=1,
            authenticated_application_ref="application:file-tracer",
            delivery_binding_ref="binding:file-tracer",
        )
        gateway = DeterministicModelGatewaySpy(_file_model_profile())
        boundary = ModelEgressBoundary(
            organization_id=scenario.organization_id,
            audience_digest=audience_digest,
            policy_epoch=1,
            profile=_file_model_profile(),
            authority=PostgreSQLEgressGrantRedemptionAuthority(egress_engine),
            gateway=gateway,
        )
        authorized = prepare_authorized_model_input(
            outcome.package,
            outcome.egress_grant,
        )

        boundary.transmit(authorized, outcome.egress_grant)

        assert gateway.request_count == 1
        assert gateway.outbound_bytes > 0
        with pytest.raises(EgressGrantNotAvailable, match="not available"):
            boundary.transmit(authorized, outcome.egress_grant)
        assert gateway.request_count == 1
    finally:
        with migration_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM egress_audit WHERE organization_id = :org"),
                {"org": scenario.organization_id},
            )
            connection.execute(
                text("DELETE FROM egress_grant WHERE organization_id = :org"),
                {"org": scenario.organization_id},
            )
        egress_engine.dispose()
        migration_engine.dispose()
