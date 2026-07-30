from __future__ import annotations

from uuid import UUID

import pytest

from engine.runtime.authorized_ranking import AuthorizedRerankItem
from engine.runtime.evidence import CandidateRef


def test_authorized_rerank_item_runtime_refuses_raw_candidate_ref() -> None:
    candidate = CandidateRef(
        organization_id=UUID("81e18bca-86a1-478a-937d-7675c6fe69b0"),
        source_ref="source:rerank",
        resource_ref="resource:rerank",
        revision_ref="05b82c43-4e8f-49ae-a286-a40289a3413e",
        fragment_ref="fragment:rerank",
    )

    with pytest.raises(TypeError, match="requires AuthorizedProjection"):
        AuthorizedRerankItem(candidate)  # type: ignore[arg-type]
