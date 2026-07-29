"""PostgreSQL same-Article/current-Revision authorized Fragment window."""

from __future__ import annotations

from engine.runtime.evidence import _require_active_authorized_projection
from engine.runtime.fragment_window import (
    FragmentWindowNotAvailable,
    FragmentWindowRead,
    FragmentWindowRequest,
)
from engine.runtime.materialized import (
    MaterializedFragmentLocator,
    MaterializedProjectionSession,
    _read_materialized_fragment_window,
)


class PostgreSQLFragmentWindowReader:
    """Read inherited content only after active Article lineage verification."""

    def read_window(
        self,
        request: FragmentWindowRequest,
        projection_session: MaterializedProjectionSession,
    ) -> FragmentWindowRead:
        if type(request) is not FragmentWindowRequest:
            raise TypeError("fragment window requires FragmentWindowRequest")
        _require_active_authorized_projection(request.anchor)
        anchor_ref = request.anchor.candidate_ref
        locator = MaterializedFragmentLocator(
            organization_id=anchor_ref.organization_id,
            source_ref=anchor_ref.source_ref,
            resource_ref=anchor_ref.resource_ref,
            revision_ref=anchor_ref.revision_ref,
            fragment_ref=anchor_ref.fragment_ref,
        )
        items = _read_materialized_fragment_window(
            projection_session,
            locator,
            request.before,
            request.after,
        )
        if not items or all(
            item.locator.fragment_ref != anchor_ref.fragment_ref for item in items
        ):
            raise FragmentWindowNotAvailable("Fragment window is not available")
        reauthorization_refs = tuple(
            candidate
            for candidate in request.expansion_candidates
            if (
                candidate.organization_id != anchor_ref.organization_id
                or candidate.source_ref != anchor_ref.source_ref
                or candidate.resource_ref != anchor_ref.resource_ref
            )
        )
        return FragmentWindowRead(
            items=items,
            reauthorization_refs=tuple(dict.fromkeys(reauthorization_refs)),
        )
