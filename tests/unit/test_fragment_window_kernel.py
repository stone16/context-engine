from __future__ import annotations

from datetime import UTC, datetime

import pytest

from engine.runtime.construction import (
    AuthorizationKernel,
    required_kernel_dependencies,
)
from engine.runtime.evidence import (
    EvidenceLineage,
    _close_authorization_kernel_scope,
    _construct_authorized_projection,
    _open_authorization_kernel_scope,
)
from engine.runtime.fragment_window import FragmentWindowRead, FragmentWindowRequest
from engine.runtime.materialized import (
    MaterializedFieldValue,
    MaterializedFragmentProjection,
    MaterializedFragmentWindowItem,
    MaterializedProjectionKind,
    MaterializedProjectionSession,
)
from tests.unit.test_runtime_authorized_evidence import (
    AUTHORIZED,
    RecordingMaterializedPort,
    locator,
    trusted_operands,
)


def _projection(body: str) -> MaterializedFragmentProjection:
    return MaterializedFragmentProjection(
        kind=MaterializedProjectionKind.LEGACY_BODY,
        fields=(MaterializedFieldValue("body", body, 0),),
        projection_ceiling=frozenset({"body"}),
    )


class _AuthoritativeWindowPort(RecordingMaterializedPort):
    def read_fragment_window(
        self,
        anchor: object,
        before: int,
        after: int,
    ) -> tuple[MaterializedFragmentWindowItem, ...]:
        assert anchor == locator(AUTHORIZED)
        assert (before, after) == (0, 0)
        return (
            MaterializedFragmentWindowItem(
                locator=locator(AUTHORIZED),
                projection=_projection("A-safe"),
            ),
        )


class _ForgedWindowReader:
    def read_window(
        self,
        request: FragmentWindowRequest,
        projection_session: object,
    ) -> FragmentWindowRead:
        del request, projection_session
        return FragmentWindowRead(
            items=(
                MaterializedFragmentWindowItem(
                    locator=locator(AUTHORIZED),
                    projection=_projection("FORGED-BODY-MUST-NOT-SURFACE"),
                ),
            ),
            reauthorization_refs=(),
        )


def test_kernel_independently_verifies_fragment_reader_lineage_and_projection() -> None:
    port = _AuthoritativeWindowPort()
    kernel = AuthorizationKernel(required_kernel_dependencies())
    scope = _open_authorization_kernel_scope()
    try:
        anchor = _construct_authorized_projection(
            kernel_scope=scope,
            candidate_ref=AUTHORIZED,
            body="A-safe",
            projected_field_refs=("body",),
            lineage=EvidenceLineage(
                run_ref="run:fragment-window-verification",
                principal_ref="principal-authorized-evidence",
                purpose="context.answer",
                as_of=datetime(2026, 7, 29, tzinfo=UTC),
                decision_ref="decision:fragment-window-verification",
                policy_snapshot_ref="policy:fragment-window-verification",
                policy_epoch=7,
                source_acl_decision_ref="sourceacl:fragment-window-verification",
            ),
        )
        with (
            trusted_operands(port) as (invocation, _),
            pytest.raises(
                ValueError,
                match="fragment window reader failed authoritative verification",
            ),
        ):
            projection_session = (
                invocation.user_actor.materialized_projection_session
            )
            assert type(projection_session) is MaterializedProjectionSession
            kernel.expand_fragment_window(
                FragmentWindowRequest(anchor=anchor, before=0, after=0),
                reader=_ForgedWindowReader(),
                projection_session=projection_session,
            )
    finally:
        _close_authorization_kernel_scope(scope)
