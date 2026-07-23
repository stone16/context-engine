import pickle
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest

from engine.runtime.evidence import (
    MAX_PROJECTED_FIELD_REFS,
    AuthorizedProjection,
    CandidateRef,
    Evidence,
    EvidenceLineage,
    PackageBlock,
    PackageContent,
    _close_authorization_kernel_scope,
    _construct_authorized_projection,
    _open_authorization_kernel_scope,
    _require_active_authorized_projection,
    construct_package_content,
    validate_package_content,
)

ORGANIZATION_ID = UUID("b7271139-f675-4f28-8146-d314ee1b80d3")
OTHER_ORGANIZATION_ID = UUID("48f519e3-c9f1-4e45-af3a-ef48ca5b23f0")
AS_OF = datetime(2026, 7, 21, 9, 30, tzinfo=UTC)


def candidate(
    suffix: str,
    *,
    organization_id: UUID = ORGANIZATION_ID,
) -> CandidateRef:
    return CandidateRef(
        organization_id=organization_id,
        source_ref=f"source-{suffix}",
        resource_ref=f"resource-{suffix}",
        revision_ref=f"revision-{suffix}",
        fragment_ref=f"fragment-{suffix}",
    )


def lineage(
    *,
    run_ref: str = "run-request-1",
    purpose: str = "context.answer",
    decision_ref: str = "decision-request-1",
) -> EvidenceLineage:
    return EvidenceLineage(
        run_ref=run_ref,
        principal_ref="principal-1",
        purpose=purpose,
        as_of=AS_OF,
        decision_ref=decision_ref,
        policy_snapshot_ref="policy-snapshot-7",
        policy_epoch=7,
        source_acl_decision_ref="source-acl-decision-4",
    )


def projection(
    suffix: str,
    body: str,
    *,
    kernel_scope: object,
    projected_field_refs: tuple[str, ...] = ("body",),
    organization_id: UUID = ORGANIZATION_ID,
    evidence_lineage: EvidenceLineage | None = None,
) -> AuthorizedProjection:
    return _construct_authorized_projection(
        kernel_scope=cast(Any, kernel_scope),
        candidate_ref=candidate(suffix, organization_id=organization_id),
        body=body,
        projected_field_refs=projected_field_refs,
        lineage=evidence_lineage or lineage(),
    )


def test_candidate_ref_is_exact_content_free_hidden_and_nonserializable() -> None:
    ref = candidate("a")

    assert tuple(field.name for field in fields(ref)) == (
        "organization_id",
        "source_ref",
        "resource_ref",
        "revision_ref",
        "fragment_ref",
    )
    assert ref.fragment_ref == "fragment-a"
    rendered = repr(ref)
    for secret in (
        str(ORGANIZATION_ID),
        "source-a",
        "resource-a",
        "revision-a",
        "fragment-a",
    ):
        assert secret not in rendered
    with pytest.raises(FrozenInstanceError):
        ref.fragment_ref = "fragment-mutated"  # type: ignore[misc]
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(ref)


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    (
        ("organization_id", str(ORGANIZATION_ID)),
        ("source_ref", ""),
        ("resource_ref", " "),
        ("revision_ref", "revision with whitespace"),
        ("fragment_ref", 4),
        ("fragment_ref", "x" * 257),
    ),
)
def test_candidate_ref_rejects_nonopaque_or_malformed_values(
    field_name: str,
    invalid: object,
) -> None:
    values: dict[str, object] = {
        "organization_id": ORGANIZATION_ID,
        "source_ref": "source-a",
        "resource_ref": "resource-a",
        "revision_ref": "revision-a",
        "fragment_ref": "fragment-a",
    }
    values[field_name] = invalid

    with pytest.raises((TypeError, ValueError)):
        CandidateRef(**cast(Any, values))


