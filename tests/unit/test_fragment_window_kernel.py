from __future__ import annotations

from collections import deque
from datetime import UTC, datetime

import pytest

from engine.runtime.construction import (
    Runtime,
    _construct_authorization_kernel_and_selector,
    required_kernel_dependencies,
)
from engine.runtime.evidence import (
    EvidenceLineage,
    _close_authorization_kernel_scope,
    _construct_authorized_projection,
    _open_authorization_kernel_scope,
)
from engine.runtime.fragment_window import (
    FragmentWindowRead,
    FragmentWindowRequest,
    FragmentWindowSession,
    _read_fragment_window_session,
)
from engine.runtime.materialized import (
    MaterializedFieldValue,
    MaterializedFragmentProjection,
    MaterializedFragmentWindowItem,
    MaterializedProjectionKind,
    MaterializedProjectionSession,
)
from tests.unit.test_runtime_authorized_evidence import (
    AS_OF,
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
        expansion_candidates: tuple[object, ...],
    ) -> FragmentWindowRead:
        assert anchor == locator(AUTHORIZED)
        assert (before, after) == (0, 0)
        assert expansion_candidates == ()
        return FragmentWindowRead(
            items=(
                MaterializedFragmentWindowItem(
                    locator=locator(AUTHORIZED),
                    projection=_projection("A-safe"),
                ),
            ),
            reauthorization_refs=(),
        )


class _ForgedWindowReader:
    def read_window(
        self,
        request: FragmentWindowRequest,
        window_session: FragmentWindowSession,
    ) -> FragmentWindowRead:
        del request, window_session
        return FragmentWindowRead(
            items=(
                MaterializedFragmentWindowItem(
                    locator=locator(AUTHORIZED),
                    projection=_projection("FORGED-BODY-MUST-NOT-SURFACE"),
                ),
            ),
            reauthorization_refs=(),
        )


class _ReachableContentAttackReader:
    def __init__(self) -> None:
        self.arbitrary_content_capability_was_reachable = False

    def read_window(
        self,
        request: FragmentWindowRequest,
        window_session: FragmentWindowSession,
    ) -> FragmentWindowRead:
        queue = deque((request, window_session))
        seen: set[int] = set()
        while queue:
            reachable = queue.popleft()
            if id(reachable) in seen:
                continue
            seen.add(id(reachable))
            for name in ("project", "locate", "read_fragment_window"):
                if callable(getattr(reachable, name, None)):
                    self.arbitrary_content_capability_was_reachable = True
            slots = getattr(type(reachable), "__slots__", ())
            if isinstance(slots, str):
                slots = (slots,)
            for slot in slots:
                try:
                    value = getattr(reachable, slot)
                except AttributeError:
                    continue
                if value is not reachable:
                    queue.append(value)
            attributes = getattr(reachable, "__dict__", None)
            if isinstance(attributes, dict):
                queue.extend(attributes.values())
        return _read_fragment_window_session(window_session)


class _AliasingMutationReader:
    def read_window(
        self,
        request: FragmentWindowRequest,
        window_session: FragmentWindowSession,
    ) -> FragmentWindowRead:
        del request
        read = _read_fragment_window_session(window_session)
        object.__setattr__(
            read.items[0].projection.fields[0],
            "field_value",
            "FORGED-THROUGH-ALIASED-READ",
        )
        return read


class _SourceAclLineageMutationReader:
    def read_window(
        self,
        request: FragmentWindowRequest,
        window_session: FragmentWindowSession,
    ) -> FragmentWindowRead:
        del request
        read = _read_fragment_window_session(window_session)
        object.__setattr__(
            read.items[0].locator,
            "source_acl_projection_ref",
            "sourceacl_projection:forged",
        )
        return read


def test_kernel_independently_verifies_fragment_reader_lineage_and_projection() -> None:
    port = _AuthoritativeWindowPort()
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
                source_acl_projection_ref="sourceacl_runtime-authorized",
                source_acl_as_of=AS_OF,
            ),
        )
        with (
            trusted_operands(port) as (invocation, _),
            pytest.raises(
                ValueError,
                match="fragment window reader failed authoritative verification",
            ),
        ):
            projection_session = invocation.user_actor.materialized_projection_session
            assert type(projection_session) is MaterializedProjectionSession
            kernel, _selector = _construct_authorization_kernel_and_selector(
                required_kernel_dependencies(),
                fragment_window_reader=_ForgedWindowReader(),
            )
            kernel.expand_fragment_window(
                FragmentWindowRequest(anchor=anchor, before=0, after=0),
                projection_session=projection_session,
            )
    finally:
        _close_authorization_kernel_scope(scope)


def test_fragment_window_is_not_a_second_runtime_delivery_surface() -> None:
    assert not hasattr(Runtime, "expand_fragment_window")


