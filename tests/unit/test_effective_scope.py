from dataclasses import FrozenInstanceError, fields
from itertools import permutations
from typing import Any, cast
from uuid import UUID

import pytest
from hypothesis import HealthCheck, given, seed, settings
from hypothesis import strategies as st

from engine.runtime.contracts import RequestNarrowing
from engine.runtime.scope import (
    MISSING_TRUSTED_SCOPE,
    OMITTED_REQUEST_NARROWING,
    EffectiveScope,
    MissingTrustedScope,
    OmittedRequestNarrowing,
    ScopeSet,
    ScopeTarget,
    TrustedScopeOperands,
    compute_effective_scope,
)

ORGANIZATION_ID = UUID("e64f0f99-a965-42d5-a12d-28d58c6b775a")
OTHER_ORGANIZATION_ID = UUID("f6f995e0-11f2-4506-a9a8-d7617025c7c1")


def test_scope_target_is_an_exact_immutable_finite_atom() -> None:
    source_target = ScopeTarget(
        organization_id=ORGANIZATION_ID,
        source_ref="source:exact",
        resource_ref=None,
    )
    resource_target = ScopeTarget(
        organization_id=ORGANIZATION_ID,
        source_ref=" source:exact ",
        resource_ref="resource:exact",
    )

    assert source_target.organization_id == ORGANIZATION_ID
    assert source_target.source_ref == "source:exact"
    assert source_target.resource_ref is None
    assert resource_target.source_ref == " source:exact "
    with pytest.raises(FrozenInstanceError):
        resource_target.resource_ref = "different"  # type: ignore[misc]

    target_repr = repr(resource_target)
    assert str(ORGANIZATION_ID) not in target_repr
    assert "source:exact" not in target_repr
    assert "resource:exact" not in target_repr


@pytest.mark.parametrize(
    ("field_name", "invalid", "error_type"),
    [
        ("organization_id", str(ORGANIZATION_ID), TypeError),
        ("source_ref", "", ValueError),
        ("source_ref", " \t\n", ValueError),
        ("source_ref", 7, ValueError),
        ("resource_ref", "", ValueError),
        ("resource_ref", " ", ValueError),
        ("resource_ref", 7, ValueError),
    ],
)
def test_scope_target_rejects_values_outside_the_minimal_atom(
    field_name: str,
    invalid: object,
    error_type: type[Exception],
) -> None:
    values: dict[str, object] = {
        "organization_id": ORGANIZATION_ID,
        "source_ref": "source:exact",
        "resource_ref": "resource:exact",
    }
    values[field_name] = invalid

    with pytest.raises(error_type):
        ScopeTarget(**cast(Any, values))


def test_scope_set_is_an_exact_nominal_immutable_finite_set() -> None:
    target = ScopeTarget(ORGANIZATION_ID, "source:one", "resource:one")
    scope = ScopeSet(frozenset({target}))

    assert type(scope.targets) is frozenset
    assert scope.targets == frozenset({target})
    with pytest.raises(FrozenInstanceError):
        scope.targets = frozenset()  # type: ignore[misc]
    assert str(ORGANIZATION_ID) not in repr(scope)
    assert "source:one" not in repr(scope)
    assert "resource:one" not in repr(scope)


def test_scope_set_rejects_subclassed_targets() -> None:
    class ScopeTargetSubclass(ScopeTarget):
        pass

    subclassed = ScopeTargetSubclass(ORGANIZATION_ID, "source:one", None)

    with pytest.raises(TypeError, match="ScopeSet targets"):
        ScopeSet(frozenset({subclassed}))


@pytest.mark.parametrize(
    "invalid",
    [set(), (), frozenset({"source:one"}), frozenset({object()})],
)
def test_scope_set_rejects_non_frozenset_or_non_target_values(invalid: object) -> None:
    with pytest.raises(TypeError, match="ScopeSet targets"):
        ScopeSet(cast(Any, invalid))