def test_evidence_lineage_carries_complete_request_and_policy_linkage() -> None:
    value = lineage()

    assert value.run_ref == "run-request-1"
    assert value.principal_ref == "principal-1"
    assert value.purpose == "context.answer"
    assert value.as_of == AS_OF
    assert value.decision_ref == "decision-request-1"
    assert value.policy_snapshot_ref == "policy-snapshot-7"
    assert value.policy_epoch == 7
    assert value.source_acl_decision_ref == "source-acl-decision-4"
    assert "principal-1" not in repr(value)


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    (
        ("run_ref", ""),
        ("principal_ref", object()),
        ("purpose", " "),
        ("as_of", datetime(2026, 7, 21, 9, 30)),
        ("decision_ref", "has whitespace"),
        ("policy_snapshot_ref", ""),
        ("policy_epoch", 0),
        ("policy_epoch", True),
        ("source_acl_decision_ref", " "),
    ),
)
def test_evidence_lineage_rejects_incomplete_or_malformed_linkage(
    field_name: str,
    invalid: object,
) -> None:
    values: dict[str, object] = {
        "run_ref": "run-request-1",
        "principal_ref": "principal-1",
        "purpose": "context.answer",
        "as_of": AS_OF,
        "decision_ref": "decision-request-1",
        "policy_snapshot_ref": "policy-snapshot-7",
        "policy_epoch": 7,
        "source_acl_decision_ref": "source-acl-decision-4",
    }
    values[field_name] = invalid

    with pytest.raises((TypeError, ValueError)):
        EvidenceLineage(**cast(Any, values))


def test_authorized_projection_is_kernel_only_hidden_and_scope_lived() -> None:
    with pytest.raises(TypeError, match="AuthorizationKernel"):
        AuthorizedProjection()

    kernel_scope = _open_authorization_kernel_scope()
    authorized = projection("a", "authorized body", kernel_scope=kernel_scope)

    _require_active_authorized_projection(authorized)
    assert authorized.candidate_ref.fragment_ref == "fragment-a"
    assert authorized.projected_body == "authorized body"
    assert authorized.projected_field_refs == ("body",)
    assert authorized.lineage == lineage()
    rendered = repr(authorized)
    assert "authorized body" not in rendered
    assert "fragment-a" not in rendered
    with pytest.raises(FrozenInstanceError):
        authorized.projected_body = "mutated"  # type: ignore[misc]
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(authorized)

    _close_authorization_kernel_scope(kernel_scope)
    with pytest.raises(ValueError, match="active AuthorizationKernel"):
        _require_active_authorized_projection(authorized)


def test_authorized_projection_revalidates_integrity_before_content_use() -> None:
    kernel_scope = _open_authorization_kernel_scope()
    authorized = projection("a", "authorized body", kernel_scope=kernel_scope)
    object.__setattr__(authorized, "projected_body", "denied replacement")

    with pytest.raises(ValueError, match="integrity"):
        _require_active_authorized_projection(authorized)
    with pytest.raises(ValueError, match="integrity"):
        construct_package_content((authorized,))

    _close_authorization_kernel_scope(kernel_scope)


def test_authorized_projection_and_evidence_bind_exact_projected_fields() -> None:
    kernel_scope = _open_authorization_kernel_scope()
    authorized = projection(
        "a",
        "status=open",
        kernel_scope=kernel_scope,
        projected_field_refs=("status",),
    )
    content = construct_package_content((authorized,))

    assert content.evidence[0].projected_field_refs == ("status",)
    object.__setattr__(authorized, "projected_field_refs", ("private_note",))
    with pytest.raises(ValueError, match="integrity"):
        construct_package_content((authorized,))

    object.__setattr__(content.evidence[0], "projected_field_refs", ("private_note",))
    with pytest.raises(ValueError, match="Evidence integrity"):
        validate_package_content(content.blocks, content.evidence)
    _close_authorization_kernel_scope(kernel_scope)


