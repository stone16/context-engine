"""Auditable one-to-one execution of the 20 clean-room Feishu oracles."""

from __future__ import annotations

import json
import socket
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from adapters.connectors.feishu import (
    FeishuAclFailure,
    FeishuAclResponse,
    FeishuAclVisibility,
    FeishuChangePage,
    FeishuDocsConnectorAdapter,
    FeishuDocument,
    FeishuDocumentDelete,
    FeishuGroupNode,
    FeishuGroupSnapshot,
    FeishuIdentityMapping,
    FeishuPermissionKind,
    FeishuPermissionSubject,
    FeishuRateLimited,
    FeishuSourceError,
    decode_feishu_checkpoint,
    flatten_feishu_groups,
)
from engine.supply import (
    ConnectorCheckpointBinding,
    SourceAclEvidenceClass,
    SupplyDocumentDeleteObservation,
)
from tests.support.feishu_connector_twin import SyntheticFeishuTwin

OBSERVED_AT = datetime(2026, 7, 31, 7, 0, tzinfo=UTC)
BINDING = ConnectorCheckpointBinding(
    UUID("10000000-0000-4000-8000-000000000001"),
    UUID("10000000-0000-4000-8000-000000000002"),
    UUID("10000000-0000-4000-8000-000000000003"),
)


def _document(document_ref: str = "document:alpha") -> FeishuDocument:
    return FeishuDocument(document_ref, "revision:1", b"# Synthetic\n")


def _acl(
    document_ref: str = "document:alpha",
    *,
    visibility: FeishuAclVisibility = FeishuAclVisibility.PRIVATE,
    subjects: tuple[FeishuPermissionSubject, ...] = (),
    observed_at: datetime = OBSERVED_AT,
) -> FeishuAclResponse:
    return FeishuAclResponse(document_ref, visibility, subjects, observed_at)


def _empty_groups(observed_at: datetime = OBSERVED_AT) -> FeishuGroupSnapshot:
    return FeishuGroupSnapshot("groups:v1", (), observed_at)


def _single_page_twin(
    *,
    document: FeishuDocument | None = None,
    acl: FeishuAclResponse | FeishuAclFailure | Exception | None = None,
    mappings: dict[str, FeishuIdentityMapping] | None = None,
    groups: FeishuGroupSnapshot | None = None,
    policy_epoch: int = 1,
) -> SyntheticFeishuTwin:
    selected = document or _document()
    return SyntheticFeishuTwin(
        pages={
            None: FeishuChangePage((selected,), (), None, "checkpoint:1"),
        },
        acl_responses={selected.document_ref: acl or _acl(selected.document_ref)},
        identity_mappings=mappings or {},
        group_snapshot=groups or _empty_groups(),
        policy_epoch=policy_epoch,
    )


def _load(twin: SyntheticFeishuTwin):  # type: ignore[no-untyped-def]
    adapter = FeishuDocsConnectorAdapter.from_twin(twin)
    adapter.load_checkpoint(None)
    return adapter, adapter.load(BINDING)


def _artifact(page: object) -> dict[str, object]:
    envelope = page.documents[0]  # type: ignore[attr-defined]
    payload = envelope.acl_observation.evidence_payload
    assert payload is not None
    decoded = json.loads(payload)
    assert isinstance(decoded, dict)
    return decoded


def test_room_a_oracle_01_twin_is_offline_and_credential_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the offline Feishu twin attempted network access")

    monkeypatch.setattr(socket, "create_connection", refuse_network)
    twin = _single_page_twin()

    _adapter, page = _load(twin)

    assert page.documents[0].document_ref == "document:alpha"
    assert twin.network_accesses == 0
    assert twin.credential_accesses == 0


def test_room_a_oracle_02_documents_are_deterministic() -> None:
    first = _load(_single_page_twin())[1]
    second = _load(_single_page_twin())[1]

    assert first == second


def test_room_a_oracle_03_pagination_uses_opaque_checkpoints() -> None:
    first = _document("document:alpha")
    second = _document("document:bravo")
    twin = SyntheticFeishuTwin(
        pages={
            None: FeishuChangePage((first,), (), "page:2", "checkpoint:1"),
            "page:2": FeishuChangePage((second,), (), None, "checkpoint:2"),
        },
        acl_responses={
            first.document_ref: _acl(first.document_ref),
            second.document_ref: _acl(second.document_ref),
        },
        identity_mappings={},
        group_snapshot=_empty_groups(),
    )
    adapter = FeishuDocsConnectorAdapter.from_twin(twin)
    adapter.load_checkpoint(None)

    first_page = adapter.load(BINDING)
    adapter.load_checkpoint(first_page.checkpoint_proposal)
    second_page = adapter.poll(BINDING)

    assert decode_feishu_checkpoint(first_page.checkpoint_proposal).page_token == (
        "page:2"
    )
    assert second_page.terminal is True
    assert twin.page_calls == [None, "page:2"]