def test_missing_and_omitted_are_distinct_nominal_singletons() -> None:
    with pytest.raises(TypeError, match="not constructible"):
        MissingTrustedScope()
    with pytest.raises(TypeError, match="not constructible"):
        OmittedRequestNarrowing()

    assert type(MISSING_TRUSTED_SCOPE) is MissingTrustedScope
    assert type(OMITTED_REQUEST_NARROWING) is OmittedRequestNarrowing
    assert MISSING_TRUSTED_SCOPE is not cast(object, OMITTED_REQUEST_NARROWING)
    assert "MissingTrustedScope" in repr(MISSING_TRUSTED_SCOPE)
    assert "OmittedRequestNarrowing" in repr(OMITTED_REQUEST_NARROWING)


def test_trusted_scope_operands_have_seven_explicit_immutable_fields() -> None:
    scope = ScopeSet(frozenset())
    operands = TrustedScopeOperands(
        organization_boundary=scope,
        membership_rights=MISSING_TRUSTED_SCOPE,
        principal_grants=scope,
        agent_ceiling=scope,
        source_native_acl=scope,
        resource_acl=scope,
        purpose_policy=scope,
    )

    assert tuple(field.name for field in fields(operands)) == (
        "organization_boundary",
        "membership_rights",
        "principal_grants",
        "agent_ceiling",
        "source_native_acl",
        "resource_acl",
        "purpose_policy",
    )
    assert operands.membership_rights is MISSING_TRUSTED_SCOPE
    with pytest.raises(FrozenInstanceError):
        operands.agent_ceiling = scope  # type: ignore[misc]


@pytest.mark.parametrize(
    "invalid",
    [None, frozenset(), set(), object()],
)
def test_trusted_scope_operands_reject_non_nominal_values(invalid: object) -> None:
    scope = ScopeSet(frozenset())
    values: dict[str, object] = {
        "organization_boundary": scope,
        "membership_rights": scope,
        "principal_grants": scope,
        "agent_ceiling": scope,
        "source_native_acl": scope,
        "resource_acl": scope,
        "purpose_policy": scope,
    }

    for field_name in values:
        malformed = values | {field_name: invalid}
        with pytest.raises(TypeError, match=field_name):
            TrustedScopeOperands(**cast(Any, malformed))


def test_trusted_scope_operands_reject_forged_missing_sentinel() -> None:
    scope = ScopeSet(frozenset())
    forged_missing = object.__new__(MissingTrustedScope)

    with pytest.raises(TypeError, match="membership_rights"):
        TrustedScopeOperands(
            organization_boundary=scope,
            membership_rights=forged_missing,
            principal_grants=scope,
            agent_ceiling=scope,
            source_native_acl=scope,
            resource_acl=scope,
            purpose_policy=scope,
        )


def test_trusted_scope_operands_reject_scope_set_subclasses() -> None:
    class ScopeSetSubclass(ScopeSet):
        pass

    scope = ScopeSet(frozenset())
    subclassed = ScopeSetSubclass(frozenset())

    with pytest.raises(TypeError, match="organization_boundary"):
        TrustedScopeOperands(
            organization_boundary=subclassed,
            membership_rights=scope,
            principal_grants=scope,
            agent_ceiling=scope,
            source_native_acl=scope,
            resource_acl=scope,
            purpose_policy=scope,
        )


def make_target(source_ref: str, resource_ref: str | None) -> ScopeTarget:
    return ScopeTarget(ORGANIZATION_ID, source_ref, resource_ref)


