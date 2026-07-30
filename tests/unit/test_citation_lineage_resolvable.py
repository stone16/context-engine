from __future__ import annotations

import pytest

from ui.views import PublicDocumentInvalid, ask_view, verify_citation_lineage


def _answer_document(*, citation_open_ref: str | None) -> dict[str, object]:
    evidence_ref = "ev_" + "a" * 64
    return {
        "kind": "resolved",
        "package": {
            "runRef": "run_authorized-answer",
            "coverage": {"status": "sufficient"},
            "blocks": [
                {
                    "blockId": "block_" + "a" * 64,
                    "text": "Authorized answer context.",
                    "evidenceRefs": [evidence_ref],
                }
            ],
            "evidence": [
                {
                    "evidenceRef": evidence_ref,
                    "sourceRef": "source:file",
                    "resourceRef": "article:handbook",
                    "revisionRef": "11111111-1111-1111-1111-111111111111",
                    "fragmentRef": "fragment:introduction",
                    "policyEpoch": 3,
                    "citationOpenRef": citation_open_ref,
                }
            ],
        },
    }


def test_citation_lineage_resolvable() -> None:
    answer = verify_citation_lineage(
        ask_view(
            _answer_document(citation_open_ref="cor_authorized"),
            query="What changed?",
        ),
        {"cor_authorized": _answer_document(citation_open_ref="cor_authorized")},
    )

    assert answer.hits[0].evidence.resource_ref == "article:handbook"
    assert answer.hits[0].evidence.revision_ref.startswith("11111111-")
    assert answer.hits[0].evidence.fragment_ref == "fragment:introduction"
    assert answer.hits[0].evidence.citation_open_ref == "cor_authorized"
    assert answer.run_ref == "run_authorized-answer"


@pytest.mark.parametrize("citation_open_ref", [None, "", " "])
def test_unresolvable_citation_never_becomes_a_clean_answer(
    citation_open_ref: str | None,
) -> None:
    with pytest.raises(PublicDocumentInvalid):
        ask_view(
            _answer_document(citation_open_ref=citation_open_ref),
            query="What changed?",
        )


def test_nonblank_but_unresolvable_locator_is_refused() -> None:
    answer = ask_view(
        _answer_document(citation_open_ref="cor_missing"),
        query="What changed?",
    )

    with pytest.raises(PublicDocumentInvalid):
        verify_citation_lineage(answer, {})
