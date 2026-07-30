"""Synthetic retained File lineage that populates the harness across tenants.

The M0 security gate must reach the same verdict on a harness volume that
already holds File lineage as on an empty one. These helpers publish a small,
entirely synthetic multi-Organization corpus — nested Markdown lineage owned by
Organizations no registered test uses — so corpus sensitivity is provable
instead of incidental. No dogfood content enters this fixture.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import text

from engine.control.contracts import FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST
from engine.persistence import DatabaseConfiguration, create_database_engine

SEED_NAMESPACE = UUID("6d0a1f2b-7c34-4c9e-9d51-0f4b8a2e6c13")
SEED_ORGANIZATION_COUNT = 2
NESTED_PATHS = ("retained/nested/alpha.md", "retained/nested/beta/gamma.md")
_SEEDED_AT = datetime(2026, 7, 30, 0, 0, tzinfo=UTC)
_CAPABILITY_MANIFEST = json.dumps(
    FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST.document(),
    sort_keys=True,
)
_IMMUTABLE_TRIGGERS = (
    ("file_acquisition", "file_acquisition_immutable"),
    ("file_source_change", "file_source_change_immutable"),
    ("file_source_change_page", "file_source_change_page_immutable"),
    ("source_version", "source_version_immutable"),
)
_DELETE_ORDER = (
    "file_acquisition",
    "file_source_delete_observation_page",
    "file_source_change",
    "file_source_change_page",
    "context_source",
    "source_version",
    "membership",
)


def _identifier(*parts: str) -> UUID:
    return uuid5(SEED_NAMESPACE, "/".join(("retained-corpus",) + parts))


def _digest(*parts: str) -> str:
    return hashlib.sha256("/".join(parts).encode("utf-8")).hexdigest()


def seeded_organization_ids() -> tuple[UUID, ...]:
    """Return the exact Organizations this synthetic corpus owns."""

    return tuple(
        _identifier("organization", str(ordinal))
        for ordinal in range(1, SEED_ORGANIZATION_COUNT + 1)
    )


def clear_retained_file_lineage(configuration: DatabaseConfiguration) -> None:
    """Remove every row this fixture published, leaving other tenants intact."""

    organization_ids = list(seeded_organization_ids())
    user_ids = [
        _identifier("user", str(ordinal))
        for ordinal in range(1, SEED_ORGANIZATION_COUNT + 1)
    ]
    engine = create_database_engine(configuration)
    try:
        with engine.begin() as connection:
            for table, trigger in _IMMUTABLE_TRIGGERS:
                connection.execute(
                    text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
                )
        try:
            with engine.begin() as connection:
                for table in _DELETE_ORDER:
                    connection.execute(
                        text(
                            f"DELETE FROM {table} "  # noqa: S608
                            "WHERE organization_id = ANY(:organization_ids)"
                        ),
                        {"organization_ids": organization_ids},
                    )
                connection.execute(
                    text(
                        "DELETE FROM organization "
                        "WHERE organization_id = ANY(:organization_ids)"
                    ),
                    {"organization_ids": organization_ids},
                )
                connection.execute(
                    text("DELETE FROM user_account WHERE user_id = ANY(:user_ids)"),
                    {"user_ids": user_ids},
                )
        finally:
            with engine.begin() as connection:
                for table, trigger in _IMMUTABLE_TRIGGERS:
                    connection.execute(
                        text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
                    )
    finally:
        engine.dispose()


def seed_retained_file_lineage(
    configuration: DatabaseConfiguration,
) -> tuple[UUID, ...]:
    """Publish nested File lineage for two Organizations and return their ids.

    The corpus satisfies, for Organizations under no test's control, exactly the
    whole-database conditions the File downgrade guards count: nested Markdown
    paths, an accepted change page, a mixed upsert/delete page, and accepted
    change acquisition lineage.
    """

    clear_retained_file_lineage(configuration)
    organization_ids = seeded_organization_ids()
    engine = create_database_engine(configuration)
    try:
        with engine.begin() as connection:
            for ordinal, organization_id in enumerate(organization_ids, start=1):
                marker = str(ordinal)
                user_id = _identifier("user", marker)
                membership_id = _identifier("membership", marker)
                source_id = _identifier("source", marker)
                version_id = _identifier("version", marker)
                scan_ref = _digest("scan", marker)
                page_ref = _digest("page", marker)
                scan_epoch = _identifier("scan-epoch", marker)
                upsert_path, delete_path = NESTED_PATHS
                upsert_digest = _digest("content", marker, upsert_path)
                delete_digest = _digest("content", marker, delete_path)
                connection.execute(
                    text(
                        "INSERT INTO organization (organization_id) "
                        "VALUES (:organization_id)"
                    ),
                    {"organization_id": organization_id},
                )
                connection.execute(
                    text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                    {"user_id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO membership (organization_id, membership_id, "
                        "user_id, status, membership_version, valid_from) VALUES "
                        "(:organization_id, :membership_id, :user_id, 'active', 1, "
                        ":seeded_at)"
                    ),
                    {
                        "organization_id": organization_id,
                        "membership_id": membership_id,
                        "user_id": user_id,
                        "seeded_at": _SEEDED_AT,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO context_source (organization_id, source_id, "
                        "display_name, source_kind, registration_operation, "
                        "idempotency_key, registration_digest, active_version_id, "
                        "created_at, lifecycle_state) VALUES (:organization_id, "
                        ":source_id, :display_name, 'file', 'register_source', "
                        ":idempotency_key, :registration_digest, :version_id, "
                        ":seeded_at, 'active')"
                    ),
                    {
                        "organization_id": organization_id,
                        "source_id": source_id,
                        "display_name": f"Retained corpus {marker}",
                        "idempotency_key": f"retained-corpus-{marker}",
                        "registration_digest": _digest("registration", marker),
                        "version_id": version_id,
                        "seeded_at": _SEEDED_AT,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO source_version (organization_id, source_id, "
                        "version_id, source_kind, root_ref, capability_manifest, "
                        "created_at) VALUES (:organization_id, :source_id, "
                        ":version_id, 'file', :root_ref, "
                        "CAST(:capability_manifest AS jsonb), :seeded_at)"
                    ),
                    {
                        "organization_id": organization_id,
                        "source_id": source_id,
                        "version_id": version_id,
                        "root_ref": f"retained-corpus-{marker}",
                        "capability_manifest": _CAPABILITY_MANIFEST,
                        "seeded_at": _SEEDED_AT,
                    },
                )
                connection.execute(
                    text(
                        "SELECT set_config('app.organization_id', "
                        "CAST(:organization_id AS text), true)"
                    ),
                    {"organization_id": organization_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO file_source_delete_observation_page ("
                        "organization_id, source_id, source_version_id, page_ref) "
                        "VALUES (:organization_id, :source_id, :version_id, "
                        ":page_ref)"
                    ),
                    {
                        "organization_id": organization_id,
                        "source_id": source_id,
                        "version_id": version_id,
                        "page_ref": page_ref,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO file_source_change_page (organization_id, "
                        "source_id, source_version_id, page_ref, scan_ref, "
                        "scan_epoch, page_limit, page_ordinal, change_count, "
                        "complete, accepted_at) VALUES (:organization_id, "
                        ":source_id, :version_id, :page_ref, :scan_ref, "
                        ":scan_epoch, 100, 1, 2, true, :seeded_at)"
                    ),
                    {
                        "organization_id": organization_id,
                        "source_id": source_id,
                        "version_id": version_id,
                        "page_ref": page_ref,
                        "scan_ref": scan_ref,
                        "scan_epoch": scan_epoch,
                        "seeded_at": _SEEDED_AT,
                    },
                )
                for change_ordinal, change_kind, path, digest in (
                    (1, "upsert", upsert_path, upsert_digest),
                    (2, "delete", delete_path, delete_digest),
                ):
                    connection.execute(
                        text(
                            "INSERT INTO file_source_change (organization_id, "
                            "source_id, source_version_id, scan_ref, page_ref, "
                            "change_ordinal, change_kind, relative_path, "
                            "content_sha256, content_length) VALUES "
                            "(:organization_id, :source_id, :version_id, "
                            ":scan_ref, :page_ref, :change_ordinal, :change_kind, "
                            ":relative_path, :content_sha256, 16)"
                        ),
                        {
                            "organization_id": organization_id,
                            "source_id": source_id,
                            "version_id": version_id,
                            "scan_ref": scan_ref,
                            "page_ref": page_ref,
                            "change_ordinal": change_ordinal,
                            "change_kind": change_kind,
                            "relative_path": path,
                            "content_sha256": digest,
                        },
                    )
                connection.execute(
                    text(
                        "INSERT INTO file_acquisition (organization_id, "
                        "acquisition_id, source_id, source_version_id, "
                        "relative_path, audience_principal_ref, "
                        "audience_membership_id, audience_membership_version, "
                        "idempotency_key, request_digest, created_at, "
                        "change_page_ref, change_ordinal, expected_content_sha256, "
                        "expected_content_length) VALUES (:organization_id, "
                        ":acquisition_id, :source_id, :version_id, :relative_path, "
                        ":principal_ref, :membership_id, 1, :idempotency_key, "
                        ":request_digest, :seeded_at, :page_ref, 1, "
                        ":content_sha256, 16)"
                    ),
                    {
                        "organization_id": organization_id,
                        "acquisition_id": _identifier("acquisition", marker),
                        "source_id": source_id,
                        "version_id": version_id,
                        "relative_path": upsert_path,
                        "principal_ref": f"principal:retained-corpus-{marker}",
                        "membership_id": membership_id,
                        "idempotency_key": f"retained-corpus-{marker}",
                        "request_digest": _digest("request", marker),
                        "seeded_at": _SEEDED_AT,
                        "page_ref": page_ref,
                        "content_sha256": upsert_digest,
                    },
                )
    finally:
        engine.dispose()
    return organization_ids


@contextmanager
def retained_file_lineage(
    configuration: DatabaseConfiguration,
) -> Iterator[tuple[UUID, ...]]:
    """Publish the synthetic corpus for one test and remove it afterwards."""

    try:
        yield seed_retained_file_lineage(configuration)
    finally:
        clear_retained_file_lineage(configuration)
