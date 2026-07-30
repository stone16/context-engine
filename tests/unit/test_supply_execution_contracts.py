from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from uuid import UUID

import pytest

from engine.supply.execution import (
    ConnectorCheckpointBinding,
    ConnectorCheckpointProposal,
    ConnectorFailure,
    ConnectorFailureCategory,
    ConnectorHeartbeat,
    SourceAclEvidenceClass,
    SourceAclObservation,
    StagedArtifact,
    SupplyChangePage,
    SupplyDocumentEnvelope,
    serialize_supply_change_page,
)

ORGANIZATION_ID = UUID("0198fb91-e6e2-75ea-a174-912597825765")
SOURCE_VERSION_ID = UUID("0198fb92-1787-70bd-9c79-cb2090379d4d")
WORKER_JOB_ID = UUID("0198fb92-3650-79e4-8657-1539f459846d")
NOW = datetime(2026, 7, 29, 16, 30, tzinfo=UTC)


def _binding(**overrides: object) -> ConnectorCheckpointBinding:
    values: dict[str, object] = {
        "organization_id": ORGANIZATION_ID,
        "source_version_id": SOURCE_VERSION_ID,
        "worker_job_id": WORKER_JOB_ID,
    }
    values.update(overrides)
    return ConnectorCheckpointBinding(**values)  # type: ignore[arg-type]


def _acl(**overrides: object) -> SourceAclObservation:
    values: dict[str, object] = {
        "organization_id": ORGANIZATION_ID,
        "evidence_class": SourceAclEvidenceClass.MIRRORED,
        "evidence_payload": b"synthetic-mirrored-acl-v1",
    }
    values.update(overrides)
    return SourceAclObservation(**values)  # type: ignore[arg-type]


def _envelope(**overrides: object) -> SupplyDocumentEnvelope:
    values: dict[str, object] = {
        "organization_id": ORGANIZATION_ID,
        "source_version_id": SOURCE_VERSION_ID,
        "worker_job_id": WORKER_JOB_ID,
        "document_ref": "document:synthetic-handbook",
        "content": b"# Synthetic handbook",
        "content_type": "text/markdown",
        "acl_observation": _acl(),
        "metadata": (("source_revision", "revision-1"),),
    }
    values.update(overrides)
    return SupplyDocumentEnvelope(**values)  # type: ignore[arg-type]


def _proposal(**overrides: object) -> ConnectorCheckpointProposal:
    values: dict[str, object] = {
        "binding": _binding(),
        "opaque_checkpoint": b"connector-owned-checkpoint-v1",
        "change_page_ref": "page:1",
    }
    values.update(overrides)
    return ConnectorCheckpointProposal(**values)  # type: ignore[arg-type]


def _page(**overrides: object) -> SupplyChangePage:
    values: dict[str, object] = {
        "binding": _binding(),
        "page_ref": "page:1",
        "documents": (_envelope(),),
        "deleted_document_refs": (),
        "checkpoint_proposal": b"connector-owned-checkpoint-v1",
        "terminal": False,
    }
    values.update(overrides)
    return SupplyChangePage(**values)  # type: ignore[arg-type]


def _artifact(**overrides: object) -> StagedArtifact:
    values: dict[str, object] = {
        "binding": _binding(),
        "artifact_ref": "artifact:page-1",
        "payload": b"synthetic-staged-payload",
    }
    values.update(overrides)
    return StagedArtifact(**values)  # type: ignore[arg-type]


def _heartbeat(**overrides: object) -> ConnectorHeartbeat:
    values: dict[str, object] = {"binding": _binding(), "observed_at": NOW}
    values.update(overrides)
    return ConnectorHeartbeat(**values)  # type: ignore[arg-type]


