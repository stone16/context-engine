from __future__ import annotations

from dataclasses import FrozenInstanceError
from pickle import dumps
from typing import cast
from uuid import UUID

import pytest

from engine.runtime.evidence import MAX_PROJECTED_FIELD_REFS, CandidateRef
from engine.runtime.materialized import (
    MaterializedFieldValue,
    MaterializedFragmentLocator,
    MaterializedFragmentProjection,
    MaterializedProjectionKind,
    MaterializedProjectionPort,
    MaterializedProjectionSession,
    _close_materialized_projection_scope,
    _construct_materialized_projection_session,
    _locate_materialized_fragment,
    _open_materialized_projection_scope,
    _project_materialized_fragment,
)

ORGANIZATION_ID = UUID("81e18bca-86a1-478a-937d-7675c6fe69b0")


def candidate() -> CandidateRef:
    return CandidateRef(
        organization_id=ORGANIZATION_ID,
        source_ref="source:synthetic",
        resource_ref="resource:authorized",
        revision_ref="revision:active",
        fragment_ref="fragment:authorized",
    )


def locator() -> MaterializedFragmentLocator:
    return MaterializedFragmentLocator(
        organization_id=ORGANIZATION_ID,
        source_ref="source:synthetic",
        resource_ref="resource:authorized",
        revision_ref="revision:active",
        fragment_ref="fragment:authorized",
    )


class RecordingProjectionPort:
    def __init__(self) -> None:
        self.locator_calls: list[CandidateRef] = []
        self.projection_calls: list[MaterializedFragmentLocator] = []

    def discover_exact_phrase(self, phrase_digest: str) -> tuple[CandidateRef, ...]:
        del phrase_digest
        return ()

    def source_is_active(self, source_ref: UUID) -> bool:
        del source_ref
        return True

    def observe_publication(self, candidate_ref: CandidateRef) -> None:
        del candidate_ref

    def locate(
        self,
        selected_candidate: CandidateRef,
    ) -> MaterializedFragmentLocator | None:
        self.locator_calls.append(selected_candidate)
        return locator()

    def project(
        self,
        selected_locator: MaterializedFragmentLocator,
    ) -> MaterializedFragmentProjection | None:
        self.projection_calls.append(selected_locator)
        return MaterializedFragmentProjection(
            kind=MaterializedProjectionKind.LEGACY_BODY,
            fields=(
                MaterializedFieldValue(
                    field_ref="body",
                    field_value="authorized synthetic body",
                    ordinal=0,
                ),
            ),
            projection_ceiling=frozenset({"body"}),
        )


def test_projection_session_is_nominal_lifetime_bound_and_nonserializable() -> None:
    with pytest.raises(TypeError):
        MaterializedProjectionSession()

    class MissingLifecyclePort:
        def locate(self, *args: object) -> None:
            del args

        project = locate
        discover_exact_phrase = locate
        observe_publication = locate

    with pytest.raises(TypeError, match="port is incomplete"):
        _construct_materialized_projection_session(
            authority_scope=_open_materialized_projection_scope(),
            port=cast(MaterializedProjectionPort, MissingLifecyclePort()),
        )

    scope = _open_materialized_projection_scope()
    port = RecordingProjectionPort()
    session = _construct_materialized_projection_session(
        authority_scope=scope,
        port=cast(MaterializedProjectionPort, port),
    )

    assert "authorized synthetic body" not in repr(session)
    with pytest.raises(TypeError, match="not serializable"):
        dumps(session)
    with pytest.raises(FrozenInstanceError):
        session._port = port  # type: ignore[misc]

    _close_materialized_projection_scope(scope)
    with pytest.raises(ValueError, match="active materialized projection scope"):
        _locate_materialized_fragment(session, candidate())


def test_locator_is_content_free_and_body_projection_is_a_separate_operation(
) -> None:
    scope = _open_materialized_projection_scope()
    port = RecordingProjectionPort()
    session = _construct_materialized_projection_session(
        authority_scope=scope,
        port=cast(MaterializedProjectionPort, port),
    )

    selected_locator = _locate_materialized_fragment(session, candidate())

    assert selected_locator == locator()
    assert set(MaterializedFragmentLocator.__dataclass_fields__) == {
        "organization_id",
        "source_ref",
        "resource_ref",
        "revision_ref",
        "fragment_ref",
    }
    assert all(
        forbidden not in MaterializedFragmentLocator.__dataclass_fields__
        for forbidden in ("body", "content", "text", "snippet", "title", "path")
    )
    assert port.projection_calls == []

    projection = _project_materialized_fragment(session, selected_locator)

    assert projection is not None
    assert projection.rendered_body == "authorized synthetic body"
    assert projection.projected_field_refs == ("body",)
    assert port.locator_calls == [candidate()]
    assert port.projection_calls == [locator()]
    _close_materialized_projection_scope(scope)


def test_materialized_projection_rejects_more_than_the_public_field_bound() -> None:
    fields = tuple(
        MaterializedFieldValue(
            field_ref=f"field_{index}",
            field_value=f"value {index}",
            ordinal=index,
        )
        for index in range(MAX_PROJECTED_FIELD_REFS + 1)
    )
    maximum = MaterializedFragmentProjection(
        kind=MaterializedProjectionKind.STRUCTURED_FIELDS,
        fields=fields[:MAX_PROJECTED_FIELD_REFS],
        projection_ceiling=frozenset(field.field_ref for field in fields),
    )

    assert len(maximum.projected_field_refs) == MAX_PROJECTED_FIELD_REFS
    with pytest.raises(ValueError, match="at most 64"):
        MaterializedFragmentProjection(
            kind=MaterializedProjectionKind.STRUCTURED_FIELDS,
            fields=fields,
            projection_ceiling=frozenset(field.field_ref for field in fields),
        )


@pytest.mark.parametrize(
    "invalid",
    (
        None,
        object(),
        "body",
    ),
)
def test_projection_operations_require_exact_nominal_inputs(invalid: object) -> None:
    scope = _open_materialized_projection_scope()
    session = _construct_materialized_projection_session(
        authority_scope=scope,
        port=cast(MaterializedProjectionPort, RecordingProjectionPort()),
    )

    with pytest.raises(TypeError):
        _locate_materialized_fragment(session, cast(CandidateRef, invalid))
    with pytest.raises(TypeError):
        _project_materialized_fragment(
            session,
            cast(MaterializedFragmentLocator, invalid),
        )
    _close_materialized_projection_scope(scope)
