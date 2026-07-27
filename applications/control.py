"""Short-lived local operator process for ContextEngine control operations."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from applications.operator_authentication import (
    CONTROL_OPERATOR_SECRET_ENV,
    LocalOperatorAuthorities,
    LocalOperatorConfiguration,
)
from engine.control import (
    ActivateFileChangeFeed,
    ActivateFileDeleteObservations,
    ContextControl,
    ControlOperation,
    FileRootRef,
    RegisterFileSource,
    SourceManifest,
    SourceNotAvailable,
    SourceRef,
)
from engine.persistence import (
    DatabasePurpose,
    PostgreSQLControlStore,
    create_database_engine,
    load_database_configuration,
)
from engine.persistence.migrations import migrate_to_head

_OPERATOR_SUBCOMMANDS = frozenset(
    {
        "register-file-source",
        "read-source",
        "activate-change-feed",
        "activate-delete-observations",
    }
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="context-engine-control")
    subcommands = parser.add_subparsers(dest="subcommand", required=True)
    subcommands.add_parser(
        "migrate",
        help="upgrade the configured database to the current schema head",
    )
    register = subcommands.add_parser(
        "register-file-source",
        help="register one logical File root",
    )
    _organization_argument(register)
    register.add_argument("--display-name", required=True)
    register.add_argument("--root-ref", required=True)
    register.add_argument("--idempotency-key", required=True)
    for name, help_text in (
        ("read-source", "read one registered File source"),
        ("activate-change-feed", "activate one File source change feed"),
        (
            "activate-delete-observations",
            "activate one File source delete-observation capability",
        ),
    ):
        source_command = subcommands.add_parser(name, help=help_text)
        _organization_argument(source_command)
        source_command.add_argument("--source-ref", required=True)
    return parser


def _organization_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--organization-id", required=True)


def main(argv: Sequence[str] | None = None) -> None:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.subcommand == "migrate":
        try:
            revision = migrate_to_head()
        except Exception:  # The local process must never render connection details.
            parser.exit(1, "context-engine-control: migration refused\n")
        print(revision, flush=True)
        return
    if arguments.subcommand not in _OPERATOR_SUBCOMMANDS:
        parser.error("unknown operation")
    try:
        manifest = _run_operator_subcommand(arguments)
        rendered = _manifest_json(manifest)
    except Exception:  # Operator refusals disclose no supplied or trusted facts.
        parser.exit(1, "context-engine-control: operation refused\n")
    print(rendered, flush=True)


def local_operator_authorities() -> LocalOperatorAuthorities | None:
    """Construct local operator authority only after complete explicit opt-in."""

    configuration = LocalOperatorConfiguration.load(os.environ)
    if configuration is None:
        return None
    return configuration.authorities()


def _run_operator_subcommand(arguments: argparse.Namespace) -> SourceManifest:
    authorities = local_operator_authorities()
    if authorities is None:
        raise SourceNotAvailable
    organization_id = UUID(arguments.organization_id)
    opaque_credential = os.environ[CONTROL_OPERATOR_SECRET_ENV]
    operation = _operation(arguments.subcommand)
    configuration = load_database_configuration(DatabasePurpose.CONTROL_PLANE)
    engine = create_database_engine(configuration)

    def clock() -> datetime:
        return datetime.now(UTC)

    try:
        control = ContextControl(
            store=PostgreSQLControlStore(engine, clock=clock),
            authority=authorities.control,
            clock=clock,
        )
        with authorities.control.authorize(
            opaque_credential=opaque_credential,
            operation=operation,
            request_id=f"local-{arguments.subcommand}-{uuid4().hex}",
        ) as call:
            if call.organization_id != organization_id:
                raise SourceNotAvailable
            if operation is ControlOperation.REGISTER_SOURCE:
                return control.register_source(
                    call,
                    RegisterFileSource(
                        display_name=arguments.display_name,
                        root_ref=FileRootRef(arguments.root_ref),
                        idempotency_key=arguments.idempotency_key,
                    ),
                )
            source_ref = SourceRef(UUID(arguments.source_ref))
            if operation is ControlOperation.READ_SOURCE:
                return control.read_source(call, source_ref)
            if operation is ControlOperation.ACTIVATE_FILE_CHANGE_FEED:
                return control.activate_file_change_feed(
                    call,
                    ActivateFileChangeFeed(source_ref),
                )
            if operation is ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS:
                return control.activate_file_delete_observations(
                    call,
                    ActivateFileDeleteObservations(source_ref),
                )
            raise SourceNotAvailable
    finally:
        engine.dispose()


def _operation(subcommand: str) -> ControlOperation:
    operations = {
        "register-file-source": ControlOperation.REGISTER_SOURCE,
        "read-source": ControlOperation.READ_SOURCE,
        "activate-change-feed": ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
        "activate-delete-observations": (
            ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS
        ),
    }
    try:
        return operations[subcommand]
    except KeyError:
        raise SourceNotAvailable from None


def _manifest_json(manifest: SourceManifest) -> str:
    if type(manifest) is not SourceManifest:
        raise SourceNotAvailable
    document = {
        "activeVersion": {
            "capabilities": manifest.active_version.capabilities.document(),
            "createdAt": _timestamp(manifest.active_version.created_at),
            "kind": manifest.active_version.kind.value,
            "rootRef": manifest.active_version.root_ref.value,
            "versionRef": str(manifest.active_version.version_ref),
        },
        "createdAt": _timestamp(manifest.created_at),
        "displayName": manifest.display_name,
        "kind": manifest.kind.value,
        "sourceRef": str(manifest.source_ref.value),
    }
    return json.dumps(document, separators=(",", ":"), sort_keys=True)


def _timestamp(value: datetime) -> str:
    if type(value) is not datetime or value.utcoffset() != timedelta(0):
        raise SourceNotAvailable
    return value.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main()
