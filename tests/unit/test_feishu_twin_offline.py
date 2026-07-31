from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from adapters.connectors.feishu import (
    DeterministicFeishuTwin,
    FeishuAclResponse,
    FeishuAclVisibility,
    FeishuChangePage,
    FeishuConnectorProcessAdapter,
    FeishuDocsConnectorAdapter,
    FeishuDocument,
    FeishuGroupNode,
    FeishuGroupSnapshot,
    FeishuIdentityMapping,
    FeishuPermissionKind,
    FeishuPermissionSubject,
    FeishuRateLimited,
    FeishuSourceError,
    decode_feishu_checkpoint,
    serialize_feishu_twin_fixture,
)
from engine.supply import ConnectorCheckpointBinding, WorkerLeaseToken
from tests.support.feishu_connector_twin import (
    SYNTHETIC_OBSERVED_AT,
    SyntheticFeishuTwin,
)


def _empty_groups() -> FeishuGroupSnapshot:
    return FeishuGroupSnapshot("groups:v1", (), SYNTHETIC_OBSERVED_AT)


def _binding() -> ConnectorCheckpointBinding:
    return ConnectorCheckpointBinding(uuid4(), uuid4(), uuid4())


def _process_adapter(
    *,
    pages: dict[str | None, FeishuChangePage | FeishuSourceError],
    acl_responses: dict[
        str, FeishuAclResponse | FeishuSourceError
    ] | None = None,
    identity_mappings: dict[
        str, FeishuIdentityMapping | FeishuSourceError
    ] | None = None,
    group_snapshot: FeishuGroupSnapshot | FeishuSourceError | None = None,
) -> FeishuConnectorProcessAdapter:
    return FeishuConnectorProcessAdapter(
        serialize_feishu_twin_fixture(
            pages=pages,
            acl_responses=acl_responses or {},
            identity_mappings=identity_mappings or {},
            group_snapshot=group_snapshot or _empty_groups(),
        ),
        policy_epoch=1,
        worker_lease=WorkerLeaseToken("synthetic.opaque.lease"),
        service_principal_id=UUID("00000000-0000-4000-8000-000000000001"),
        idempotency_key="0" * 64,
        service_actor_expires_at=datetime(2026, 7, 31, 9, tzinfo=UTC),
    )


def _acl(document_ref: str) -> FeishuAclResponse:
    return FeishuAclResponse(
        document_ref=document_ref,
        visibility=FeishuAclVisibility.PRIVATE,
        subjects=(
            FeishuPermissionSubject(FeishuPermissionKind.USER, "user:opaque"),
        ),
        observed_at=SYNTHETIC_OBSERVED_AT,
    )


def test_feishu_twin_runs_offline_without_network_or_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline Feishu twin attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    document = FeishuDocument("document:alpha", "revision:1", b"# Alpha\n")
    twin = SyntheticFeishuTwin(
        pages={
            None: FeishuChangePage((document,), (), None, "checkpoint:1"),
        },
        acl_responses={document.document_ref: _acl(document.document_ref)},
        identity_mappings={},
        group_snapshot=_empty_groups(),
    )
    adapter = FeishuDocsConnectorAdapter.from_twin(twin)
    adapter.load_checkpoint(None)

    page = adapter.load(_binding())

    assert tuple(item.document_ref for item in page.documents) == ("document:alpha",)
    assert page.terminal is True
    assert twin.network_accesses == 0
    assert twin.credential_accesses == 0


def test_feishu_twin_paginates_with_opaque_checkpoint_tokens() -> None:
    first = FeishuDocument("document:alpha", "revision:1", b"# Alpha\n")
    second = FeishuDocument("document:bravo", "revision:1", b"# Bravo\n")
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
    binding = _binding()
    adapter = FeishuDocsConnectorAdapter.from_twin(twin)
    adapter.load_checkpoint(None)
    first_page = adapter.load(binding)
    adapter.load_checkpoint(first_page.checkpoint_proposal)

    second_page = adapter.poll(binding)

    assert decode_feishu_checkpoint(first_page.checkpoint_proposal).page_token == (
        "page:2"
    )
    assert decode_feishu_checkpoint(second_page.checkpoint_proposal).page_token is None
    assert tuple(item.document_ref for item in second_page.documents) == (
        "document:bravo",
    )
    assert twin.page_calls == [None, "page:2"]


def test_feishu_rate_response_emits_no_page_or_checkpoint() -> None:
    twin = SyntheticFeishuTwin(
        pages={None: FeishuRateLimited(7)},
        acl_responses={},
        identity_mappings={},
        group_snapshot=_empty_groups(),
    )
    adapter = FeishuDocsConnectorAdapter.from_twin(twin)
    adapter.load_checkpoint(None)

    with pytest.raises(FeishuRateLimited) as failure:
        adapter.load(_binding())

    assert failure.value.retry_after_seconds == 7
    assert adapter.emitted_pages == []


@pytest.mark.parametrize(
    "failure",
    [FeishuRateLimited(7), FeishuSourceError("synthetic source error")],
    ids=["rate-limited", "source-error"],
)
def test_feishu_runner_twin_page_failure_emits_no_page_or_checkpoint(
    failure: FeishuSourceError,
) -> None:
    adapter = _process_adapter(pages={None: failure})
    adapter.load_checkpoint(None)

    with pytest.raises(RuntimeError, match="process is unavailable"):
        adapter.load(_binding())


