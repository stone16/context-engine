from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import cast

import pytest
from hypothesis import given, seed, settings
from hypothesis import strategies as st

from engine.runtime.materialized import (
    MaterializedFieldValue,
    MaterializedFragmentLocator,
    MaterializedFragmentProjection,
    MaterializedProjectionKind,
    MaterializedProjectionPort,
    _close_materialized_projection_scope,
    _construct_materialized_projection_session,
    _open_materialized_projection_scope,
    _project_materialized_fragment,
)
from tests.unit.test_materialized_projection import locator

FIELD_VALUES = {
    "assignee": "alice",
    "priority": "high",
    "private_note": "secret",
    "status": "open",
}
FIELD_REFS = tuple(FIELD_VALUES)
FIELD_SUBSETS = st.sets(
    st.sampled_from(FIELD_REFS),
    min_size=1,
    max_size=len(FIELD_REFS),
)


class StructuredProjectionPort:
    def __init__(self, projection: MaterializedFragmentProjection | None) -> None:
        self.projection = projection
        self.calls: list[MaterializedFragmentLocator] = []

    def locate(self, candidate_ref: object) -> MaterializedFragmentLocator:
        del candidate_ref
        return locator()

    def project(
        self,
        selected_locator: MaterializedFragmentLocator,
    ) -> MaterializedFragmentProjection | None:
        self.calls.append(selected_locator)
        return self.projection


def projection(
    *field_refs: str,
    ceiling: frozenset[str] | None = None,
) -> MaterializedFragmentProjection:
    fields = tuple(
        MaterializedFieldValue(
            field_ref=field_ref,
            field_value=FIELD_VALUES[field_ref],
            ordinal=ordinal,
        )
        for ordinal, field_ref in enumerate(field_refs)
    )
    return MaterializedFragmentProjection(
        kind=MaterializedProjectionKind.STRUCTURED_FIELDS,
        fields=fields,
        projection_ceiling=(ceiling if ceiling is not None else frozenset(field_refs)),
    )


@seed(20_260_722)
@settings(max_examples=100, deadline=None)
@given(allowed=FIELD_SUBSETS, removed=FIELD_SUBSETS)
def test_generated_projection_narrowing_never_expands_or_leaks_denied_values(
    allowed: set[str],
    removed: set[str],
) -> None:
    broader_refs = tuple(ref for ref in FIELD_REFS if ref in allowed)
    narrower_ceiling = allowed - removed
    narrower_refs = tuple(ref for ref in broader_refs if ref in narrower_ceiling)

    broader = projection(*broader_refs, ceiling=frozenset(allowed))
    assert set(narrower_refs) <= set(broader.projected_field_refs)
    if not narrower_refs:
        assert narrower_ceiling == set()
        return

    narrower = projection(*narrower_refs, ceiling=frozenset(narrower_ceiling))
    denied_refs = set(broader.projected_field_refs) - set(narrower.projected_field_refs)
    assert set(narrower.projected_field_refs) <= set(broader.projected_field_refs)
    assert all(
        f"{denied_ref}={FIELD_VALUES[denied_ref]}" not in narrower.rendered_body
        for denied_ref in denied_refs
    )


def test_structured_projection_renders_only_ceiling_fields_canonically() -> None:
    selected = projection("status", ceiling=frozenset({"status"}))
    scope = _open_materialized_projection_scope()
    port = StructuredProjectionPort(selected)
    session = _construct_materialized_projection_session(
        authority_scope=scope,
        port=cast(MaterializedProjectionPort, port),
    )

    observed = _project_materialized_fragment(session, locator())

    assert observed == selected
    assert observed is not None
    assert observed.projected_field_refs == ("status",)
    assert observed.rendered_body == "status=open"
    assert "secret" not in repr(observed)
    assert port.calls == [locator()]
    with pytest.raises(FrozenInstanceError):
        observed.fields = ()  # type: ignore[misc]
    _close_materialized_projection_scope(scope)


@pytest.mark.parametrize("ceiling", (frozenset(), frozenset({"other"})))
def test_missing_or_nonmatching_projection_ceiling_absorbs_content(
    ceiling: frozenset[str],
) -> None:
    with pytest.raises(ValueError, match="projection.*ceiling|trusted ceiling"):
        projection("status", ceiling=ceiling)


def test_projection_rejects_a_field_outside_the_trusted_ceiling() -> None:
    with pytest.raises(ValueError, match="outside.*ceiling"):
        projection(
            "status",
            "private_note",
            ceiling=frozenset({"status"}),
        )


def test_projection_rejects_noncanonical_or_duplicate_field_order() -> None:
    with pytest.raises(ValueError, match="canonical ordinal"):
        MaterializedFragmentProjection(
            kind=MaterializedProjectionKind.STRUCTURED_FIELDS,
            fields=(
                MaterializedFieldValue(
                    field_ref="status",
                    field_value="open",
                    ordinal=1,
                ),
            ),
            projection_ceiling=frozenset({"status"}),
        )

    with pytest.raises(ValueError, match="unique"):
        MaterializedFragmentProjection(
            kind=MaterializedProjectionKind.STRUCTURED_FIELDS,
            fields=(
                MaterializedFieldValue(
                    field_ref="status",
                    field_value="open",
                    ordinal=0,
                ),
                MaterializedFieldValue(
                    field_ref="status",
                    field_value="closed",
                    ordinal=1,
                ),
            ),
            projection_ceiling=frozenset({"status"}),
        )


def test_legacy_body_is_an_explicit_single_field_not_a_wildcard() -> None:
    selected = MaterializedFragmentProjection(
        kind=MaterializedProjectionKind.LEGACY_BODY,
        fields=(
            MaterializedFieldValue(
                field_ref="body",
                field_value="authorized synthetic body",
                ordinal=0,
            ),
        ),
        projection_ceiling=frozenset({"body", "status"}),
    )

    assert selected.projected_field_refs == ("body",)
    assert selected.rendered_body == "authorized synthetic body"

    with pytest.raises(ValueError, match="legacy body"):
        MaterializedFragmentProjection(
            kind=MaterializedProjectionKind.LEGACY_BODY,
            fields=(
                MaterializedFieldValue(
                    field_ref="status",
                    field_value="open",
                    ordinal=0,
                ),
            ),
            projection_ceiling=frozenset({"status"}),
        )