TARGET_A = make_target("source:a", "resource:a")
TARGET_B = make_target("source:a", "resource:b")
TARGET_C = make_target("source:c", "resource:c")
ALL_TARGETS = ScopeSet(frozenset({TARGET_A, TARGET_B, TARGET_C}))
TARGET_POOL = (
    TARGET_A,
    TARGET_B,
    TARGET_C,
    make_target("source:a", None),
    make_target("source:d", "resource:d"),
    ScopeTarget(OTHER_ORGANIZATION_ID, "source:a", "resource:a"),
)
PROPERTY_SETTINGS = settings(
    max_examples=100,
    database=None,
    deadline=None,
    print_blob=True,
    suppress_health_check=(HealthCheck.too_slow,),
)
SCOPE_SET_STRATEGY = st.frozensets(
    st.sampled_from(TARGET_POOL),
    max_size=len(TARGET_POOL),
).map(ScopeSet)
SEVEN_SCOPE_SETS_STRATEGY = st.lists(
    SCOPE_SET_STRATEGY,
    min_size=7,
    max_size=7,
)
SOURCE_REFS_STRATEGY = st.lists(
    st.sampled_from(
        ("source:a", "source:c", "source:d", "source:unknown", "source:overbroad")
    ),
    min_size=1,
    max_size=5,
    unique=True,
).map(tuple)
RESOURCE_REFS_STRATEGY = st.lists(
    st.sampled_from(
        (
            "resource:a",
            "resource:b",
            "resource:c",
            "resource:d",
            "resource:unknown",
        )
    ),
    min_size=1,
    max_size=5,
    unique=True,
).map(tuple)
REQUEST_NARROWING_STRATEGY = st.one_of(
    st.builds(RequestNarrowing, source_refs=SOURCE_REFS_STRATEGY),
    st.builds(RequestNarrowing, resource_refs=RESOURCE_REFS_STRATEGY),
    st.builds(
        RequestNarrowing,
        source_refs=SOURCE_REFS_STRATEGY,
        resource_refs=RESOURCE_REFS_STRATEGY,
    ),
)


def trusted_operands(
    *scopes: ScopeSet | MissingTrustedScope,
) -> TrustedScopeOperands:
    if not scopes:
        scopes = (ALL_TARGETS,) * 7
    assert len(scopes) == 7
    return TrustedScopeOperands(*scopes)


def test_mutation_control_union_would_leak_noncommon_targets() -> None:
    left = ScopeSet(frozenset({TARGET_A, TARGET_B}))
    right = ScopeSet(frozenset({TARGET_B, TARGET_C}))

    effective = compute_effective_scope(
        trusted_operands(
            ALL_TARGETS,
            left,
            ALL_TARGETS,
            right,
            ALL_TARGETS,
            ALL_TARGETS,
            ALL_TARGETS,
        ),
        OMITTED_REQUEST_NARROWING,
    )

    assert type(effective) is EffectiveScope
    assert effective.targets == frozenset({TARGET_B})


def test_effective_scope_has_a_stable_order_independent_sha256_digest() -> None:
    first = EffectiveScope(frozenset({TARGET_A, TARGET_C, TARGET_B}))
    second = EffectiveScope(frozenset({TARGET_B, TARGET_A, TARGET_C}))

    assert first.digest == second.digest
    assert len(first.digest) == 64
    assert set(first.digest) <= set("0123456789abcdef")
    assert first.digest != EffectiveScope(frozenset()).digest


def test_digest_encoding_distinguishes_none_and_ambiguous_ref_boundaries() -> None:
    no_resource = make_target("source:a", None)
    empty_like_resource = make_target("source:a", "<none>")
    left_boundary = make_target("a", "bc")
    right_boundary = make_target("ab", "c")

    assert EffectiveScope(frozenset({no_resource})).digest != EffectiveScope(
        frozenset({empty_like_resource})
    ).digest
    assert EffectiveScope(frozenset({left_boundary})).digest != EffectiveScope(
        frozenset({right_boundary})
    ).digest


def test_effective_scope_repr_exposes_neither_identifiers_nor_digest() -> None:
    effective = EffectiveScope(frozenset({TARGET_A}))

    effective_repr = repr(effective)
    assert str(ORGANIZATION_ID) not in effective_repr
    assert TARGET_A.source_ref not in effective_repr
    assert cast(str, TARGET_A.resource_ref) not in effective_repr
    assert effective.digest not in effective_repr