def test_authorized_projection_rejects_more_than_the_public_field_bound() -> None:
    kernel_scope = _open_authorization_kernel_scope()
    maximum_refs = tuple(f"field_{index}" for index in range(MAX_PROJECTED_FIELD_REFS))
    too_many_refs = tuple(
        f"field_{index}" for index in range(MAX_PROJECTED_FIELD_REFS + 1)
    )

    maximum_projection = projection(
        "a",
        "authorized body",
        kernel_scope=kernel_scope,
        projected_field_refs=maximum_refs,
    )
    maximum_content = construct_package_content((maximum_projection,))

    assert maximum_content.evidence[0].projected_field_refs == maximum_refs
    with pytest.raises(ValueError, match="at most 64"):
        projection(
            "a",
            "authorized body",
            kernel_scope=kernel_scope,
            projected_field_refs=too_many_refs,
        )

    _close_authorization_kernel_scope(kernel_scope)


def test_evidence_ref_binds_the_exact_projected_body() -> None:
    kernel_scope = _open_authorization_kernel_scope()
    open_content = construct_package_content(
        (
            projection(
                "a",
                "status=open",
                kernel_scope=kernel_scope,
                projected_field_refs=("status",),
            ),
        )
    )
    closed_content = construct_package_content(
        (
            projection(
                "a",
                "status=closed",
                kernel_scope=kernel_scope,
                projected_field_refs=("status",),
            ),
        )
    )

    assert (
        open_content.evidence[0].evidence_ref != closed_content.evidence[0].evidence_ref
    )
    assert open_content.blocks[0].evidence_ref == open_content.evidence[0].evidence_ref
    assert (
        closed_content.blocks[0].evidence_ref == closed_content.evidence[0].evidence_ref
    )
    _close_authorization_kernel_scope(kernel_scope)


def test_package_content_is_deterministic_and_exactly_links_each_projection() -> None:
    kernel_scope = _open_authorization_kernel_scope()
    projection_b = projection("b", "body b", kernel_scope=kernel_scope)
    projection_a = projection("a", "body a", kernel_scope=kernel_scope)

    content_from_hostile_rank = construct_package_content(
        (projection_b, projection_a)
    )
    content_from_reverse_rank = construct_package_content(
        (projection_a, projection_b)
    )

    assert content_from_hostile_rank == content_from_reverse_rank
    assert isinstance(content_from_hostile_rank, PackageContent)
    assert tuple(block.body for block in content_from_hostile_rank.blocks) == (
        "body a",
        "body b",
    )
    assert tuple(item.fragment_ref for item in content_from_hostile_rank.evidence) == (
        "fragment-a",
        "fragment-b",
    )
    assert tuple(block.evidence_ref for block in content_from_hostile_rank.blocks) == (
        tuple(item.evidence_ref for item in content_from_hostile_rank.evidence)
    )
    assert all(
        item.lineage == lineage() for item in content_from_hostile_rank.evidence
    )
    assert "organization_id" not in {
        field.name for field in fields(content_from_hostile_rank.evidence[0])
    }
    validate_package_content(
        content_from_hostile_rank.blocks,
        content_from_hostile_rank.evidence,
    )

    _close_authorization_kernel_scope(kernel_scope)


@pytest.mark.security_evidence(id="PROP-INDEX-NOT-AUTHORITY-005", layer="property")
def test_package_content_can_only_be_constructed_from_authorized_projections() -> None:
    with pytest.raises(TypeError, match="authorized projections"):
        PackageContent(blocks=(), evidence=())


def test_package_constructor_rejects_denied_or_nonprojection_objects() -> None:
    class DeniedObject:
        body = "denied bytes"

    kernel_scope = _open_authorization_kernel_scope()
    authorized = projection("a", "authorized body", kernel_scope=kernel_scope)

    for rejected in (candidate("denied"), DeniedObject(), object()):
        with pytest.raises(TypeError, match="AuthorizedProjection"):
            construct_package_content((authorized, cast(Any, rejected)))

    _close_authorization_kernel_scope(kernel_scope)


def test_package_constructor_rejects_expired_projection_authority() -> None:
    kernel_scope = _open_authorization_kernel_scope()
    authorized = projection("a", "authorized body", kernel_scope=kernel_scope)
    _close_authorization_kernel_scope(kernel_scope)

    with pytest.raises(ValueError, match="active AuthorizationKernel"):
        construct_package_content((authorized,))


