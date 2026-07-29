"""PostgreSQL same-Article/current-Revision authorized Fragment window."""

from __future__ import annotations

from engine.runtime.evidence import _require_active_authorized_projection
from engine.runtime.fragment_window import (
    FragmentWindowNotAvailable,
    FragmentWindowRequest,
    FragmentWindowSession,
    _read_fragment_window_session,
)
from engine.runtime.materialized import (
    MaterializedFragmentWindowRead,
)


class PostgreSQLFragmentWindowReader:
    """Read inherited content only after active Article lineage verification."""

    def read_window(
        self,
        request: FragmentWindowRequest,
        window_session: FragmentWindowSession,
    ) -> MaterializedFragmentWindowRead:
        if type(request) is not FragmentWindowRequest:
            raise TypeError("fragment window requires FragmentWindowRequest")
        _require_active_authorized_projection(request.anchor)
        anchor_ref = request.anchor.candidate_ref
        read = _read_fragment_window_session(window_session)
        if not read.items or all(
            item.locator.fragment_ref != anchor_ref.fragment_ref
            for item in read.items
        ):
            raise FragmentWindowNotAvailable("Fragment window is not available")
        return read