def test_effective_scope_digest_is_derived_and_immutable() -> None:
    effective = EffectiveScope(frozenset({TARGET_A}))

    with pytest.raises(FrozenInstanceError):
        effective.digest = "0" * 64  # type: ignore[misc]
    with pytest.raises(TypeError):
        EffectiveScope(frozenset({TARGET_A}), digest="0" * 64)  # type: ignore[call-arg]


def test_effective_scope_rejects_non_nominal_targets() -> None:
    class ScopeTargetSubclass(ScopeTarget):
        pass

    with pytest.raises(TypeError, match="EffectiveScope targets"):
        EffectiveScope(
            frozenset(
                {ScopeTargetSubclass(ORGANIZATION_ID, "source:one", None)}
            )
        )


@pytest.mark.parametrize("missing_index", range(7))
def test_mutation_control_missing_or_empty_as_unrestricted_would_leak(
    missing_index: int,
) -> None:
    empty = ScopeSet(frozenset())
    missing_scopes: list[ScopeSet | MissingTrustedScope] = [ALL_TARGETS] * 7
    missing_scopes[missing_index] = MISSING_TRUSTED_SCOPE
    empty_scopes: list[ScopeSet | MissingTrustedScope] = [ALL_TARGETS] * 7
    empty_scopes[missing_index] = empty

    assert compute_effective_scope(
        trusted_operands(*missing_scopes), OMITTED_REQUEST_NARROWING
    ).targets == frozenset()
    assert compute_effective_scope(
        trusted_operands(*empty_scopes), OMITTED_REQUEST_NARROWING
    ).targets == frozenset()


def test_omitted_request_narrowing_is_identity() -> None:
    assert compute_effective_scope(
        trusted_operands(), OMITTED_REQUEST_NARROWING
    ).targets == ALL_TARGETS.targets


@pytest.mark.parametrize(
    ("narrowing", "expected"),
    [
        (RequestNarrowing(source_refs=("source:a",)), {TARGET_A, TARGET_B}),
        (RequestNarrowing(resource_refs=("resource:b",)), {TARGET_B}),
        (
            RequestNarrowing(
                source_refs=("source:a",), resource_refs=("resource:b",)
            ),
            {TARGET_B},
        ),
        (RequestNarrowing(source_refs=("source:unknown",)), set()),
        (RequestNarrowing(resource_refs=("resource:unknown",)), set()),
        (
            RequestNarrowing(
                source_refs=("source:a", "source:unknown"),
                resource_refs=("resource:b", "resource:unknown"),
            ),
            {TARGET_B},
        ),
    ],
)
def test_present_request_refs_are_exact_and_combined_as_and_filters(
    narrowing: RequestNarrowing,
    expected: set[ScopeTarget],
) -> None:
    effective = compute_effective_scope(trusted_operands(), narrowing)

    assert effective.targets == frozenset(expected)


def test_source_level_target_does_not_implicitly_match_a_resource_filter() -> None:
    source_level = make_target("source:a", None)
    scopes = ScopeSet(frozenset({source_level, TARGET_A}))

    effective = compute_effective_scope(
        trusted_operands(*(scopes,) * 7),
        RequestNarrowing(resource_refs=("resource:a",)),
    )

    assert effective.targets == frozenset({TARGET_A})


@pytest.mark.parametrize(
    ("operands", "narrowing", "error_match"),
    [
        (None, OMITTED_REQUEST_NARROWING, "trusted_operands"),
        (object(), OMITTED_REQUEST_NARROWING, "trusted_operands"),
        (trusted_operands(), None, "request_narrowing"),
        (trusted_operands(), object(), "request_narrowing"),
        (
            trusted_operands(),
            object.__new__(OmittedRequestNarrowing),
            "request_narrowing",
        ),
    ],
)
def test_oracle_rejects_values_outside_its_closed_nominal_inputs(
    operands: object,
    narrowing: object,
    error_match: str,
) -> None:
    with pytest.raises(TypeError, match=error_match):
        compute_effective_scope(cast(Any, operands), cast(Any, narrowing))


