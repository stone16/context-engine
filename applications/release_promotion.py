"""Local operator composition for explicit dogfood Release promotion."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from applications.operator_authentication import (
    CONTROL_OPERATOR_SECRET_ENV,
    DOGFOOD_SECRET_ENV,
    RELEASE_OPERATOR_SECRET_ENV,
    WORKER_SECRET_ENV,
    LocalOperatorAuthorities,
    LocalOperatorConfiguration,
    LocalReleaseOperatorAuthenticator,
)
from engine.learning import (
    ContentProfileRef,
    ContextLearning,
    CurationProfileRef,
    Gate,
    GateEvidence,
    GateStatus,
    IndexProfileRef,
    PromotionAuthorizationRequest,
    ReleaseCandidate,
    ReleaseEvaluationKeyring,
    ReleaseManifest,
    RuntimeProfileRef,
)
from engine.persistence import (
    DatabasePurpose,
    PostgreSQLReleaseCandidateSnapshotStore,
    PostgreSQLReleaseStore,
    create_database_engine,
    load_database_configuration,
)
from engine.runtime.release_lineage import (
    CONTENT_PROFILE_DIGEST_V0,
    CONTENT_PROFILE_REF_V0,
    CONTENT_SCHEMA_REF_V0,
    CURATION_PROFILE_DIGEST_V0,
    CURATION_PROFILE_REF_V0,
    DOGFOOD_VECTOR_INDEX_PROFILE_DIGEST_V1,
    DOGFOOD_VECTOR_INDEX_PROFILE_REF_V1,
    INDEX_SCHEMA_REF_V0,
    PACKAGE_SCHEMA_REF_V0,
    RUNTIME_PROFILE_DIGEST_V0,
    RUNTIME_PROFILE_REF_V0,
    RUNTIME_TOKENIZER_REF_V0,
)

RELEASE_EVALUATION_SIGNING_KEY_VERSION_ENV = (
    "CONTEXT_ENGINE_RELEASE_EVALUATION_SIGNING_KEY_VERSION"
)
RELEASE_EVALUATION_SIGNING_KEY_ENV = "CONTEXT_ENGINE_RELEASE_EVALUATION_SIGNING_KEY_HEX"


class ReleasePromotionConfigurationUnavailable(ValueError):
    """The local release candidate or signing configuration is unavailable."""


@dataclass(frozen=True, slots=True)
class ReleasePromotionReport:
    """Content-free operator confirmation of one committed release activation."""

    active_generation: int
    active_revision_count: int
    index_profile_ref: str
    manifest_ref: str


@dataclass(frozen=True, slots=True)
class _ReleaseEvidence:
    gate_evidence: tuple[GateEvidence, ...]
    capability_coverage_digest: str
    fixture_digest: str
    verification_commands: tuple[str, ...]

    def lineage_digest(self) -> str:
        """Bind a candidate reference to its complete normalized evidence."""

        return _digest(
            json.dumps(
                {
                    "capabilityCoverageDigest": self.capability_coverage_digest,
                    "fixtureDigest": self.fixture_digest,
                    "gates": [
                        {
                            "evidenceDigest": item.evidence_digest,
                            "gate": item.gate.value,
                            "status": item.status.value,
                        }
                        for item in self.gate_evidence
                    ],
                    "verificationCommands": list(self.verification_commands),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _keyring(environment: Mapping[str, str]) -> ReleaseEvaluationKeyring:
    try:
        raw_version = environment[RELEASE_EVALUATION_SIGNING_KEY_VERSION_ENV]
        if not raw_version.isdecimal():
            raise ValueError
        version = int(raw_version)
        raw_key = environment[RELEASE_EVALUATION_SIGNING_KEY_ENV]
        if len(raw_key) != 64:
            raise ValueError
        key = bytes.fromhex(raw_key)
        if len(key) != 32:
            raise ValueError
        operator_secrets = (
            environment[CONTROL_OPERATOR_SECRET_ENV],
            environment[RELEASE_OPERATOR_SECRET_ENV],
            environment[DOGFOOD_SECRET_ENV],
        )
        worker_secret = environment[WORKER_SECRET_ENV]
        worker_key = bytes.fromhex(worker_secret)
        if any(
            hmac.compare_digest(raw_key, secret)
            or hmac.compare_digest(key, secret.encode("utf-8"))
            for secret in operator_secrets
        ) or hmac.compare_digest(raw_key, worker_secret) or hmac.compare_digest(
            key, worker_key
        ):
            raise ValueError
        return ReleaseEvaluationKeyring(
            active_version=version,
            keys={version: key},
        )
    except (KeyError, TypeError, ValueError):
        raise ReleasePromotionConfigurationUnavailable from None


def _digest_value(document: Mapping[str, Any], key: str) -> str:
    value = document[key]
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ReleasePromotionConfigurationUnavailable
    return value


def _evidence(path: Path) -> _ReleaseEvidence:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if type(raw) is not dict:
            raise ReleasePromotionConfigurationUnavailable
        document = cast(dict[str, Any], raw)
        expected_keys = {
            *(gate.value for gate in Gate),
            "capabilityCoverageDigest",
            "fixtureDigest",
            "verificationCommands",
        }
        if set(document) != expected_keys:
            raise ReleasePromotionConfigurationUnavailable
        gate_evidence = tuple(_gate_evidence(document, gate) for gate in Gate)
        commands = document["verificationCommands"]
        if type(commands) is not list:
            raise ReleasePromotionConfigurationUnavailable
        return _ReleaseEvidence(
            gate_evidence=gate_evidence,
            capability_coverage_digest=_digest_value(
                document,
                "capabilityCoverageDigest",
            ),
            fixture_digest=_digest_value(document, "fixtureDigest"),
            verification_commands=tuple(commands),
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        raise ReleasePromotionConfigurationUnavailable from None


def _gate_evidence(document: Mapping[str, Any], gate: Gate) -> GateEvidence:
    raw_gate = document[gate.value]
    if type(raw_gate) is not dict or set(raw_gate) != {"evidenceDigest", "status"}:
        raise ReleasePromotionConfigurationUnavailable
    gate_document = cast(dict[str, Any], raw_gate)
    return GateEvidence(
        gate=gate,
        status=GateStatus(gate_document["status"]),
        evidence_digest=_digest_value(gate_document, "evidenceDigest"),
    )


def _manifest(
    organization_id: UUID,
    active_revision_refs: tuple[str, ...],
) -> ReleaseManifest:
    content = ContentProfileRef(
        profile_ref=CONTENT_PROFILE_REF_V0,
        profile_digest=CONTENT_PROFILE_DIGEST_V0,
        content_schema_ref=CONTENT_SCHEMA_REF_V0,
    )
    index = IndexProfileRef(
        profile_ref=DOGFOOD_VECTOR_INDEX_PROFILE_REF_V1,
        profile_digest=DOGFOOD_VECTOR_INDEX_PROFILE_DIGEST_V1,
        content_profile_digest=content.profile_digest,
        content_schema_ref=content.content_schema_ref,
        index_schema_ref=INDEX_SCHEMA_REF_V0,
    )
    runtime = RuntimeProfileRef(
        profile_ref=RUNTIME_PROFILE_REF_V0,
        profile_digest=RUNTIME_PROFILE_DIGEST_V0,
        content_profile_digest=content.profile_digest,
        index_profile_digest=index.profile_digest,
        content_schema_ref=content.content_schema_ref,
        index_schema_ref=index.index_schema_ref,
        tokenizer_ref=RUNTIME_TOKENIZER_REF_V0,
        package_schema_ref=PACKAGE_SCHEMA_REF_V0,
    )
    lineage = "\x00".join(active_revision_refs)
    return ReleaseManifest(
        organization_id=organization_id,
        manifest_ref=f"manifest-dogfood-{_digest(lineage)}",
        content_profile=content,
        index_profile=index,
        runtime_profile=runtime,
        curation_profile=CurationProfileRef.off(
            profile_ref=CURATION_PROFILE_REF_V0,
            profile_digest=CURATION_PROFILE_DIGEST_V0,
        ),
        active_revision_refs=active_revision_refs,
    )


def promote_release(
    *,
    organization_id: UUID,
    evidence_file: Path,
    configuration: LocalOperatorConfiguration,
    authorities: LocalOperatorAuthorities,
) -> ReleasePromotionReport:
    """Evaluate and promote the exact active corpus through ContextLearning."""

    if type(organization_id) is not UUID:
        raise ReleasePromotionConfigurationUnavailable
    if type(authorities) is not LocalOperatorAuthorities:
        raise ReleasePromotionConfigurationUnavailable
    if (
        type(configuration) is not LocalOperatorConfiguration
        or configuration.organization_id != organization_id
    ):
        raise ReleasePromotionConfigurationUnavailable
    opaque_credential = os.environ[RELEASE_OPERATOR_SECRET_ENV]
    release_identity = LocalReleaseOperatorAuthenticator(
        configuration,
        clock=lambda: datetime.now(UTC),
    ).authenticate(opaque_credential)
    evidence = _evidence(evidence_file)
    keyring = _keyring(os.environ)
    learning_engine = create_database_engine(
        load_database_configuration(DatabasePurpose.LEARNING)
    )
    snapshot_engine = create_database_engine(
        load_database_configuration(DatabasePurpose.RELEASE_OPERATOR)
    )

    def clock() -> datetime:
        return datetime.now(UTC)

    try:
        store = PostgreSQLReleaseStore(learning_engine)
        snapshot = PostgreSQLReleaseCandidateSnapshotStore(
            snapshot_engine
        ).observe_candidate_snapshot(
            organization_id,
            release_identity,
        )
        if not snapshot.active_revision_refs:
            raise ReleasePromotionConfigurationUnavailable
        manifest = _manifest(organization_id, snapshot.active_revision_refs)
        candidate = ReleaseCandidate(
            organization_id=organization_id,
            candidate_ref=(
                "candidate-dogfood-"
                f"{snapshot.expected_active_generation}-"
                f"{manifest.manifest_digest}-{evidence.lineage_digest()}"
            ),
            manifest=manifest,
            expected_active_generation=snapshot.expected_active_generation,
            expected_base_manifest_digest=(snapshot.expected_base_manifest_digest),
            gate_evidence=evidence.gate_evidence,
            capability_coverage_digest=evidence.capability_coverage_digest,
            fixture_digest=evidence.fixture_digest,
            verification_commands=evidence.verification_commands,
        )
        learning = ContextLearning(
            store=store,
            evaluation_keyring=keyring,
            promotion_authority=authorities.release,
            clock=clock,
        )
        store.persist_candidate(candidate)
        evaluation = learning.evaluate(candidate.reference())
        authorization = PromotionAuthorizationRequest(
            organization_id=organization_id,
            promotion_ref=(
                "promotion-dogfood-"
                f"{snapshot.expected_active_generation + 1}-{manifest.manifest_digest}"
            ),
            candidate=candidate,
            evaluation=evaluation,
            request_id=f"local-promote-release-{uuid4().hex}",
            audit_reason="activate exact current dogfood File corpus",
            opaque_credential=opaque_credential,
        )
        with authorities.release.authorize(authorization) as call:
            receipt = learning.promote(call)
        return ReleasePromotionReport(
            active_generation=receipt.active_generation,
            active_revision_count=len(snapshot.active_revision_refs),
            index_profile_ref=manifest.index_profile.profile_ref,
            manifest_ref=receipt.manifest_ref,
        )
    finally:
        snapshot_engine.dispose()
        learning_engine.dispose()


def release_report_json(report: ReleasePromotionReport) -> str:
    """Render the stable content-free promotion report."""

    if type(report) is not ReleasePromotionReport:
        raise ReleasePromotionConfigurationUnavailable
    return json.dumps(
        {
            "activeGeneration": report.active_generation,
            "activeRevisionCount": report.active_revision_count,
            "indexProfileRef": report.index_profile_ref,
            "manifestRef": report.manifest_ref,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = [
    "RELEASE_EVALUATION_SIGNING_KEY_ENV",
    "RELEASE_EVALUATION_SIGNING_KEY_VERSION_ENV",
    "ReleasePromotionConfigurationUnavailable",
    "ReleasePromotionReport",
    "promote_release",
    "release_report_json",
]
