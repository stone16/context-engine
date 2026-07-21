from __future__ import annotations

from dataclasses import FrozenInstanceError
from pickle import dumps
from typing import cast
from uuid import UUID

import pytest

from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import (
    MaterializedFragmentLocator,
    MaterializedProjectionPort,
    MaterializedProjectionSession,
    _close_materialized_projection_scope,
    _construct_materialized_projection_session,
    _locate_materialized_fragment,
    _open_materialized_projection_scope,
    _project_materialized_fragment_body,
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
        self.body_calls: list[MaterializedFragmentLocator] = []

    def locate(
        self,
        selected_candidate: CandidateRef,
    ) -> MaterializedFragmentLocator | None:
        self.locator_calls.append(selected_candidate)
        return locator()

    def project_body(
        self,
        selected_locator: MaterializedFragmentLocator,
    ) -> str | None:
        self.body_calls.append(selected_locator)
        return "authorized synthetic body"


def test_projection_session_is_nominal_lifetime_bound_and_nonserializable() -> None:
    with pytest.raises(TypeError):
        MaterializedProjectionSession()

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
    assert port.body_calls == []

    body = _project_materialized_fragment_body(session, selected_locator)

    assert body == "authorized synthetic body"
    assert port.locator_calls == [candidate()]
    assert port.body_calls == [locator()]
    _close_materialized_projection_scope(scope)


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
        _project_materialized_fragment_body(
            session,
            cast(MaterializedFragmentLocator, invalid),
        )
    _close_materialized_projection_scope(scope)
