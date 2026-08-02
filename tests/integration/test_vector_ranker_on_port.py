from __future__ import annotations

import inspect

import pytest

from adapters.exact_phrase import PostgreSQLExactPhraseCandidateIndex
from adapters.pgvector import PostgreSQLVectorCandidateIndex
from engine.runtime.content_io import CandidateIndex

pytestmark = pytest.mark.integration


def test_existing_pgvector_ranker_conforms_to_candidate_port() -> None:
    assert tuple(
        inspect.signature(CandidateIndex.prepare_budgeted_discovery).parameters
    ) == (
        "self",
        "request",
        "effective_scope",
        "budget",
        "active_embedding_profile_digest",
    )
    assert tuple(
        inspect.signature(
            PostgreSQLVectorCandidateIndex.prepare_budgeted_discovery
        ).parameters
    ) == (
        "self",
        "request",
        "effective_scope",
        "budget",
        "active_embedding_profile_digest",
    )
    assert tuple(inspect.signature(CandidateIndex.discover).parameters) == (
        "self",
        "request",
        "discovery_session",
        "effective_scope",
    )
    assert tuple(
        inspect.signature(PostgreSQLVectorCandidateIndex.discover).parameters
    ) == ("self", "request", "discovery_session", "effective_scope")
    assert tuple(
        inspect.signature(
            PostgreSQLExactPhraseCandidateIndex.prepare_budgeted_discovery
        ).parameters
    ) == (
        "self",
        "request",
        "effective_scope",
        "budget",
        "active_embedding_profile_digest",
    )
    assert tuple(
        inspect.signature(PostgreSQLExactPhraseCandidateIndex.discover).parameters
    ) == ("self", "request", "discovery_session", "effective_scope")


def test_pgvector_ranker_has_one_production_implementation() -> None:
    import adapters.pgvector as pgvector

    implementations = tuple(
        member
        for _name, member in inspect.getmembers(pgvector, inspect.isclass)
        if member.__module__ == "adapters.pgvector"
        and member.__name__.endswith("CandidateIndex")
    )
    assert implementations == (PostgreSQLVectorCandidateIndex,)
