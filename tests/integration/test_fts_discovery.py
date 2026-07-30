from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.persistence.membership_context import _FTS_CANDIDATE_SQL
from engine.runtime.evidence import CandidateRef

pytestmark = pytest.mark.integration


def _cleanup(engine: Engine, organization_id: UUID) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE context_fragment DISABLE TRIGGER "
                "context_fragment_reject_mutation"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE context_revision DISABLE TRIGGER "
                "context_revision_reject_mutation"
            )
        )
    try:
        with engine.begin() as connection:
            for table in (
                "context_fragment",
                "context_revision",
                "context_resource",
                "organization",
            ):
                connection.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :o"),
                    {"o": organization_id},
                )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE context_fragment ENABLE TRIGGER "
                    "context_fragment_reject_mutation"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE context_revision ENABLE TRIGGER "
                    "context_revision_reject_mutation"
                )
            )


def _parameters(candidate: CandidateRef) -> dict[str, object]:
    return {
        "query_text": "transactional quokka",
        "limit": 64,
        "source_refs": None,
        "resource_refs": None,
        "scope_resource_organization_ids": [candidate.organization_id],
        "scope_resource_source_refs": [candidate.source_ref],
        "scope_resource_refs": [candidate.resource_ref],
    }


def test_fts_publication_is_discoverable_and_supersession_is_atomic(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """One transaction observes its new Fragment and active-pointer replacement."""

    organization_id = uuid4()
    resource_ref = f"resource:fts:{uuid4()}"
    source_ref = f"source:fts:{uuid4()}"
    first_revision = uuid4()
    second_revision = uuid4()
    first = CandidateRef(
        organization_id=organization_id,
        source_ref=source_ref,
        resource_ref=resource_ref,
        revision_ref=str(first_revision),
        fragment_ref="fragment:first",
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization VALUES (:organization_id)"),
                {"organization_id": organization_id},
            )
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    """
                    INSERT INTO context_resource (
                        organization_id, resource_ref, source_ref,
                        active_revision_id, tombstoned
                    ) VALUES (
                        :organization_id, :resource_ref, :source_ref,
                        :revision_id, false
                    )
                    """
                ),
                {
                    "organization_id": organization_id,
                    "resource_ref": resource_ref,
                    "source_ref": source_ref,
                    "revision_id": first_revision,
                },
            )
            for revision_id, fragment_ref in (
                (first_revision, "fragment:first"),
                (second_revision, "fragment:second"),
            ):
                connection.execute(
                    text("INSERT INTO context_revision VALUES (:o, :r, :v)"),
                    {"o": organization_id, "r": resource_ref, "v": revision_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO context_fragment (
                            organization_id, resource_ref, revision_id,
                            fragment_ref, ordinal, content, projection_kind
                        ) VALUES (:o, :r, :v, :f, 0, :content, 'body')
                        """
                    ),
                    {
                        "o": organization_id,
                        "r": resource_ref,
                        "v": revision_id,
                        "f": fragment_ref,
                        "content": "transactional quokka handbook",
                    },
                )
                if revision_id == first_revision:
                    assert tuple(
                        tuple(row)
                        for row in connection.execute(
                            text(_FTS_CANDIDATE_SQL),
                            _parameters(first),
                        )
                    ) == (
                        (
                            organization_id,
                            source_ref,
                            resource_ref,
                            first_revision,
                            "fragment:first",
                        ),
                    )
            connection.execute(
                text(
                    "UPDATE context_resource SET active_revision_id = :revision_id "
                    "WHERE organization_id = :organization_id "
                    "AND resource_ref = :resource_ref"
                ),
                {
                    "revision_id": second_revision,
                    "organization_id": organization_id,
                    "resource_ref": resource_ref,
                },
            )
            assert tuple(
                tuple(row)
                for row in connection.execute(
                    text(_FTS_CANDIDATE_SQL),
                    _parameters(first),
                )
            ) == (
                (
                    organization_id,
                    source_ref,
                    resource_ref,
                    second_revision,
                    "fragment:second",
                ),
            )
    finally:
        _cleanup(engine, organization_id)
        engine.dispose()


def test_fts_tombstone_stops_discovery_in_the_same_transaction(
    migration_configuration: DatabaseConfiguration,
) -> None:
    organization_id = uuid4()
    revision_id = uuid4()
    resource_ref = f"resource:fts-tombstone:{uuid4()}"
    source_ref = f"source:fts-tombstone:{uuid4()}"
    candidate = CandidateRef(
        organization_id=organization_id,
        source_ref=source_ref,
        resource_ref=resource_ref,
        revision_ref=str(revision_id),
        fragment_ref="fragment:tombstone",
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization VALUES (:o)"), {"o": organization_id}
            )
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text("INSERT INTO context_resource VALUES (:o, :r, :s, :v, false)"),
                {
                    "o": organization_id,
                    "r": resource_ref,
                    "s": source_ref,
                    "v": revision_id,
                },
            )
            connection.execute(
                text("INSERT INTO context_revision VALUES (:o, :r, :v)"),
                {"o": organization_id, "r": resource_ref, "v": revision_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, ordinal, content, projection_kind
                    ) VALUES (
                        :o, :r, :v, 'fragment:tombstone', 0,
                        'transactional quokka', 'body'
                    )
                    """
                ),
                {"o": organization_id, "r": resource_ref, "v": revision_id},
            )
            assert tuple(
                connection.execute(text(_FTS_CANDIDATE_SQL), _parameters(candidate))
            )
            connection.execute(
                text(
                    "UPDATE context_resource SET tombstoned = true "
                    "WHERE organization_id = :o AND resource_ref = :r"
                ),
                {"o": organization_id, "r": resource_ref},
            )
            assert (
                tuple(
                    connection.execute(text(_FTS_CANDIDATE_SQL), _parameters(candidate))
                )
                == ()
            )
    finally:
        _cleanup(engine, organization_id)
        engine.dispose()