def test_room_a_oracle_04_rate_response_is_bounded_and_emits_nothing() -> None:
    twin = SyntheticFeishuTwin(
        pages={None: FeishuRateLimited(9)},
        acl_responses={},
        identity_mappings={},
        group_snapshot=_empty_groups(),
    )
    adapter = FeishuDocsConnectorAdapter.from_twin(twin)
    adapter.load_checkpoint(None)

    with pytest.raises(FeishuRateLimited) as refusal:
        adapter.load(BINDING)

    assert refusal.value.retry_after_seconds == 9
    assert adapter.emitted_pages == []


def test_room_a_oracle_05_source_error_emits_no_checkpoint() -> None:
    twin = SyntheticFeishuTwin(
        pages={None: FeishuSourceError("synthetic refusal")},
        acl_responses={},
        identity_mappings={},
        group_snapshot=_empty_groups(),
    )
    adapter = FeishuDocsConnectorAdapter.from_twin(twin)
    adapter.load_checkpoint(None)

    with pytest.raises(FeishuSourceError, match="synthetic refusal"):
        adapter.load(BINDING)

    assert adapter.emitted_pages == []


def test_room_a_oracle_06_unmapped_identity_stays_opaque_and_grants_nothing() -> None:
    subject = FeishuPermissionSubject(FeishuPermissionKind.USER, "identity:opaque")
    twin = _single_page_twin(acl=_acl(subjects=(subject,)))

    _adapter, page = _load(twin)
    flattening = _artifact(page)["flattening"]

    assert isinstance(flattening, dict)
    assert flattening["local_principal_refs"] == []
    assert flattening["opaque_identity_refs"] == ["identity:opaque"]


def test_room_a_oracle_07_mapped_identity_is_retained_as_local_principal() -> None:
    subject = FeishuPermissionSubject(FeishuPermissionKind.USER, "identity:mapped")
    twin = _single_page_twin(
        acl=_acl(subjects=(subject,)),
        mappings={
            "identity:mapped": FeishuIdentityMapping(
                "identity:mapped", "principal:local"
            )
        },
    )

    _adapter, page = _load(twin)
    flattening = _artifact(page)["flattening"]

    assert isinstance(flattening, dict)
    assert flattening["local_principal_refs"] == ["principal:local"]
    assert flattening["opaque_identity_refs"] == []


def test_room_a_oracle_08_nested_groups_flatten_transitively() -> None:
    snapshot = FeishuGroupSnapshot(
        "groups:v8",
        (
            FeishuGroupNode(
                "group:child", "local-group:child", ("identity:member",), ()
            ),
            FeishuGroupNode(
                "group:root", "local-group:root", (), ("group:child",)
            ),
        ),
        OBSERVED_AT,
    )

    artifact = flatten_feishu_groups(
        snapshot,
        ("group:root",),
        lambda ref: FeishuIdentityMapping(ref, "principal:member"),
    )

    assert artifact.local_group_refs == (
        "local-group:child",
        "local-group:root",
    )
    assert artifact.local_principal_refs == ("principal:member",)


def test_room_a_oracle_09_group_artifact_version_and_digest_are_reproducible() -> None:
    snapshot = FeishuGroupSnapshot(
        "groups:v9",
        (FeishuGroupNode("group:root", "local-group:root"),),
        OBSERVED_AT,
    )

    first = flatten_feishu_groups(snapshot, ("group:root",), FeishuIdentityMapping)
    second = flatten_feishu_groups(snapshot, ("group:root",), FeishuIdentityMapping)

    assert first.version_ref == "groups:v9"
    assert first.digest == second.digest
    assert len(first.digest) == 64


def test_room_a_oracle_10_group_cycles_terminate_without_overgrant() -> None:
    snapshot = FeishuGroupSnapshot(
        "groups:v10",
        (
            FeishuGroupNode("group:a", "local-group:a", (), ("group:b",)),
            FeishuGroupNode("group:b", "local-group:b", (), ("group:a",)),
        ),
        OBSERVED_AT,
    )

    artifact = flatten_feishu_groups(snapshot, ("group:a",), FeishuIdentityMapping)

    assert artifact.local_group_refs == ("local-group:a", "local-group:b")
    assert artifact.local_principal_refs == ()


def test_room_a_oracle_11_unresolved_group_isolates() -> None:
    subject = FeishuPermissionSubject(FeishuPermissionKind.GROUP, "group:missing")
    twin = _single_page_twin(acl=_acl(subjects=(subject,)))

    _adapter, page = _load(twin)
    artifact = _artifact(page)

    assert artifact["status"] == "unresolved_group"
    assert artifact["policy_kind"] is None


def test_room_a_oracle_12_later_acl_outage_isolates_instead_of_reusing_grant() -> None:
    document = _document()
    twin = SyntheticFeishuTwin(
        pages={
            None: FeishuChangePage((document,), (), "page:2", "checkpoint:1"),
            "page:2": FeishuChangePage((document,), (), None, "checkpoint:2"),
        },
        acl_responses={},
        acl_sequences={
            document.document_ref: (
                _acl(),
                FeishuAclFailure(
                    document.document_ref,
                    OBSERVED_AT + timedelta(minutes=1),
                ),
            )
        },
        identity_mappings={},
        group_snapshot=_empty_groups(),
    )
    adapter = FeishuDocsConnectorAdapter.from_twin(twin)
    adapter.load_checkpoint(None)
    first = adapter.load(BINDING)
    adapter.load_checkpoint(first.checkpoint_proposal)

    second = adapter.poll(BINDING)

    assert _artifact(second)["status"] == "failed"
    assert _artifact(second)["policy_kind"] is None