def test_seven_way_intersection_is_order_independent() -> None:
    scopes = (
        ALL_TARGETS,
        ScopeSet(frozenset({TARGET_A, TARGET_B})),
        ScopeSet(frozenset({TARGET_B, TARGET_C})),
        ScopeSet(frozenset({TARGET_B})),
        ALL_TARGETS,
        ALL_TARGETS,
        ALL_TARGETS,
    )

    results = {
        compute_effective_scope(
            trusted_operands(*ordered), OMITTED_REQUEST_NARROWING
        ).targets
        for ordered in permutations(scopes)
    }

    assert results == {frozenset({TARGET_B})}


@seed(20_260_721)
@PROPERTY_SETTINGS
@given(
    scopes=SEVEN_SCOPE_SETS_STRATEGY,
    order=st.permutations(tuple(range(7))),
)
def test_generated_intersection_is_commutative(
    scopes: list[ScopeSet],
    order: list[int],
) -> None:
    original = compute_effective_scope(
        trusted_operands(*scopes), OMITTED_REQUEST_NARROWING
    )
    reordered = compute_effective_scope(
        trusted_operands(*(scopes[index] for index in order)),
        OMITTED_REQUEST_NARROWING,
    )

    assert reordered == original


@seed(20_260_722)
@PROPERTY_SETTINGS
@given(scope=SCOPE_SET_STRATEGY)
def test_generated_intersection_is_idempotent(scope: ScopeSet) -> None:
    effective = compute_effective_scope(
        trusted_operands(*(scope,) * 7), OMITTED_REQUEST_NARROWING
    )

    assert effective.targets == scope.targets


@seed(20_260_723)
@PROPERTY_SETTINGS
@given(
    scopes=SEVEN_SCOPE_SETS_STRATEGY,
    operand_index=st.integers(min_value=0, max_value=6),
    use_missing=st.booleans(),
)
def test_generated_missing_and_empty_operands_are_absorbing(
    scopes: list[ScopeSet],
    operand_index: int,
    use_missing: bool,
) -> None:
    operands: list[ScopeSet | MissingTrustedScope] = list(scopes)
    operands[operand_index] = (
        MISSING_TRUSTED_SCOPE if use_missing else ScopeSet(frozenset())
    )

    effective = compute_effective_scope(
        trusted_operands(*operands), OMITTED_REQUEST_NARROWING
    )

    assert effective.targets == frozenset()


@seed(20_260_724)
@PROPERTY_SETTINGS
@given(
    scopes=SEVEN_SCOPE_SETS_STRATEGY,
    operand_index=st.integers(min_value=0, max_value=6),
    removed=SCOPE_SET_STRATEGY,
)
def test_generated_trusted_operand_narrowing_never_expands_effective_scope(
    scopes: list[ScopeSet],
    operand_index: int,
    removed: ScopeSet,
) -> None:
    original = compute_effective_scope(
        trusted_operands(*scopes), OMITTED_REQUEST_NARROWING
    )
    narrower_scopes = list(scopes)
    narrower_scopes[operand_index] = ScopeSet(
        scopes[operand_index].targets - removed.targets
    )

    narrower = compute_effective_scope(
        trusted_operands(*narrower_scopes), OMITTED_REQUEST_NARROWING
    )

    assert narrower.targets <= original.targets


@seed(20_260_725)
@PROPERTY_SETTINGS
@given(
    scope=SCOPE_SET_STRATEGY,
    narrowing=REQUEST_NARROWING_STRATEGY,
)
def test_generated_request_narrowing_never_expands_effective_scope(
    scope: ScopeSet,
    narrowing: RequestNarrowing,
) -> None:
    operands = trusted_operands(*(scope,) * 7)
    original = compute_effective_scope(operands, OMITTED_REQUEST_NARROWING)

    narrowed = compute_effective_scope(operands, narrowing)

    assert narrowed.targets <= original.targets
