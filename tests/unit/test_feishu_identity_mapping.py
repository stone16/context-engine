from __future__ import annotations

import json
from uuid import uuid4

from adapters.connectors.feishu import (
    FeishuAclResponse,
    FeishuAclVisibility,
    FeishuChangePage,
    FeishuDocsConnectorAdapter,
    FeishuDocument,
    FeishuGroupSnapshot,
    FeishuIdentityMapping,
    FeishuPermissionKind,
    FeishuPermissionSubject,
)
from engine.supply import ConnectorCheckpointBinding
from tests.support.feishu_connector_twin import (
    SYNTHETIC_OBSERVED_AT,
    SyntheticFeishuTwin,
)


def test_unmapped_identity_remains_opaque_and_grants_nothing() -> None:
    document = FeishuDocument("document:private", "revision:1", b"# Private\n")
    twin = SyntheticFeishuTwin(
        pages={None: FeishuChangePage((document,), (), None, "checkpoint:1")},
        acl_responses={
            document.document_ref: FeishuAclResponse(
                document_ref=document.document_ref,
                visibility=FeishuAclVisibility.PRIVATE,
                subjects=(
                    FeishuPermissionSubject(
                        FeishuPermissionKind.USER,
                        "identity:unmapped",
                    ),
                ),
                observed_at=SYNTHETIC_OBSERVED_AT,
            )
        },
        identity_mappings={},
        group_snapshot=FeishuGroupSnapshot(
            "groups:v1",
            (),
            SYNTHETIC_OBSERVED_AT,
        ),
    )
    adapter = FeishuDocsConnectorAdapter.from_twin(twin)
    adapter.load_checkpoint(None)

    page = adapter.load(ConnectorCheckpointBinding(uuid4(), uuid4(), uuid4()))
    payload = page.documents[0].acl_observation.evidence_payload
    assert payload is not None
    artifact = json.loads(payload)

    assert artifact["policy_kind"] == "private"
    assert artifact["flattening"]["local_principal_refs"] == []
    assert artifact["flattening"]["opaque_identity_refs"] == [
        "identity:unmapped"
    ]
    assert twin.map_identity("identity:unmapped") == FeishuIdentityMapping(
        "identity:unmapped"
    )


def test_mapped_identity_is_recorded_without_becoming_connector_authority() -> None:
    mapping = FeishuIdentityMapping("identity:mapped", "principal:local")

    assert mapping.opaque is False
    assert mapping.local_principal_ref == "principal:local"