def test_fragment_reader_cannot_reach_an_arbitrary_content_capability() -> None:
    port = _AuthoritativeWindowPort()
    reader = _ReachableContentAttackReader()
    scope = _open_authorization_kernel_scope()
    try:
        anchor = _construct_authorized_projection(
            kernel_scope=scope,
            candidate_ref=AUTHORIZED,
            body="A-safe",
            projected_field_refs=("body",),
            lineage=EvidenceLineage(
                run_ref="run:fragment-window-capability",
                principal_ref="principal-authorized-evidence",
                purpose="context.answer",
                as_of=datetime(2026, 7, 29, tzinfo=UTC),
                decision_ref="decision:fragment-window-capability",
                policy_snapshot_ref="policy:fragment-window-capability",
                policy_epoch=7,
                source_acl_decision_ref="sourceacl:fragment-window-capability",
                source_acl_projection_ref="sourceacl_runtime-authorized",
                source_acl_as_of=AS_OF,
            ),
        )
        with trusted_operands(port) as (invocation, _):
            projection_session = invocation.user_actor.materialized_projection_session
            assert type(projection_session) is MaterializedProjectionSession
            kernel, _selector = _construct_authorization_kernel_and_selector(
                required_kernel_dependencies(),
                fragment_window_reader=reader,
            )
            result = kernel.expand_fragment_window(
                FragmentWindowRequest(anchor=anchor, before=0, after=0),
                projection_session=projection_session,
            )
        assert reader.arbitrary_content_capability_was_reachable is False
        assert tuple(item.projected_body for item in result.projections) == ("A-safe",)
    finally:
        _close_authorization_kernel_scope(scope)


def test_fragment_reader_cannot_mutate_both_sides_of_authoritative_comparison() -> None:
    port = _AuthoritativeWindowPort()
    scope = _open_authorization_kernel_scope()
    try:
        anchor = _construct_authorized_projection(
            kernel_scope=scope,
            candidate_ref=AUTHORIZED,
            body="A-safe",
            projected_field_refs=("body",),
            lineage=EvidenceLineage(
                run_ref="run:fragment-window-alias",
                principal_ref="principal-authorized-evidence",
                purpose="context.answer",
                as_of=datetime(2026, 7, 29, tzinfo=UTC),
                decision_ref="decision:fragment-window-alias",
                policy_snapshot_ref="policy:fragment-window-alias",
                policy_epoch=7,
                source_acl_decision_ref="sourceacl:fragment-window-alias",
                source_acl_projection_ref="sourceacl_runtime-authorized",
                source_acl_as_of=AS_OF,
            ),
        )
        with (
            trusted_operands(port) as (invocation, _),
            pytest.raises(
                ValueError,
                match="fragment window reader failed authoritative verification",
            ),
        ):
            projection_session = invocation.user_actor.materialized_projection_session
            assert type(projection_session) is MaterializedProjectionSession
            kernel, _selector = _construct_authorization_kernel_and_selector(
                required_kernel_dependencies(),
                fragment_window_reader=_AliasingMutationReader(),
            )
            kernel.expand_fragment_window(
                FragmentWindowRequest(anchor=anchor, before=0, after=0),
                projection_session=projection_session,
            )
    finally:
        _close_authorization_kernel_scope(scope)


def test_fragment_reader_cannot_mutate_inherited_source_acl_lineage() -> None:
    port = _AuthoritativeWindowPort()
    scope = _open_authorization_kernel_scope()
    source_acl_as_of = datetime(2026, 7, 29, tzinfo=UTC)
    try:
        anchor = _construct_authorized_projection(
            kernel_scope=scope,
            candidate_ref=AUTHORIZED,
            body="A-safe",
            projected_field_refs=("body",),
            lineage=EvidenceLineage(
                run_ref="run:fragment-window-source-acl",
                principal_ref="principal-authorized-evidence",
                purpose="context.answer",
                as_of=source_acl_as_of,
                decision_ref="decision:fragment-window-source-acl",
                policy_snapshot_ref="policy:fragment-window-source-acl",
                policy_epoch=7,
                source_acl_decision_ref="sourceacl:fragment-window-source-acl",
                source_acl_projection_ref="sourceacl_runtime-authorized",
                source_acl_as_of=AS_OF,
            ),
        )
        with (
            trusted_operands(port) as (invocation, _),
            pytest.raises(
                ValueError,
                match="fragment window reader failed authoritative verification",
            ),
        ):
            projection_session = invocation.user_actor.materialized_projection_session
            assert type(projection_session) is MaterializedProjectionSession
            kernel, _selector = _construct_authorization_kernel_and_selector(
                required_kernel_dependencies(),
                fragment_window_reader=_SourceAclLineageMutationReader(),
            )
            kernel.expand_fragment_window(
                FragmentWindowRequest(anchor=anchor, before=0, after=0),
                projection_session=projection_session,
            )
    finally:
        _close_authorization_kernel_scope(scope)
