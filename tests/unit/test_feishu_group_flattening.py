from __future__ import annotations

from adapters.connectors.feishu import (
    FeishuGroupNode,
    FeishuGroupSnapshot,
    FeishuIdentityMapping,
    flatten_feishu_groups,
)
from tests.support.feishu_connector_twin import SYNTHETIC_OBSERVED_AT


def _snapshot() -> FeishuGroupSnapshot:
    return FeishuGroupSnapshot(
        "groups:v7",
        (
            FeishuGroupNode(
                "group:child",
                "local-group:child",
                ("identity:mapped", "identity:opaque"),
                ("group:root",),
            ),
            FeishuGroupNode(
                "group:root",
                "local-group:root",
                (),
                ("group:child",),
            ),
        ),
        SYNTHETIC_OBSERVED_AT,
    )


def _mapping(identity_ref: str) -> FeishuIdentityMapping:
    if identity_ref == "identity:mapped":
        return FeishuIdentityMapping(identity_ref, "principal:mapped")
    return FeishuIdentityMapping(identity_ref)


def test_nested_groups_flatten_into_a_versioned_recorded_artifact() -> None:
    artifact = flatten_feishu_groups(_snapshot(), ("group:root",), _mapping)

    assert artifact.version_ref == "groups:v7"
    assert len(artifact.digest) == 64
    assert artifact.local_group_refs == (
        "local-group:child",
        "local-group:root",
    )
    assert artifact.local_principal_refs == ("principal:mapped",)
    assert artifact.opaque_identity_refs == ("identity:opaque",)
    assert artifact.resolved is True


def test_group_cycle_terminates_without_duplicate_or_extra_grants() -> None:
    artifact = flatten_feishu_groups(_snapshot(), ("group:root",), _mapping)

    assert artifact.local_group_refs == (
        "local-group:child",
        "local-group:root",
    )
    assert artifact.local_principal_refs == ("principal:mapped",)


def test_unresolved_nested_group_is_explicit_and_fail_closed() -> None:
    snapshot = FeishuGroupSnapshot(
        "groups:v8",
        (
            FeishuGroupNode(
                "group:root",
                "local-group:root",
                (),
                ("group:missing",),
            ),
        ),
        SYNTHETIC_OBSERVED_AT,
    )

    artifact = flatten_feishu_groups(snapshot, ("group:root",), _mapping)

    assert artifact.resolved is False
    assert artifact.unresolved_group_refs == ("group:missing",)
