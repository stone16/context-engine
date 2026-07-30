from __future__ import annotations

from collections import deque
from typing import Any, cast

import pytest

from engine.runtime.budget import PackageBudget
from engine.runtime.candidate_ranking import (
    CandidateQuery,
    RankedCandidate,
    RankedCandidateList,
)
from engine.runtime.construction import (
    AuthorizationKernel,
    Runtime,
    SealedPackageSelection,
    SealedRuntimeSelector,
    _construct_authorization_kernel_and_selector,
    _OpaqueReferenceIssuer,
    required_kernel_dependencies,
)
from engine.runtime.content_io import CandidateIndex
from engine.runtime.contracts import Acquire, ContextNeed, Resolved
from engine.runtime.evidence import construct_package_content
from tests.support.context_run import TEST_QUERY_DIGEST_KEYRING
from tests.unit.test_runtime_authorized_evidence import (
    AS_OF,
    AUTHORIZED,
    AUTHORIZED_SECOND,
    DENIED,
    HostileCandidateIndex,
    RecordingMaterializedPort,
    locator,
    scope_for,
    trusted_operands,
)


def _prepare_exact_phrase(*args: Any, **kwargs: Any) -> object:
    return HostileCandidateIndex(()).prepare_discovery(*args, **kwargs)


class _ReachableContentAttackIndex:
    """Reproduce content read/mutation through the pre-Kernel seam argument."""

    def __init__(self) -> None:
        self.content_was_reachable = False
        self.content_was_read = False

    prepare_discovery = staticmethod(_prepare_exact_phrase)

    def discover(self, *args: Any, **kwargs: Any) -> CandidateQuery:
        del kwargs
        queue = deque(args)
        seen: set[int] = set()
        while queue:
            reachable = queue.popleft()
            if id(reachable) in seen:
                continue
            seen.add(id(reachable))
            for name in ("project", "locate", "read_fragment_window"):
                if callable(getattr(reachable, name, None)):
                    self.content_was_reachable = True
            if isinstance(reachable, dict):
                queue.extend(reachable.keys())
                queue.extend(reachable.values())
            elif isinstance(reachable, tuple | list | set | frozenset):
                queue.extend(reachable)
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
            if not self.content_was_reachable:
                continue
            locate_candidate = getattr(reachable, "locate", None)
            project_locator = getattr(reachable, "project", None)
            if callable(locate_candidate) and callable(project_locator):
                selected = locate_candidate(AUTHORIZED)
                projection = project_locator(selected)
                self.content_was_read = projection is not None
                body_by_candidate = getattr(reachable, "body_by_candidate", None)
                if isinstance(body_by_candidate, dict):
                    body_by_candidate[AUTHORIZED] = (
                        "FORGED-FROM-RANK-SIDE-CHANNEL"
                    )
        return CandidateQuery(
            ranked_lists=(
                RankedCandidateList(
                    ranker_ref="hostile",
                    candidates=(RankedCandidate(candidate_ref=AUTHORIZED),),
                ),
            )
        )


class _RestoreAfterProjectionScopeAttackIndex:
    """Widen shared scope, then restore it after the Kernel's membership check."""

    def __init__(self) -> None:
        self.scope_was_mutated = False
        self.restore_ran = False

    prepare_discovery = staticmethod(_prepare_exact_phrase)

    def discover(self, *args: Any, **kwargs: Any) -> CandidateQuery:
        scope = kwargs.get("effective_scope")
        if scope is not None:
            original_scope = str(scope)
            try:
                object.__setattr__(scope, "targets", scope_for(DENIED).targets)
            except (AttributeError, TypeError):
                return CandidateQuery(
                    ranked_lists=(
                        RankedCandidateList(
                            ranker_ref="hostile",
                            candidates=(RankedCandidate(candidate_ref=DENIED),),
                        ),
                    )
                )
            self.scope_was_mutated = True
            for argument in args:
                port = getattr(argument, "_port", None)
                if port is None:
                    continue
                original_project = port.project

                def restoring_project(
                    selected: object,
                    original_project: Any = original_project,
                ) -> object:
                    projected = original_project(selected)
                    object.__setattr__(scope, "targets", original_scope)
                    self.restore_ran = True
                    return projected

                port.project = restoring_project
        return CandidateQuery(
            ranked_lists=(
                RankedCandidateList(
                    ranker_ref="hostile",
                    candidates=(RankedCandidate(candidate_ref=DENIED),),
                ),
            )
        )


class _MalformedDiscoveryRequestIndex:
    """Return a non-contract discovery request before the adapter is invoked."""

    def prepare_discovery(self, *args: Any, **kwargs: Any) -> object:
        del args, kwargs
        return object()

    def discover(self, *args: Any, **kwargs: Any) -> CandidateQuery:
        del args, kwargs
        raise AssertionError("invalid discovery request must fail before the adapter")


def _resolve_with(index: object, port: RecordingMaterializedPort) -> Resolved:
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )
    with trusted_operands(port) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="adversarial candidate seam")),
        )
    assert type(outcome) is Resolved
    return outcome


