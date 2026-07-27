"""Explicit local identity and optional File receiver dogfood seeding."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from applications.operator_authentication import (
    RELEASE_OPERATOR_SECRET_ENV,
    LocalOperatorConfiguration,
    LocalReleaseOperatorAuthenticator,
)
from engine.persistence import (
    DatabasePurpose,
    create_database_engine,
    load_database_configuration,
)
from engine.persistence.role_guard import assert_migrator_role


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a canonical UUID") from error


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Seed one local Organization/User/current Membership and an "
            "optional File-import receiver"
        )
    )
    parser.add_argument("--organization-id", required=True, type=_uuid)
    parser.add_argument("--user-id", required=True, type=_uuid)
    parser.add_argument("--membership-id", required=True, type=_uuid)
    parser.add_argument("--membership-version", default=1, type=int)
    parser.add_argument("--file-import-service-principal-id", type=_uuid)
    parser.add_argument(
        "--provision-release-operator-grant",
        action="store_true",
        help="provision the current bounded local release identity grant",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.membership_version < (1 << 63):
        parser.error("--membership-version must be a positive signed bigint")

    seeded_at = datetime.now(UTC).replace(microsecond=0)
    try:
        release_identity = None
        if args.provision_release_operator_grant:
            configuration = LocalOperatorConfiguration.load(os.environ)
            if (
                configuration is None
                or configuration.organization_id != args.organization_id
            ):
                raise RuntimeError
            release_identity = LocalReleaseOperatorAuthenticator(
                configuration,
                clock=lambda: seeded_at,
            ).authenticate(os.environ[RELEASE_OPERATOR_SECRET_ENV])
    except Exception:
        parser.exit(1, "context-engine-dogfood-seed: operation refused\n")
    engine = create_database_engine(
        load_database_configuration(DatabasePurpose.MIGRATION)
    )
    try:
        with engine.begin() as connection:
            assert_migrator_role(connection)
            connection.execute(
                text(
                    """
                    INSERT INTO organization (organization_id)
                    VALUES (:organization_id)
                    ON CONFLICT (organization_id) DO NOTHING
                    """
                ),
                {"organization_id": args.organization_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO user_account (user_id)
                    VALUES (:user_id)
                    ON CONFLICT (user_id) DO NOTHING
                    """
                ),
                {"user_id": args.user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from, valid_until
                    ) VALUES (
                        :organization_id, :membership_id, :user_id, 'active',
                        :membership_version, :valid_from, NULL
                    )
                    ON CONFLICT (organization_id, membership_id) DO NOTHING
                    """
                ),
                {
                    "organization_id": args.organization_id,
                    "user_id": args.user_id,
                    "membership_id": args.membership_id,
                    "membership_version": args.membership_version,
                    "valid_from": seeded_at,
                },
            )
            if args.file_import_service_principal_id is not None:
                connection.execute(
                    text(
                        """
                        INSERT INTO service_principal (
                            organization_id, service_principal_id, workload,
                            worker_audience, operation, enabled
                        ) VALUES (
                            :organization_id, :service_principal_id,
                            'supply.file-import', 'context-engine-worker',
                            'file.import', true
                        )
                        ON CONFLICT (
                            organization_id, service_principal_id
                        ) DO NOTHING
                        """
                    ),
                    {
                        "organization_id": args.organization_id,
                        "service_principal_id": (args.file_import_service_principal_id),
                    },
                )
            exact = connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM membership
                        WHERE organization_id = :organization_id
                          AND membership_id = :membership_id
                          AND user_id = :user_id
                          AND status = 'active'
                          AND membership_version = :membership_version
                          AND valid_from <= statement_timestamp()
                          AND (
                            valid_until IS NULL
                            OR statement_timestamp() < valid_until
                          )
                    )
                    """
                ),
                {
                    "organization_id": args.organization_id,
                    "user_id": args.user_id,
                    "membership_id": args.membership_id,
                    "membership_version": args.membership_version,
                },
            ).scalar_one()
            if exact is not True:
                raise RuntimeError("dogfood identity conflicts with durable ownership")
            if args.file_import_service_principal_id is not None:
                exact_receiver = connection.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM service_principal
                            WHERE organization_id = :organization_id
                              AND service_principal_id = :service_principal_id
                              AND workload = 'supply.file-import'
                              AND worker_audience = 'context-engine-worker'
                              AND operation = 'file.import'
                              AND enabled IS TRUE
                        )
                        """
                    ),
                    {
                        "organization_id": args.organization_id,
                        "service_principal_id": (args.file_import_service_principal_id),
                    },
                ).scalar_one()
                if exact_receiver is not True:
                    raise RuntimeError(
                        "dogfood receiver conflicts with durable ownership"
                    )
            if release_identity is not None:
                connection.execute(
                    text(
                        """
                        INSERT INTO release_operator_grant (
                            organization_id, authority_ref, authority_digest,
                            operator_ref, authentication_binding_ref,
                            valid_from, expires_at, revoked_at
                        ) VALUES (
                            :organization_id, :authority_ref, :authority_digest,
                            :operator_ref, :authentication_binding_ref,
                            :valid_from, :expires_at, NULL
                        )
                        ON CONFLICT (organization_id, authority_ref) DO UPDATE
                        SET authority_digest = EXCLUDED.authority_digest,
                            operator_ref = EXCLUDED.operator_ref,
                            authentication_binding_ref =
                                EXCLUDED.authentication_binding_ref,
                            valid_from = EXCLUDED.valid_from,
                            expires_at = EXCLUDED.expires_at,
                            revoked_at = NULL
                        """
                    ),
                    {
                        "organization_id": release_identity.organization_id,
                        "authority_ref": release_identity.authority_ref,
                        "authority_digest": release_identity.authority_digest,
                        "operator_ref": release_identity.operator_ref,
                        "authentication_binding_ref": (
                            release_identity.authentication_binding_ref
                        ),
                        "valid_from": release_identity.valid_from,
                        "expires_at": release_identity.expires_at,
                    },
                )
    finally:
        engine.dispose()
    print(
        "dogfood identity ready: "
        f"organization={args.organization_id} "
        f"user={args.user_id} membership={args.membership_id} "
        f"version={args.membership_version}"
        + (
            ""
            if args.file_import_service_principal_id is None
            else (
                " file_import_service_principal="
                f"{args.file_import_service_principal_id}"
            )
        )
        + ("" if release_identity is None else " release_operator_grant=ready"),
        flush=True,
    )


if __name__ == "__main__":
    main()
