from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from fastapi.testclient import TestClient

from adapters.http.app import create_app


def _reachable_schemas(
    root: Mapping[str, object],
    schemas: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    pending: list[object] = [root]
    reachable: dict[str, Mapping[str, object]] = {}
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith(
                "#/components/schemas/"
            ):
                name = reference.rsplit("/", 1)[-1]
                if name not in reachable:
                    document = schemas[name]
                    assert isinstance(document, Mapping)
                    reachable[name] = document
                    pending.append(document)
            else:
                pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return reachable


def _response_schema_name(operation: Mapping[str, object], status: int) -> str:
    responses = operation["responses"]
    assert isinstance(responses, Mapping)
    response = responses[str(status)]
    assert isinstance(response, Mapping)
    content = response["content"]
    assert isinstance(content, Mapping)
    json_response = content["application/json"]
    assert isinstance(json_response, Mapping)
    schema = json_response["schema"]
    assert isinstance(schema, Mapping)
    reference = schema["$ref"]
    assert isinstance(reference, str)
    return reference.rsplit("/", 1)[-1]


def test_openapi_v0_exposes_one_versioned_resolve_operation() -> None:
    schema = TestClient(create_app()).get("/openapi.json").json()

    assert schema["info"]["version"] == "0.0.0"
    assert set(schema["paths"]) == {"/v0/resolve"}
    operation = schema["paths"]["/v0/resolve"]["post"]
    assert operation["operationId"] == "resolveContextV0"
    assert operation["security"] == [{"ContextEngineBearer": []}]
    assert schema["components"]["securitySchemes"]["ContextEngineBearer"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "opaque",
    }

    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert set(parameters) == {
        "X-Context-Request-Id",
        "X-Context-Delivery-Evidence-Ref",
    }
    assert parameters["X-Context-Request-Id"]["in"] == "header"
    assert parameters["X-Context-Request-Id"]["required"] is True
    assert parameters["X-Context-Delivery-Evidence-Ref"]["required"] is False
    evidence_ref_schema = parameters["X-Context-Delivery-Evidence-Ref"]["schema"]
    assert evidence_ref_schema["anyOf"][0]["pattern"] == r"^\S+$"

    assert set(operation["responses"]) == {
        "200",
        "400",
        "401",
        "403",
        "422",
        "429",
        "503",
    }
    expected_response_models = {
        200: "ResolutionOutcomeWire",
        400: "InvalidRequestWire",
        401: "AuthenticationFailureWire",
        403: "ApplicationForbiddenWire",
        422: "InvalidRequestWire",
        429: "RateLimitedWire",
        503: "ServiceUnavailableWire",
    }
    assert {
        status: _response_schema_name(operation, status)
        for status in expected_response_models
    } == expected_response_models


def test_openapi_v0_request_is_the_closed_untrusted_union() -> None:
    schema = TestClient(create_app()).get("/openapi.json").json()
    operation = schema["paths"]["/v0/resolve"]["post"]
    root = operation["requestBody"]["content"]["application/json"]["schema"]
    reachable = _reachable_schemas(root, schema["components"]["schemas"])

    assert set(reachable) == {
        "ResolveWire",
        "AcquireWire",
        "ContinueWire",
        "OpenCitationWire",
        "ContextNeedWire",
        "PackageBudgetWire",
        "RequestNarrowingWire",
    }
    assert set(cast(Mapping[str, object], reachable["AcquireWire"]["properties"])) == {
        "kind",
        "need",
        "packageBudget",
        "requestNarrowing",
    }
    assert set(cast(Mapping[str, object], reachable["ContinueWire"]["properties"])) == {
        "kind",
        "continuationToken",
        "packageBudget",
    }
    assert set(
        cast(Mapping[str, object], reachable["OpenCitationWire"]["properties"])
    ) == {
        "kind",
        "citationOpenRef",
    }
    assert all(
        document["additionalProperties"] is False
        for name, document in reachable.items()
        if name != "ResolveWire"
    )

    serialized = repr(reachable).casefold()
    for forbidden in (
        "organization",
        "tenant",
        "principal",
        "membership",
        "actorcontext",
        "purpose",
        "audiencesnapshot",
        "audiencemembers",
        "sourceaclevidence",
        "egressgrant",
        "sql",
        "filterbypass",
        "preauthorized",
        "authorizedprojection",
    ):
        assert forbidden not in serialized


def test_openapi_v0_freezes_the_complete_context_package() -> None:
    schema = TestClient(create_app()).get("/openapi.json").json()
    operation = schema["paths"]["/v0/resolve"]["post"]
    root = operation["responses"]["200"]["content"]["application/json"]["schema"]
    reachable = _reachable_schemas(root, schema["components"]["schemas"])

    assert set(reachable) == {
        "ResolutionOutcomeWire",
        "ResolvedWire",
        "RequestNotAvailableWire",
        "CitationNotAvailableWire",
        "ContextPackageWire",
        "BlockWire",
        "EvidenceWire",
        "SourceAclEvidenceWire",
        "LiveSourceAclEvidenceWire",
        "MirroredSourceAclEvidenceWire",
        "WeakSourceAclEvidenceWire",
        "GapWire",
        "BudgetUsageWire",
        "CoverageWire",
        "ContinuationOfferWire",
        "ModelEgressGrantWire",
        "ChannelEgressGrantWire",
    }
    package = cast(dict[str, Any], reachable["ContextPackageWire"])
    resolved = cast(dict[str, Any], reachable["ResolvedWire"])
    assert "egressGrant" in resolved["required"]
    assert package["required"] == [
        "packageId",
        "packageDigest",
        "purpose",
        "audienceDigest",
        "policyEpoch",
        "policySnapshotRef",
        "decisionRef",
        "runRef",
        "releaseManifestRef",
        "retentionPolicyRef",
        "asOf",
        "expiresAt",
        "ttlSeconds",
        "tokenizerRef",
        "packageSchemaRef",
        "blocks",
        "evidence",
        "gaps",
        "coverage",
        "budgetUsage",
        "continuation",
    ]
    assert package["properties"]["packageId"]["pattern"] == (r"^pkg_[0-9a-f]{32}$")
    assert "organizationRef" not in package["properties"]
    assert package["properties"]["audienceDigest"]["pattern"] == (r"^[0-9a-f]{64}$")

    evidence = cast(dict[str, Any], reachable["EvidenceWire"])
    assert set(evidence["properties"]) == {
        "evidenceRef",
        "sourceRef",
        "resourceRef",
        "revisionRef",
        "fragmentRef",
        "projectedFields",
        "runRef",
        "purpose",
        "authorizationAsOf",
        "decisionRef",
        "policySnapshotRef",
        "policyEpoch",
        "sourceAclEvidence",
        "citationOpenRef",
    }
    source_acl = cast(dict[str, Any], reachable["SourceAclEvidenceWire"])
    assert set(source_acl["discriminator"]["mapping"]) == {
        "live",
        "mirrored",
        "weak",
    }
    coverage = cast(dict[str, Any], reachable["CoverageWire"])
    assert coverage["properties"]["status"]["enum"] == [
        "empty",
        "partial",
        "sufficient",
    ]
    gap = cast(dict[str, Any], reachable["GapWire"])
    assert gap["properties"]["category"]["enum"] == [
        "source_unavailable",
        "stale_evidence",
        "budget_exhausted",
        "capability_unsupported",
    ]
    budget_usage = cast(dict[str, Any], reachable["BudgetUsageWire"])
    for field_name in (
        "tokens",
        "providerCalls",
        "costMicrounits",
        "elapsedMs",
    ):
        assert budget_usage["properties"][field_name]["minimum"] == 0
        assert "const" not in budget_usage["properties"][field_name]

    serialized = repr(reachable).casefold()
    for forbidden in (
        "organizationid",
        "principalref",
        "userid",
        "membershipid",
        "authenticatedinvocation",
        "trusteddeliverycontext",
        "candidate",
        "denied",
    ):
        assert forbidden not in serialized


def test_openapi_generation_is_deterministic_in_process() -> None:
    first = create_app().openapi()
    second = create_app().openapi()

    assert first == second