def test_room_a_oracle_13_failed_acl_remains_mirrored_and_never_weak() -> None:
    twin = _single_page_twin(acl=FeishuAclFailure("document:alpha", OBSERVED_AT))

    _adapter, page = _load(twin)
    observation = page.documents[0].acl_observation

    assert observation.evidence_class is SourceAclEvidenceClass.MIRRORED
    assert _artifact(page)["status"] == "failed"


def test_room_a_oracle_14_out_of_order_timestamp_is_preserved_for_refusal() -> None:
    older = OBSERVED_AT - timedelta(minutes=1)
    twin = _single_page_twin(acl=_acl(observed_at=older))

    _adapter, page = _load(twin)

    assert page.documents[0].acl_observation.observed_at == older


def test_room_a_oracle_15_source_acl_emits_narrowing_policy() -> None:
    organization = _load(
        _single_page_twin(acl=_acl(visibility=FeishuAclVisibility.ORGANIZATION))
    )[1]
    private = _load(_single_page_twin(acl=_acl()))[1]

    assert _artifact(organization)["policy_kind"] == "organization"
    assert _artifact(private)["policy_kind"] == "private"


def test_room_a_oracle_16_revoke_observation_removes_prior_principal() -> None:
    subject = FeishuPermissionSubject(FeishuPermissionKind.USER, "identity:reader")
    granted = _load(
        _single_page_twin(
            acl=_acl(subjects=(subject,)),
            mappings={
                "identity:reader": FeishuIdentityMapping(
                    "identity:reader", "principal:reader"
                )
            },
        )
    )[1]
    revoked = _load(_single_page_twin(acl=_acl(subjects=())))[1]

    granted_flattening = _artifact(granted)["flattening"]
    revoked_flattening = _artifact(revoked)["flattening"]
    assert isinstance(granted_flattening, dict)
    assert isinstance(revoked_flattening, dict)
    assert granted_flattening["local_principal_refs"] == ["principal:reader"]
    assert revoked_flattening["local_principal_refs"] == []


def test_room_a_oracle_17_grant_observation_is_complete_without_index_state() -> None:
    subject = FeishuPermissionSubject(FeishuPermissionKind.USER, "identity:reader")
    twin = _single_page_twin(
        acl=_acl(subjects=(subject,)),
        mappings={
            "identity:reader": FeishuIdentityMapping(
                "identity:reader", "principal:reader"
            )
        },
    )

    _adapter, page = _load(twin)
    flattening = _artifact(page)["flattening"]

    assert isinstance(flattening, dict)
    assert flattening["local_principal_refs"] == ["principal:reader"]
    assert not hasattr(twin, "index")


def test_room_a_oracle_18_delete_uses_exact_pinned_observation_shape() -> None:
    twin = SyntheticFeishuTwin(
        pages={
            None: FeishuChangePage(
                (),
                (FeishuDocumentDelete("document:deleted", OBSERVED_AT),),
                None,
                "checkpoint:1",
            )
        },
        acl_responses={},
        identity_mappings={},
        group_snapshot=_empty_groups(),
    )

    _adapter, page = _load(twin)
    deletion = page.deleted_document_refs[0]

    assert type(deletion) is SupplyDocumentDeleteObservation
    assert tuple(field.name for field in fields(deletion)) == (
        "document_ref",
        "acl_observation",
    )
    assert twin.acl_calls == []


def test_room_a_oracle_19_checkpoint_waits_for_acceptance() -> None:
    document = _document()
    twin = SyntheticFeishuTwin(
        pages={
            None: FeishuChangePage((document,), (), "page:2", "checkpoint:1"),
            "page:2": FeishuChangePage((document,), (), None, "checkpoint:2"),
        },
        acl_responses={document.document_ref: _acl()},
        identity_mappings={},
        group_snapshot=_empty_groups(),
    )
    adapter = FeishuDocsConnectorAdapter.from_twin(twin)
    adapter.load_checkpoint(None)

    first = adapter.load(BINDING)
    repeated = adapter.poll(BINDING)

    assert first.page_ref == repeated.page_ref
    assert twin.page_calls == [None, None]


def test_room_a_oracle_20_ahead_epoch_and_future_time_remain_untrusted_inputs() -> None:
    future = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)
    twin = _single_page_twin(
        acl=_acl(observed_at=future),
        policy_epoch=2**63 - 1,
    )

    _adapter, page = _load(twin)
    observation = page.documents[0].acl_observation

    assert observation.policy_epoch == 2**63 - 1
    assert observation.observed_at == future
    assert observation.evidence_class is SourceAclEvidenceClass.MIRRORED