def _failure(**overrides: object) -> ConnectorFailure:
    values: dict[str, object] = {
        "binding": _binding(),
        "category": ConnectorFailureCategory.RETRYABLE,
        "reason_digest": "1" * 64,
        "observed_at": NOW,
    }
    values.update(overrides)
    return ConnectorFailure(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("factory", "field_name", "invalid"),
    [
        (_binding, "organization_id", None),
        (_binding, "organization_id", "org-1"),
        (_binding, "source_version_id", None),
        (_binding, "source_version_id", "version-1"),
        (_binding, "worker_job_id", None),
        (_binding, "worker_job_id", "job-1"),
        (_envelope, "organization_id", None),
        (_envelope, "organization_id", "org-1"),
        (_envelope, "source_version_id", None),
        (_envelope, "worker_job_id", None),
        (_envelope, "document_ref", ""),
        (_envelope, "document_ref", "   "),
        (_envelope, "document_ref", 7),
        (_envelope, "content", b""),
        (_envelope, "content", "not-bytes"),
        (_envelope, "content_type", ""),
        (_envelope, "content_type", 7),
        (_envelope, "acl_observation", None),
        (_envelope, "metadata", None),
        (_envelope, "metadata", {"source_revision": "revision-1"}),
    ],
)
def test_binding_and_document_contracts_reject_missing_blank_or_wrong_types(
    factory: object,
    field_name: str,
    invalid: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory(**{field_name: invalid})  # type: ignore[operator]


def test_document_envelope_cannot_be_constructed_without_organization() -> None:
    with pytest.raises(TypeError):
        SupplyDocumentEnvelope(  # type: ignore[call-arg]
            source_version_id=SOURCE_VERSION_ID,
            worker_job_id=WORKER_JOB_ID,
            document_ref="document:synthetic-handbook",
            content=b"# Synthetic handbook",
            content_type="text/markdown",
            acl_observation=_acl(),
        )


def test_document_envelope_rejects_cross_binding_acl_observation() -> None:
    with pytest.raises(ValueError, match="Organization"):
        _envelope(
            acl_observation=_acl(
                organization_id=UUID("0198fb94-57ab-710b-b03d-3e29149ae95a")
            )
        )


@pytest.mark.parametrize(
    ("field_name", "foreign_value"),
    [
        ("organization_id", UUID("0198fb94-57ab-710b-b03d-3e29149ae95a")),
        ("source_version_id", UUID("0198fb94-6cba-7e4b-a84b-fc65f19e2270")),
        ("worker_job_id", UUID("0198fb94-7f40-7de2-bfce-56814a428277")),
    ],
)
def test_change_page_rejects_document_outside_its_exact_binding(
    field_name: str,
    foreign_value: UUID,
) -> None:
    overrides: dict[str, object] = {field_name: foreign_value}
    if field_name == "organization_id":
        overrides["acl_observation"] = _acl(organization_id=foreign_value)
    with pytest.raises(ValueError, match="document exact binding must match"):
        _page(documents=(_envelope(**overrides),))


@pytest.mark.parametrize(
    ("target", "field_name", "invalid"),
    [
        ("document", "organization_id", UUID("0198fb94-57ab-710b-b03d-3e29149ae95a")),
        (
            "document",
            "source_version_id",
            UUID("0198fb94-6cba-7e4b-a84b-fc65f19e2270"),
        ),
        ("document", "worker_job_id", UUID("0198fb94-7f40-7de2-bfce-56814a428277")),
        ("document", "content", b""),
        ("acl", "organization_id", UUID("0198fb94-57ab-710b-b03d-3e29149ae95a")),
        ("acl", "evidence_class", SourceAclEvidenceClass.WEAK),
        ("acl", "evidence_payload", None),
    ],
)
def test_staged_serialization_revalidates_every_nested_claim(
    target: str,
    field_name: str,
    invalid: object,
) -> None:
    page = _page()
    envelope = page.documents[0]
    mutated = envelope if target == "document" else envelope.acl_observation
    object.__setattr__(mutated, field_name, invalid)

    with pytest.raises((TypeError, ValueError)):
        serialize_supply_change_page(page)


def test_staged_serialization_refuses_unjustified_weak_acl_downgrade() -> None:
    page = _page()
    observation = page.documents[0].acl_observation
    object.__setattr__(observation, "evidence_class", SourceAclEvidenceClass.WEAK)
    object.__setattr__(observation, "evidence_payload", None)
    object.__setattr__(observation, "source_lacks_stronger_acl", None)

    with pytest.raises(ValueError, match="Weak ACL evidence requires explicit"):
        serialize_supply_change_page(page)


@pytest.mark.parametrize(
    ("factory", "field_name", "invalid"),
    [
        (_proposal, "binding", None),
        (_proposal, "opaque_checkpoint", b""),
        (_proposal, "opaque_checkpoint", "checkpoint"),
        (_proposal, "change_page_ref", ""),
        (_proposal, "change_page_ref", None),
        (_page, "binding", None),
        (_page, "page_ref", ""),
        (_page, "documents", []),
        (_page, "documents", (None,)),
        (_page, "deleted_document_refs", []),
        (_page, "deleted_document_refs", ("",)),
        (_page, "checkpoint_proposal", b""),
        (_page, "checkpoint_proposal", None),
        (_page, "terminal", None),
        (_artifact, "binding", None),
        (_artifact, "artifact_ref", ""),
        (_artifact, "payload", b""),
        (_artifact, "payload", None),
        (_heartbeat, "binding", None),
        (_heartbeat, "observed_at", datetime(2026, 7, 29, 16, 30)),
        (_heartbeat, "observed_at", None),
        (_failure, "binding", None),
        (_failure, "category", "retryable"),
        (_failure, "reason_digest", ""),
        (_failure, "reason_digest", None),
        (_failure, "observed_at", datetime(2026, 7, 29, 16, 30)),
        (_failure, "observed_at", None),
    ],
)
def test_runner_contracts_reject_missing_blank_or_wrong_types(
    factory: object,
    field_name: str,
    invalid: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory(**{field_name: invalid})  # type: ignore[operator]


def test_all_supply_execution_dataclasses_are_frozen_slotted_and_validate() -> None:
    binding = _binding()
    contracts = (
        binding,
        _acl(),
        _envelope(),
        ConnectorCheckpointProposal(
            binding=binding,
            opaque_checkpoint=b"connector-owned-checkpoint-v1",
            change_page_ref="page:1",
        ),
        SupplyChangePage(
            binding=binding,
            page_ref="page:1",
            documents=(_envelope(),),
            deleted_document_refs=(),
            checkpoint_proposal=b"connector-owned-checkpoint-v1",
        ),
        StagedArtifact(
            binding=binding,
            artifact_ref="artifact:page-1",
            payload=b"synthetic-staged-payload",
        ),
        ConnectorHeartbeat(binding=binding, observed_at=NOW),
        ConnectorFailure(
            binding=binding,
            category=ConnectorFailureCategory.RETRYABLE,
            reason_digest="1" * 64,
            observed_at=NOW,
        ),
    )

    for contract in contracts:
        assert not hasattr(contract, "__dict__")
        assert fields(contract)
        with pytest.raises(FrozenInstanceError):
            setattr(contract, fields(contract)[0].name, None)


@pytest.mark.parametrize(
    ("evidence_class", "payload", "justification"),
    [
        (SourceAclEvidenceClass.LIVE, None, None),
        (SourceAclEvidenceClass.LIVE, b"", None),
        (SourceAclEvidenceClass.MIRRORED, None, None),
        (SourceAclEvidenceClass.MIRRORED, b"", None),
        (SourceAclEvidenceClass.WEAK, None, None),
        (SourceAclEvidenceClass.WEAK, None, ""),
        (SourceAclEvidenceClass.WEAK, None, "   "),
    ],
)
def test_acl_observation_requires_evidence_or_explicit_weak_justification(
    evidence_class: SourceAclEvidenceClass,
    payload: bytes | None,
    justification: str | None,
) -> None:
    with pytest.raises(ValueError):
        SourceAclObservation(
            organization_id=ORGANIZATION_ID,
            evidence_class=evidence_class,
            evidence_payload=payload,
            source_lacks_stronger_acl=justification,
        )


def test_live_and_mirrored_acl_observations_carry_evidence_payloads() -> None:
    for evidence_class in (
        SourceAclEvidenceClass.LIVE,
        SourceAclEvidenceClass.MIRRORED,
    ):
        observation = SourceAclObservation(
            organization_id=ORGANIZATION_ID,
            evidence_class=evidence_class,
            evidence_payload=b"synthetic-source-acl-evidence",
        )
        assert observation.evidence_payload == b"synthetic-source-acl-evidence"
        assert observation.source_lacks_stronger_acl is None


def test_weak_acl_observation_requires_and_retains_honest_justification() -> None:
    observation = SourceAclObservation(
        organization_id=ORGANIZATION_ID,
        evidence_class=SourceAclEvidenceClass.WEAK,
        source_lacks_stronger_acl="source exposes membership but no object ACL",
    )

    assert observation.evidence_payload is None
    assert observation.source_lacks_stronger_acl == (
        "source exposes membership but no object ACL"
    )


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    [
        ("organization_id", None),
        ("organization_id", "org-1"),
        ("evidence_class", None),
        ("evidence_class", "mirrored"),
        ("evidence_payload", "not-bytes"),
        ("source_lacks_stronger_acl", 7),
    ],
)
def test_acl_observation_rejects_missing_or_wrong_type_fields(
    field_name: str,
    invalid: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _acl(**{field_name: invalid})
