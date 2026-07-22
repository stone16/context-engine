"""PostgreSQL persistence for immutable release lineage and atomic promotion."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import Transaction
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from engine.learning import (
    ContentProfileRef,
    CurationMode,
    CurationProfileRef,
    Gate,
    GateEvidence,
    GateStatus,
    IndexProfileRef,
    PromotionCommit,
    ReleaseCandidate,
    ReleaseCandidateRef,
    ReleaseEvaluation,
    ReleaseEvaluationKeyring,
    ReleaseEvaluationUnavailable,
    ReleaseManifest,
    ReleasePromotionRejected,
    ReleasePromotionTransactionPort,
    ReleasePromotionUnavailable,
    RuntimeProfileRef,
    TrustedPromotionCall,
    verify_release_candidate,
    verify_release_evaluation,
    verify_release_manifest,
)
from engine.persistence.role_guard import assert_learning_role


def _gate_parameters(evidence: tuple[GateEvidence, ...]) -> dict[str, str]:
    return {
        f"{item.gate.value}_{suffix}": value
        for item in evidence
        for suffix, value in (
            ("status", item.status.value),
            ("evidence_digest", item.evidence_digest),
        )
    }


def _bind_organization(connection: Connection, organization_id: UUID) -> None:
    expected = str(organization_id)
    observed = connection.execute(
        text(
            "SELECT set_config('app.organization_id', :organization_id, true)"
        ),
        {"organization_id": expected},
    ).scalar_one()
    if observed != expected:
        raise ReleasePromotionUnavailable(
            "ContextLearning Organization context binding failed"
        )


def _manifest_parameters(manifest: ReleaseManifest) -> dict[str, object]:
    curation = manifest.curation_profile
    return {
        "organization_id": manifest.organization_id,
        "manifest_ref": manifest.manifest_ref,
        "manifest_digest": manifest.manifest_digest,
        "lineage_digest": manifest.lineage_digest,
        "content_profile_ref": manifest.content_profile.profile_ref,
        "content_profile_digest": manifest.content_profile.profile_digest,
        "content_schema_ref": manifest.content_profile.content_schema_ref,
        "index_profile_ref": manifest.index_profile.profile_ref,
        "index_profile_digest": manifest.index_profile.profile_digest,
        "index_content_profile_digest": (
            manifest.index_profile.content_profile_digest
        ),
        "index_content_schema_ref": manifest.index_profile.content_schema_ref,
        "index_schema_ref": manifest.index_profile.index_schema_ref,
        "runtime_profile_ref": manifest.runtime_profile.profile_ref,
        "runtime_profile_digest": manifest.runtime_profile.profile_digest,
        "runtime_content_profile_digest": (
            manifest.runtime_profile.content_profile_digest
        ),
        "runtime_index_profile_digest": (
            manifest.runtime_profile.index_profile_digest
        ),
        "runtime_content_schema_ref": manifest.runtime_profile.content_schema_ref,
        "runtime_index_schema_ref": manifest.runtime_profile.index_schema_ref,
        "runtime_tokenizer_ref": manifest.runtime_profile.tokenizer_ref,
        "runtime_package_schema_ref": manifest.runtime_profile.package_schema_ref,
        "curation_profile_ref": curation.profile_ref,
        "curation_profile_digest": curation.profile_digest,
        "curation_mode": curation.mode.value,
        "curation_snapshot_ref": curation.curation_snapshot_ref,
        "compatible_revision_refs": json.dumps(
            curation.compatible_revision_refs,
            separators=(",", ":"),
        ),
        "curation_evaluation_digest": curation.evaluation_digest,
        "active_revision_refs": json.dumps(
            manifest.active_revision_refs,
            separators=(",", ":"),
        ),
    }


def _evaluation_parameters(evaluation: ReleaseEvaluation) -> dict[str, object]:
    return {
        "organization_id": evaluation.organization_id,
        "evaluation_ref": evaluation.evaluation_ref,
        "evaluation_digest": evaluation.evaluation_digest,
        "candidate_ref": evaluation.candidate_ref,
        "candidate_digest": evaluation.candidate_digest,
        "manifest_ref": evaluation.manifest_ref,
        "manifest_digest": evaluation.manifest_digest,
        "expected_active_generation": evaluation.expected_active_generation,
        "expected_base_manifest_digest": evaluation.expected_base_manifest_digest,
        "compatibility_passed": evaluation.compatibility_passed,
        "compatibility_evidence_digest": (
            evaluation.compatibility_evidence_digest
        ),
        "capability_coverage_digest": evaluation.capability_coverage_digest,
        "fixture_digest": evaluation.fixture_digest,
        "verification_commands": json.dumps(
            evaluation.verification_commands,
            separators=(",", ":"),
        ),
        "evaluated_at": evaluation.evaluated_at,
        "digest_profile": evaluation.digest_profile,
        "signature_profile": evaluation.signature_profile,
        "signing_key_version": evaluation.signing_key_version,
        "signature": evaluation.signature,
        **_gate_parameters(evaluation.gate_evidence),
    }


_INSERT_MANIFEST = text(
    """
    INSERT INTO public.release_manifest (
        organization_id, manifest_ref, manifest_digest, lineage_digest,
        content_profile_ref, content_profile_digest, content_schema_ref,
        index_profile_ref, index_profile_digest, index_content_profile_digest,
        index_content_schema_ref, index_schema_ref,
        runtime_profile_ref, runtime_profile_digest,
        runtime_content_profile_digest, runtime_index_profile_digest,
        runtime_content_schema_ref, runtime_index_schema_ref,
        runtime_tokenizer_ref, runtime_package_schema_ref,
        curation_profile_ref, curation_profile_digest, curation_mode,
        curation_snapshot_ref, compatible_revision_refs,
        curation_evaluation_digest, active_revision_refs
    ) VALUES (
        :organization_id, :manifest_ref, :manifest_digest, :lineage_digest,
        :content_profile_ref, :content_profile_digest, :content_schema_ref,
        :index_profile_ref, :index_profile_digest,
        :index_content_profile_digest, :index_content_schema_ref,
        :index_schema_ref, :runtime_profile_ref, :runtime_profile_digest,
        :runtime_content_profile_digest, :runtime_index_profile_digest,
        :runtime_content_schema_ref, :runtime_index_schema_ref,
        :runtime_tokenizer_ref, :runtime_package_schema_ref,
        :curation_profile_ref, :curation_profile_digest, :curation_mode,
        :curation_snapshot_ref, CAST(:compatible_revision_refs AS jsonb),
        :curation_evaluation_digest, CAST(:active_revision_refs AS jsonb)
    )
    ON CONFLICT (organization_id, manifest_ref) DO NOTHING
    """
)


_INSERT_CANDIDATE = text(
    """
    INSERT INTO public.release_candidate (
        organization_id, candidate_ref, candidate_digest,
        manifest_ref, manifest_digest, expected_active_generation,
        expected_base_manifest_digest,
        security_status, security_evidence_digest,
        reliability_status, reliability_evidence_digest,
        quality_status, quality_evidence_digest,
        budget_status, budget_evidence_digest,
        capability_coverage_digest, fixture_digest, verification_commands
    ) VALUES (
        :organization_id, :candidate_ref, :candidate_digest,
        :manifest_ref, :manifest_digest, :expected_active_generation,
        :expected_base_manifest_digest,
        :security_status, :security_evidence_digest,
        :reliability_status, :reliability_evidence_digest,
        :quality_status, :quality_evidence_digest,
        :budget_status, :budget_evidence_digest,
        :capability_coverage_digest, :fixture_digest,
        CAST(:verification_commands AS jsonb)
    )
    ON CONFLICT (organization_id, candidate_ref) DO NOTHING
    """
)


_LOAD_CANDIDATE = text(
    """
    SELECT
        candidate.*,
        manifest.lineage_digest,
        manifest.content_profile_ref,
        manifest.content_profile_digest,
        manifest.content_schema_ref,
        manifest.index_profile_ref,
        manifest.index_profile_digest,
        manifest.index_content_profile_digest,
        manifest.index_content_schema_ref,
        manifest.index_schema_ref,
        manifest.runtime_profile_ref,
        manifest.runtime_profile_digest,
        manifest.runtime_content_profile_digest,
        manifest.runtime_index_profile_digest,
        manifest.runtime_content_schema_ref,
        manifest.runtime_index_schema_ref,
        manifest.runtime_tokenizer_ref,
        manifest.runtime_package_schema_ref,
        manifest.curation_profile_ref,
        manifest.curation_profile_digest,
        manifest.curation_mode,
        manifest.curation_snapshot_ref,
        manifest.compatible_revision_refs,
        manifest.curation_evaluation_digest,
        manifest.active_revision_refs
    FROM public.release_candidate AS candidate
    JOIN public.release_manifest AS manifest
      ON manifest.organization_id = candidate.organization_id
     AND manifest.manifest_ref = candidate.manifest_ref
     AND manifest.manifest_digest = candidate.manifest_digest
    WHERE candidate.organization_id = :organization_id
      AND candidate.candidate_ref = :candidate_ref
      AND candidate.candidate_digest = :candidate_digest
    """
)


_LOAD_EVALUATION = text(
    """
    SELECT *
    FROM public.release_evaluation
    WHERE organization_id = :organization_id
      AND evaluation_ref = :evaluation_ref
    """
)


def _evaluation_from_row(row: dict[str, Any]) -> ReleaseEvaluation:
    gate_evidence = tuple(
        GateEvidence(
            gate=gate,
            status=GateStatus(row[f"{gate.value}_status"]),
            evidence_digest=row[f"{gate.value}_evidence_digest"],
        )
        for gate in Gate
    )
    evaluation = ReleaseEvaluation(
        organization_id=row["organization_id"],
        candidate_ref=row["candidate_ref"],
        candidate_digest=row["candidate_digest"],
        manifest_ref=row["manifest_ref"],
        manifest_digest=row["manifest_digest"],
        expected_active_generation=row["expected_active_generation"],
        expected_base_manifest_digest=row["expected_base_manifest_digest"],
        gate_evidence=gate_evidence,
        compatibility_passed=row["compatibility_passed"],
        compatibility_evidence_digest=row["compatibility_evidence_digest"],
        capability_coverage_digest=row["capability_coverage_digest"],
        fixture_digest=row["fixture_digest"],
        verification_commands=tuple(row["verification_commands"]),
        evaluated_at=row["evaluated_at"],
        signing_key_version=row["signing_key_version"],
        signature=bytes(row["signature"]),
    )
    if (
        evaluation.evaluation_ref != row["evaluation_ref"]
        or evaluation.evaluation_digest != row["evaluation_digest"]
        or evaluation.digest_profile != row["digest_profile"]
        or evaluation.signature_profile != row["signature_profile"]
    ):
        raise ReleaseEvaluationUnavailable(
            "persisted ReleaseEvaluation digest does not match its lineage"
        )
    return evaluation


def _load_evaluation(
    connection: Connection,
    *,
    organization_id: UUID,
    evaluation_ref: str,
) -> ReleaseEvaluation:
    row = connection.execute(
        _LOAD_EVALUATION,
        {
            "organization_id": organization_id,
            "evaluation_ref": evaluation_ref,
        },
    ).mappings().one_or_none()
    if row is None:
        raise ReleaseEvaluationUnavailable("release evaluation is unavailable")
    try:
        return _evaluation_from_row(dict(row))
    except ReleaseEvaluationUnavailable:
        raise
    except (KeyError, TypeError, ValueError):
        raise ReleaseEvaluationUnavailable(
            "persisted release evaluation is invalid"
        ) from None


def _candidate_from_row(row: dict[str, Any]) -> ReleaseCandidate:
    content_profile = ContentProfileRef(
        profile_ref=row["content_profile_ref"],
        profile_digest=row["content_profile_digest"],
        content_schema_ref=row["content_schema_ref"],
    )
    index_profile = IndexProfileRef(
        profile_ref=row["index_profile_ref"],
        profile_digest=row["index_profile_digest"],
        content_profile_digest=row["index_content_profile_digest"],
        content_schema_ref=row["index_content_schema_ref"],
        index_schema_ref=row["index_schema_ref"],
    )
    runtime_profile = RuntimeProfileRef(
        profile_ref=row["runtime_profile_ref"],
        profile_digest=row["runtime_profile_digest"],
        content_profile_digest=row["runtime_content_profile_digest"],
        index_profile_digest=row["runtime_index_profile_digest"],
        content_schema_ref=row["runtime_content_schema_ref"],
        index_schema_ref=row["runtime_index_schema_ref"],
        tokenizer_ref=row["runtime_tokenizer_ref"],
        package_schema_ref=row["runtime_package_schema_ref"],
    )
    mode = CurationMode(row["curation_mode"])
    if mode is CurationMode.OFF:
        curation_profile = CurationProfileRef.off(
            profile_ref=row["curation_profile_ref"],
            profile_digest=row["curation_profile_digest"],
        )
    else:
        curation_profile = CurationProfileRef.on(
            profile_ref=row["curation_profile_ref"],
            profile_digest=row["curation_profile_digest"],
            curation_snapshot_ref=row["curation_snapshot_ref"],
            compatible_revision_refs=tuple(row["compatible_revision_refs"]),
            evaluation_digest=row["curation_evaluation_digest"],
        )
    manifest = ReleaseManifest(
        organization_id=row["organization_id"],
        manifest_ref=row["manifest_ref"],
        content_profile=content_profile,
        index_profile=index_profile,
        runtime_profile=runtime_profile,
        curation_profile=curation_profile,
        active_revision_refs=tuple(row["active_revision_refs"]),
    )
    if (
        manifest.manifest_digest != row["manifest_digest"]
        or manifest.lineage_digest != row["lineage_digest"]
        or not verify_release_manifest(manifest)
    ):
        raise ReleaseEvaluationUnavailable(
            "persisted ReleaseManifest digest does not match its lineage"
        )
    gate_evidence = tuple(
        GateEvidence(
            gate=gate,
            status=GateStatus(row[f"{gate.value}_status"]),
            evidence_digest=row[f"{gate.value}_evidence_digest"],
        )
        for gate in Gate
    )
    candidate = ReleaseCandidate(
        organization_id=row["organization_id"],
        candidate_ref=row["candidate_ref"],
        manifest=manifest,
        expected_active_generation=row["expected_active_generation"],
        expected_base_manifest_digest=row["expected_base_manifest_digest"],
        gate_evidence=gate_evidence,
        capability_coverage_digest=row["capability_coverage_digest"],
        fixture_digest=row["fixture_digest"],
        verification_commands=tuple(row["verification_commands"]),
    )
    if candidate.candidate_digest != row["candidate_digest"]:
        raise ReleaseEvaluationUnavailable(
            "persisted ReleaseCandidate digest does not match its lineage"
        )
    return candidate


def _load_candidate(
    connection: Connection,
    candidate_ref: ReleaseCandidateRef,
) -> ReleaseCandidate:
    row = connection.execute(
        _LOAD_CANDIDATE,
        {
            "organization_id": candidate_ref.organization_id,
            "candidate_ref": candidate_ref.candidate_ref,
            "candidate_digest": candidate_ref.candidate_digest,
        },
    ).mappings().one_or_none()
    if row is None:
        raise ReleaseEvaluationUnavailable(
            "release candidate is unavailable"
        )
    try:
        candidate = _candidate_from_row(dict(row))
    except ReleaseEvaluationUnavailable:
        raise
    except (KeyError, TypeError, ValueError):
        raise ReleaseEvaluationUnavailable(
            "persisted release candidate is invalid"
        ) from None
    if (
        candidate.reference() != candidate_ref
        or not verify_release_candidate(candidate)
    ):
        raise ReleaseEvaluationUnavailable(
            "release candidate reference does not match persisted lineage"
        )
    return candidate


class PostgreSQLReleaseStore:
    """Immutable release store with one retained promotion transaction seam."""

    def __init__(self, engine: Engine) -> None:
        if not isinstance(engine, Engine):
            raise TypeError("PostgreSQLReleaseStore requires a SQLAlchemy Engine")
        self._engine = engine

    def persist_candidate(self, candidate: ReleaseCandidate) -> None:
        """Persist one exact manifest and candidate without publication access."""

        if type(candidate) is not ReleaseCandidate or not verify_release_candidate(
            candidate
        ):
            raise TypeError("persist_candidate requires a valid ReleaseCandidate")
        try:
            with self._engine.begin() as connection:
                assert_learning_role(connection)
                _bind_organization(connection, candidate.organization_id)
                connection.execute(
                    _INSERT_MANIFEST,
                    _manifest_parameters(candidate.manifest),
                )
                parameters: dict[str, object] = {
                    "organization_id": candidate.organization_id,
                    "candidate_ref": candidate.candidate_ref,
                    "candidate_digest": candidate.candidate_digest,
                    "manifest_ref": candidate.manifest.manifest_ref,
                    "manifest_digest": candidate.manifest.manifest_digest,
                    "expected_active_generation": candidate.expected_active_generation,
                    "expected_base_manifest_digest": (
                        candidate.expected_base_manifest_digest
                    ),
                    "capability_coverage_digest": (
                        candidate.capability_coverage_digest
                    ),
                    "fixture_digest": candidate.fixture_digest,
                    "verification_commands": json.dumps(
                        candidate.verification_commands,
                        separators=(",", ":"),
                    ),
                    **_gate_parameters(candidate.gate_evidence),
                }
                connection.execute(_INSERT_CANDIDATE, parameters)
                persisted = _load_candidate(connection, candidate.reference())
                if persisted != candidate:
                    raise ReleaseEvaluationUnavailable(
                        "immutable release candidate reference collision"
                    )
        except ReleaseEvaluationUnavailable:
            raise
        except (AssertionError, SQLAlchemyError) as error:
            raise ReleaseEvaluationUnavailable(
                "release candidate persistence is unavailable"
            ) from error

    def load_candidate(
        self,
        candidate_ref: ReleaseCandidateRef,
    ) -> ReleaseCandidate:
        if type(candidate_ref) is not ReleaseCandidateRef:
            raise TypeError("load_candidate requires ReleaseCandidateRef")
        try:
            with self._engine.begin() as connection:
                assert_learning_role(connection)
                _bind_organization(connection, candidate_ref.organization_id)
                return _load_candidate(connection, candidate_ref)
        except ReleaseEvaluationUnavailable:
            raise
        except (AssertionError, SQLAlchemyError) as error:
            raise ReleaseEvaluationUnavailable(
                "release candidate loading is unavailable"
            ) from error

    def persist_evaluation(self, evaluation: ReleaseEvaluation) -> None:
        if type(evaluation) is not ReleaseEvaluation:
            raise TypeError("persist_evaluation requires ReleaseEvaluation")
        parameters = _evaluation_parameters(evaluation)
        try:
            with self._engine.begin() as connection:
                assert_learning_role(connection)
                _bind_organization(connection, evaluation.organization_id)
                result = connection.execute(
                    text(
                        """
                        INSERT INTO public.release_evaluation (
                            organization_id, evaluation_ref, evaluation_digest,
                            candidate_ref, candidate_digest,
                            manifest_ref, manifest_digest,
                            expected_active_generation,
                            expected_base_manifest_digest,
                            security_status, security_evidence_digest,
                            reliability_status, reliability_evidence_digest,
                            quality_status, quality_evidence_digest,
                            budget_status, budget_evidence_digest,
                            compatibility_passed,
                            compatibility_evidence_digest,
                            capability_coverage_digest, fixture_digest,
                            verification_commands, evaluated_at,
                            digest_profile, signature_profile,
                            signing_key_version, signature
                        ) VALUES (
                            :organization_id, :evaluation_ref,
                            :evaluation_digest, :candidate_ref,
                            :candidate_digest, :manifest_ref, :manifest_digest,
                            :expected_active_generation,
                            :expected_base_manifest_digest,
                            :security_status, :security_evidence_digest,
                            :reliability_status, :reliability_evidence_digest,
                            :quality_status, :quality_evidence_digest,
                            :budget_status, :budget_evidence_digest,
                            :compatibility_passed,
                            :compatibility_evidence_digest,
                            :capability_coverage_digest, :fixture_digest,
                            CAST(:verification_commands AS jsonb), :evaluated_at,
                            :digest_profile, :signature_profile,
                            :signing_key_version, :signature
                        )
                        ON CONFLICT (organization_id, evaluation_ref) DO NOTHING
                        """
                    ),
                    parameters,
                )
                if result.rowcount == 0:
                    persisted = _load_evaluation(
                        connection,
                        organization_id=evaluation.organization_id,
                        evaluation_ref=evaluation.evaluation_ref,
                    )
                    if persisted != evaluation:
                        raise ReleaseEvaluationUnavailable(
                            "immutable release evaluation reference collision"
                        )
        except ReleaseEvaluationUnavailable:
            raise
        except (AssertionError, SQLAlchemyError) as error:
            raise ReleaseEvaluationUnavailable(
                "release evaluation persistence is unavailable"
            ) from error

    @contextmanager
    def transaction(
        self,
        organization_id: UUID,
    ) -> Iterator[ReleasePromotionTransactionPort]:
        if type(organization_id) is not UUID:
            raise TypeError("release transaction requires Organization UUID")
        connection = self._engine.connect()
        database_transaction = connection.begin()
        retained = _PostgreSQLReleasePromotionTransaction(
            connection,
            database_transaction,
            organization_id,
        )
        try:
            assert_learning_role(connection)
            _bind_organization(connection, organization_id)
            yield retained
        finally:
            if database_transaction.is_active:
                database_transaction.rollback()
            connection.close()


class _PostgreSQLReleasePromotionTransaction:
    """One non-reusable database transaction retained through commit."""

    def __init__(
        self,
        connection: Connection,
        database_transaction: Transaction,
        organization_id: UUID,
    ) -> None:
        self._connection = connection
        self._database_transaction = database_transaction
        self._organization_id = organization_id
        self._committed = False

    def _require_open(self, call: TrustedPromotionCall) -> None:
        if (
            self._committed
            or not self._database_transaction.is_active
            or type(call) is not TrustedPromotionCall
            or call.organization_id != self._organization_id
        ):
            raise ReleasePromotionRejected

    def revalidate_promotion(
        self,
        call: TrustedPromotionCall,
        *,
        evaluation_keyring: ReleaseEvaluationKeyring,
    ) -> None:
        self._require_open(call)
        try:
            durable_candidate = _load_candidate(
                self._connection,
                ReleaseCandidateRef(
                    organization_id=call.organization_id,
                    candidate_ref=call.candidate_ref,
                    candidate_digest=call.candidate_digest,
                ),
            )
            durable_evaluation = _load_evaluation(
                self._connection,
                organization_id=call.organization_id,
                evaluation_ref=call.evaluation_ref,
            )
        except ReleaseEvaluationUnavailable:
            raise ReleasePromotionRejected from None
        if (
            durable_candidate != call.candidate
            or durable_evaluation != call.evaluation
            or not verify_release_evaluation(
                durable_evaluation,
                candidate=durable_candidate,
                keyring=evaluation_keyring,
            )
        ):
            raise ReleasePromotionRejected

    def promote_atomically(self, call: TrustedPromotionCall) -> PromotionCommit:
        self._require_open(call)
        try:
            row = self._connection.execute(
                text(
                    """
                    SELECT * FROM public.context_learning_promote_release(
                        :organization_id, :promotion_ref, :operator_ref,
                        :authentication_binding_ref, :authority_ref,
                        :authority_digest, :candidate_ref, :candidate_digest,
                        :manifest_ref, :manifest_digest, :evaluation_ref,
                        :evaluation_digest, :evaluation_signing_key_version,
                        :evaluation_signature, :expected_active_generation,
                        :expected_base_manifest_digest, :issued_at,
                        :expires_at, :request_id, :audit_reason_digest,
                        :promotion_call_digest
                    )
                    """
                ),
                {
                    "organization_id": call.organization_id,
                    "promotion_ref": call.promotion_ref,
                    "operator_ref": call.operator_ref,
                    "authentication_binding_ref": call.authentication_binding_ref,
                    "authority_ref": call.authority_ref,
                    "authority_digest": call.authority_digest,
                    "candidate_ref": call.candidate_ref,
                    "candidate_digest": call.candidate_digest,
                    "manifest_ref": call.manifest_ref,
                    "manifest_digest": call.manifest_digest,
                    "evaluation_ref": call.evaluation_ref,
                    "evaluation_digest": call.evaluation_digest,
                    "evaluation_signing_key_version": (
                        call.evaluation.signing_key_version
                    ),
                    "evaluation_signature": call.evaluation.signature,
                    "expected_active_generation": (
                        call.expected_active_generation
                    ),
                    "expected_base_manifest_digest": (
                        call.expected_base_manifest_digest
                    ),
                    "issued_at": call.issued_at,
                    "expires_at": call.expires_at,
                    "request_id": call.request_id,
                    "audit_reason_digest": call.audit_reason_digest,
                    "promotion_call_digest": call.promotion_call_digest,
                },
            ).mappings().one()
            return PromotionCommit(
                organization_id=call.organization_id,
                promotion_ref=row["promotion_ref"],
                active_generation=row["active_generation"],
                manifest_ref=row["manifest_ref"],
                manifest_digest=row["manifest_digest"],
                promoted_at=row["promoted_at"],
            )
        except ReleasePromotionRejected:
            raise
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) in {"40001", "42501"}:
                raise ReleasePromotionRejected from None
            raise ReleasePromotionUnavailable(
                "release promotion database function is unavailable"
            ) from error
        except SQLAlchemyError as error:
            raise ReleasePromotionUnavailable(
                "release promotion database function is unavailable"
            ) from error

    def commit(self) -> None:
        if self._committed or not self._database_transaction.is_active:
            raise ReleasePromotionUnavailable(
                "release promotion transaction is not committable"
            )
        try:
            self._database_transaction.commit()
        except SQLAlchemyError as error:
            raise ReleasePromotionUnavailable(
                "release promotion commit failed"
            ) from error
        self._committed = True