def test_package_constructor_rejects_cross_organization_or_mixed_request() -> None:
    kernel_scope = _open_authorization_kernel_scope()
    authorized = projection("a", "authorized body", kernel_scope=kernel_scope)
    cross_organization = projection(
        "cross",
        "cross organization body",
        kernel_scope=kernel_scope,
        organization_id=OTHER_ORGANIZATION_ID,
    )
    other_request = projection(
        "other-run",
        "other run body",
        kernel_scope=kernel_scope,
        evidence_lineage=lineage(run_ref="run-request-2"),
    )

    with pytest.raises(ValueError, match="one Organization"):
        construct_package_content((authorized, cross_organization))
    with pytest.raises(ValueError, match="one request decision"):
        construct_package_content((authorized, other_request))

    _close_authorization_kernel_scope(kernel_scope)


def test_package_integrity_rejects_duplicate_dangling_and_orphan_references() -> None:
    kernel_scope = _open_authorization_kernel_scope()
    content = construct_package_content(
        (projection("a", "authorized body", kernel_scope=kernel_scope),)
    )
    block = content.blocks[0]
    evidence = content.evidence[0]
    unknown_ref = "ev_" + "f" * 64

    malformed_cases = (
        ((block, block), (evidence,), "duplicate block"),
        ((block,), (evidence, evidence), "duplicate Evidence"),
        (
            (replace(block, evidence_ref=unknown_ref),),
            (evidence,),
            "dangling",
        ),
        (
            (block,),
            (
                evidence,
                replace(
                    evidence,
                    evidence_ref=unknown_ref,
                    fragment_ref="fragment-orphan",
                ),
            ),
            "orphan",
        ),
        (
            (block,),
            (
                evidence,
                replace(evidence, evidence_ref=unknown_ref),
            ),
            "duplicate Fragment",
        ),
    )
    for blocks, evidence_items, expected in malformed_cases:
        with pytest.raises(ValueError, match=expected):
            validate_package_content(blocks, evidence_items)

    _close_authorization_kernel_scope(kernel_scope)


def test_package_integrity_rejects_lineage_mixed_within_one_package() -> None:
    kernel_scope = _open_authorization_kernel_scope()
    content = construct_package_content(
        (
            projection("a", "body a", kernel_scope=kernel_scope),
            projection("b", "body b", kernel_scope=kernel_scope),
        )
    )
    mixed = replace(
        content.evidence[1],
        lineage=lineage(decision_ref="decision-request-2"),
    )

    with pytest.raises(ValueError, match="one request decision"):
        validate_package_content(content.blocks, (content.evidence[0], mixed))

    _close_authorization_kernel_scope(kernel_scope)


def test_package_integrity_revalidates_mutated_block_and_evidence_values() -> None:
    kernel_scope = _open_authorization_kernel_scope()
    content = construct_package_content(
        (projection("a", "authorized body", kernel_scope=kernel_scope),)
    )
    object.__setattr__(content.blocks[0], "body", " ")

    with pytest.raises(ValueError, match="projected body"):
        validate_package_content(content.blocks, content.evidence)

    object.__setattr__(content.blocks[0], "body", "authorized body")
    object.__setattr__(content.evidence[0].lineage, "policy_epoch", 0)
    with pytest.raises(ValueError, match="policy_epoch"):
        validate_package_content(content.blocks, content.evidence)

    _close_authorization_kernel_scope(kernel_scope)


@pytest.mark.parametrize(
    ("value_type", "values"),
    (
        (
            PackageBlock,
            {"evidence_ref": "ev_" + "a" * 64, "body": " "},
        ),
        (
            Evidence,
            {
                "evidence_ref": "not-an-evidence-ref",
                "source_ref": "source-a",
                "resource_ref": "resource-a",
                "revision_ref": "revision-a",
                "fragment_ref": "fragment-a",
                "projected_field_refs": ("body",),
                "lineage": lineage(),
            },
        ),
    ),
)
def test_evidence_and_block_reject_malformed_public_values(
    value_type: type[object],
    values: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        value_type(**cast(Any, values))