def test_candidate_seam_has_no_reachable_content_capability_or_mutation_path() -> None:
    attack = _ReachableContentAttackIndex()
    port = RecordingMaterializedPort()

    outcome = _resolve_with(attack, port)

    assert attack.content_was_reachable is False
    assert attack.content_was_read is False
    assert tuple(block.body for block in outcome.package.blocks) == ("A-safe",)
    assert port.body_calls == [locator(AUTHORIZED)]


def test_candidate_seam_cannot_mutate_or_restore_kernel_effective_scope() -> None:
    attack = _RestoreAfterProjectionScopeAttackIndex()
    port = RecordingMaterializedPort()

    outcome = _resolve_with(attack, port)

    assert attack.scope_was_mutated is False
    assert attack.restore_ran is False
    assert port.body_calls == []
    assert outcome.package.blocks == ()


def test_normal_candidate_index_still_uses_the_sealed_authorized_path() -> None:
    outcome = _resolve_with(
        HostileCandidateIndex((AUTHORIZED,)),
        RecordingMaterializedPort(),
    )

    assert tuple(block.body for block in outcome.package.blocks) == ("A-safe",)


def test_discovery_construction_preserves_the_authority_error() -> None:
    with pytest.raises(TypeError, match="candidate discovery request"):
        _resolve_with(
            _MalformedDiscoveryRequestIndex(),
            RecordingMaterializedPort(),
        )


def test_finalizer_rederives_exact_budget_selection_and_refuses_forgery() -> None:
    kernel, selector = _construct_authorization_kernel_and_selector(
        required_kernel_dependencies()
    )
    port = RecordingMaterializedPort()
    with trusted_operands(port) as (invocation, delivery):
        allowed = scope_for(AUTHORIZED, AUTHORIZED_SECOND)
        for operand_name in (
            "organization_boundary",
            "membership_rights",
            "principal_grants",
            "agent_ceiling",
            "source_native_acl",
            "resource_acl",
            "purpose_policy",
        ):
            object.__setattr__(
                invocation.trusted_scope_snapshot,
                operand_name,
                allowed,
            )
        preparation = kernel.prepare_acquire(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="sealed budget attack")),
            server_budget=PackageBudget(6, 1, 1, 1),
            as_of=AS_OF,
            reference_issuer=_OpaqueReferenceIssuer(),
        )
        decision = kernel.authorize_acquire(
            invocation,
            preparation,
            (AUTHORIZED, AUTHORIZED_SECOND),
            projection_session=(
                invocation.user_actor.materialized_projection_session
            ),
        )
        selection = selector.select_for_delivery(decision, ())
        forged = SealedPackageSelection(
            decision=decision,
            content=construct_package_content(decision.projections),
            effective_budget_limits=(12, 1, 1, 1),
            integrity_seal=b"forged",
        )

        with pytest.raises(ValueError, match="sealed selection integrity"):
            kernel.finalize_for_delivery(
                invocation,
                forged,
            )

        object.__setattr__(selection, "content", construct_package_content(()))
        with pytest.raises(ValueError, match="sealed selection integrity"):
            kernel.finalize_for_delivery(
                invocation,
                selection,
            )


def test_coordinated_budget_mutation_and_fresh_selector_cannot_forge_selection() -> (
    None
):
    kernel, selector = _construct_authorization_kernel_and_selector(
        required_kernel_dependencies()
    )
    port = RecordingMaterializedPort()
    with trusted_operands(port) as (invocation, delivery):
        allowed = scope_for(AUTHORIZED, AUTHORIZED_SECOND)
        for operand_name in (
            "organization_boundary",
            "membership_rights",
            "principal_grants",
            "agent_ceiling",
            "source_native_acl",
            "resource_acl",
            "purpose_policy",
        ):
            object.__setattr__(
                invocation.trusted_scope_snapshot,
                operand_name,
                allowed,
            )
        preparation = kernel.prepare_acquire(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="coordinated sealed budget attack")),
            server_budget=PackageBudget(6, 1, 1, 1),
            as_of=AS_OF,
            reference_issuer=_OpaqueReferenceIssuer(),
        )
        decision = kernel.authorize_acquire(
            invocation,
            preparation,
            (AUTHORIZED, AUTHORIZED_SECOND),
            projection_session=(
                invocation.user_actor.materialized_projection_session
            ),
        )
        selection = selector.select_for_delivery(decision, ())
        object.__setattr__(decision, "effective_budget", PackageBudget(12, 1, 1, 1))
        object.__setattr__(decision, "_effective_budget_limits", (12, 1, 1, 1))

        with pytest.raises(ValueError, match="sealed selection integrity"):
            kernel.finalize_for_delivery(invocation, selection)
        with pytest.raises(TypeError, match="only be constructed by Runtime"):
            SealedRuntimeSelector()
        with pytest.raises(
            RuntimeError,
            match="Runtime-owned selection authority",
        ):
            AuthorizationKernel(
                required_kernel_dependencies(),
                _selection_authority=object(),  # type: ignore[arg-type]
            )
        assert not hasattr(kernel, "_selection_secret")
        assert not hasattr(selector, "_selection_secret")
        assert not hasattr(kernel, "_selection_authority")
        assert not hasattr(selector, "_selection_authority")
        with pytest.raises(ValueError, match="authorization decision integrity"):
            selector.select_for_delivery(decision, ())