@pytest.mark.parametrize("failure_surface", ["acl", "identity", "group"])
def test_feishu_runner_twin_replays_dependency_errors_offline(
    failure_surface: str,
) -> None:
    document = FeishuDocument("document:runner-outage", "revision:1", b"# Outage\n")
    subject = FeishuPermissionSubject(
        (
            FeishuPermissionKind.GROUP
            if failure_surface == "group"
            else FeishuPermissionKind.USER
        ),
        f"{failure_surface}:unavailable",
    )
    acl: FeishuAclResponse | FeishuSourceError = FeishuAclResponse(
        document.document_ref,
        FeishuAclVisibility.PRIVATE,
        (subject,),
        SYNTHETIC_OBSERVED_AT,
    )
    if failure_surface == "acl":
        acl = FeishuSourceError("synthetic ACL error")
    adapter = _process_adapter(
        pages={
            None: FeishuChangePage((document,), (), None, "checkpoint:1"),
        },
        acl_responses={document.document_ref: acl},
        identity_mappings=(
            {
                subject.external_ref: FeishuSourceError(
                    "synthetic identity error"
                )
            }
            if failure_surface == "identity"
            else {}
        ),
        group_snapshot=(
            FeishuSourceError("synthetic group error")
            if failure_surface == "group"
            else _empty_groups()
        ),
    )
    adapter.load_checkpoint(None)

    if failure_surface == "acl":
        with pytest.raises(RuntimeError, match="process is unavailable"):
            adapter.load(_binding())
        return
    page = adapter.load(_binding())
    payload = page.documents[0].acl_observation.evidence_payload
    assert payload is not None
    assert b'"status":"failed"' in payload


@pytest.mark.parametrize(
    "malformation",
    [
        "pages-object",
        "acl-object",
        "identity-object",
        "group-nodes-object",
        "group-identities-string",
        "group-children-string",
    ],
)
def test_feishu_runner_twin_rejects_non_list_fixture_containers(
    malformation: str,
) -> None:
    group = FeishuGroupNode("group:root", "group:local", (), ())
    payload = json.loads(
        serialize_feishu_twin_fixture(
            pages={
                None: FeishuChangePage((), (), None, "checkpoint:1"),
            },
            acl_responses={},
            identity_mappings={},
            group_snapshot=FeishuGroupSnapshot(
                "groups:v1", (group,), SYNTHETIC_OBSERVED_AT
            ),
        )
    )
    if malformation == "pages-object":
        payload["pages"] = {}
    elif malformation == "acl-object":
        payload["acl_responses"] = {}
    elif malformation == "identity-object":
        payload["identity_mappings"] = {}
    elif malformation == "group-nodes-object":
        payload["group_snapshot"]["nodes"] = {}
    elif malformation == "group-identities-string":
        payload["group_snapshot"]["nodes"][0]["identity_refs"] = "identity:a"
    else:
        payload["group_snapshot"]["nodes"][0]["child_group_refs"] = "group:a"

    malformed = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )
    with pytest.raises(ValueError, match="fixture is unavailable"):
        DeterministicFeishuTwin(malformed, policy_epoch=1)


@pytest.mark.parametrize("failure_surface", ["identity", "group"])
def test_feishu_runner_twin_rejects_rate_outcomes_on_non_rate_surfaces(
    failure_surface: str,
) -> None:
    with pytest.raises(TypeError, match="must be exact"):
        serialize_feishu_twin_fixture(
            pages={None: FeishuChangePage((), (), None, "checkpoint:1")},
            acl_responses={},
            identity_mappings=(
                {"identity:rate": FeishuRateLimited(7)}
                if failure_surface == "identity"
                else {}
            ),
            group_snapshot=(
                FeishuRateLimited(7)
                if failure_surface == "group"
                else _empty_groups()
            ),
        )


def test_feishu_observed_at_is_aware_utc() -> None:
    with pytest.raises(ValueError, match="UTC"):
        FeishuAclResponse(
            document_ref="document:alpha",
            visibility=FeishuAclVisibility.PRIVATE,
            subjects=(),
            observed_at=datetime(2026, 7, 31, 8, 0),
        )
    assert SYNTHETIC_OBSERVED_AT.tzinfo is UTC


@pytest.mark.parametrize("failure_surface", ["identity", "group"])
def test_feishu_acl_dependency_outage_emits_failed_observation(
    failure_surface: str,
) -> None:
    document = FeishuDocument("document:outage", "revision:1", b"# Outage\n")
    subject = FeishuPermissionSubject(
        (
            FeishuPermissionKind.USER
            if failure_surface == "identity"
            else FeishuPermissionKind.GROUP
        ),
        f"{failure_surface}:unavailable",
    )
    twin = SyntheticFeishuTwin(
        pages={None: FeishuChangePage((document,), (), None, "checkpoint:1")},
        acl_responses={
            document.document_ref: FeishuAclResponse(
                document.document_ref,
                FeishuAclVisibility.PRIVATE,
                (subject,),
                SYNTHETIC_OBSERVED_AT,
            )
        },
        identity_mappings=(
            {
                subject.external_ref: FeishuSourceError(
                    "synthetic identity outage"
                )
            }
            if failure_surface == "identity"
            else {}
        ),
        group_snapshot=(
            FeishuSourceError("synthetic group outage")
            if failure_surface == "group"
            else _empty_groups()
        ),
    )
    adapter = FeishuDocsConnectorAdapter.from_twin(twin)
    adapter.load_checkpoint(None)

    page = adapter.load(_binding())

    payload = page.documents[0].acl_observation.evidence_payload
    assert payload is not None
    assert b'"status":"failed"' in payload
    assert b'"policy_kind":null' in payload
