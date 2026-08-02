"""Read-only maintainer CLI for fresh resolve and Package inspection."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from adapters.http.contracts import (
    AcquireWire,
    ContextPackageWire,
    ResolutionOutcomeWire,
    ResolvedWire,
)
from adapters.http.dogfood_client import (
    DOGFOOD_SECRET_ENV,
    DogfoodEvaluationUnavailable,
    DogfoodHttpConfiguration,
    DogfoodResolveClient,
    DogfoodSecretExclusionUnavailable,
    validate_dogfood_query,
    validate_dogfood_request_id,
)
from context_engine_contracts import verify_context_package_public_document

EXIT_SUCCESS: Final = 0
EXIT_EXPLICIT_REFUSAL: Final = 10
EXIT_SERVICE_UNAVAILABLE: Final = 11
EXIT_MALFORMED_PACKAGE: Final = 12
EXIT_EXPIRED_PACKAGE: Final = 13
EXIT_INVALID_CONFIGURATION: Final = 14

_OUTCOME_ADAPTER: Final[TypeAdapter[ResolutionOutcomeWire]] = TypeAdapter(
    ResolutionOutcomeWire
)
_PACKAGE_ADAPTER: Final[TypeAdapter[ContextPackageWire]] = TypeAdapter(
    ContextPackageWire
)
MAX_CAPTURE_BYTES: Final = 16 * 1024 * 1024


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="context-engine-context",
        description="query and inspect read-only ContextPackages",
    )
    subcommands = parser.add_subparsers(dest="subcommand", required=True)
    query = subcommands.add_parser(
        "query",
        help="send one fresh loopback Acquire",
    )
    query.add_argument("query")
    query.add_argument("--format", choices=("human", "json"), default="human")
    query.add_argument("--max-tokens", type=int)
    query.add_argument("--max-provider-calls", type=int)
    query.add_argument("--max-cost-microunits", type=int)
    query.add_argument("--max-elapsed-ms", type=int)
    query.add_argument("--source-ref", action="append")
    query.add_argument("--resource-ref", action="append")
    inspect = subcommands.add_parser(
        "inspect",
        help="validate one untrusted local Package capture",
    )
    inspect.add_argument("capture", type=Path)
    inspect.add_argument("--format", choices=("human", "json"), default="human")
    return parser


def _budget(arguments: argparse.Namespace) -> dict[str, int] | None:
    values = {
        "maxTokens": arguments.max_tokens,
        "maxProviderCalls": arguments.max_provider_calls,
        "maxCostMicrounits": arguments.max_cost_microunits,
        "maxElapsedMs": arguments.max_elapsed_ms,
    }
    present = {key: value for key, value in values.items() if value is not None}
    return present or None


def _narrowing(arguments: argparse.Namespace) -> dict[str, Sequence[str]] | None:
    values = {
        "sourceRefs": arguments.source_ref,
        "resourceRefs": arguments.resource_ref,
    }
    present = {key: value for key, value in values.items() if value is not None}
    return present or None


def _query(arguments: argparse.Namespace) -> int:
    request_id = f"maintainer-context-{uuid4().hex}"
    budget = _budget(arguments)
    narrowing = _narrowing(arguments)
    try:
        request = AcquireWire.model_validate(
            {
                "kind": "acquire",
                "need": {"query": arguments.query},
                **({"packageBudget": budget} if budget is not None else {}),
                **(
                    {"requestNarrowing": narrowing}
                    if narrowing is not None
                    else {}
                ),
            }
        )
        configuration = DogfoodHttpConfiguration.load()
        validate_dogfood_query(request.need.query)
        validate_dogfood_request_id(request_id)
        configuration.reject_secret_material(request_id)
    except (ValidationError, DogfoodEvaluationUnavailable):
        print("context-engine-context: invalid_configuration", file=sys.stderr)
        return EXIT_INVALID_CONFIGURATION
    try:
        outcome = DogfoodResolveClient(configuration).resolve_acquire_document(
            acquire=request.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            ),
            request_id=request_id,
        )
        validated = _OUTCOME_ADAPTER.validate_python(outcome)
        _require_exact_outcome_package_digest(outcome, validated)
    except (ValidationError, ValueError):
        print("context-engine-context: malformed_package", file=sys.stderr)
        return EXIT_MALFORMED_PACKAGE
    except DogfoodSecretExclusionUnavailable:
        print("context-engine-context: invalid_configuration", file=sys.stderr)
        return EXIT_INVALID_CONFIGURATION
    except DogfoodEvaluationUnavailable:
        print("context-engine-context: service_unavailable", file=sys.stderr)
        return EXIT_SERVICE_UNAVAILABLE
    if outcome.get("kind") == "request_not_available":
        _render_refusal(
            format_name=arguments.format,
            raw=outcome,
            category="request_not_available",
        )
        return EXIT_EXPLICIT_REFUSAL
    if type(validated) is not ResolvedWire:
        print("context-engine-context: explicit_refusal", file=sys.stderr)
        return EXIT_EXPLICIT_REFUSAL
    package = validated.package
    try:
        _validate_lifetime(package)
    except ValueError:
        print("context-engine-context: malformed_package", file=sys.stderr)
        return EXIT_MALFORMED_PACKAGE
    if datetime.now(tz=UTC) >= package.expiresAt.astimezone(UTC):
        print("context-engine-context: expired_package", file=sys.stderr)
        return EXIT_EXPIRED_PACKAGE
    refusal = _coverage_refusal(package)
    if refusal is not None:
        _render_refusal(
            format_name=arguments.format,
            raw=outcome,
            category=refusal,
        )
        return EXIT_EXPLICIT_REFUSAL
    if arguments.format == "json":
        print(json.dumps(outcome, ensure_ascii=False, separators=(",", ":")))
    else:
        print(_render_human(package))
    return EXIT_SUCCESS


def _read_capture(path: Path) -> object:
    if path == Path("-"):
        raw = sys.stdin.buffer.read(MAX_CAPTURE_BYTES + 1)
    else:
        with path.open("rb") as capture:
            raw = capture.read(MAX_CAPTURE_BYTES + 1)
    if len(raw) > MAX_CAPTURE_BYTES:
        raise ValueError("capture is too large")
    return json.loads(raw)


def _inspect(arguments: argparse.Namespace) -> int:
    try:
        raw = _read_capture(arguments.capture)
        package, package_document = _validated_capture(raw)
        _validate_lifetime(package)
    except (OSError, UnicodeDecodeError, ValueError, ValidationError):
        print("context-engine-context: malformed_package", file=sys.stderr)
        return EXIT_MALFORMED_PACKAGE
    try:
        _reject_configured_secret(raw)
    except (DogfoodEvaluationUnavailable, DogfoodSecretExclusionUnavailable):
        print("context-engine-context: invalid_configuration", file=sys.stderr)
        return EXIT_INVALID_CONFIGURATION
    if datetime.now(tz=UTC) >= package.expiresAt.astimezone(UTC):
        print("context-engine-context: expired_package", file=sys.stderr)
        return EXIT_EXPIRED_PACKAGE
    refusal = _coverage_refusal(package)
    if refusal is not None:
        _render_refusal(
            format_name=arguments.format,
            raw=package_document,
            category=refusal,
        )
        return EXIT_EXPLICIT_REFUSAL
    if arguments.format == "json":
        print(
            json.dumps(
                package_document,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    else:
        print(_render_human(package))
    return EXIT_SUCCESS


def _validated_capture(raw: object) -> tuple[ContextPackageWire, object]:
    if type(raw) is dict and "kind" in raw:
        outcome = _OUTCOME_ADAPTER.validate_python(raw)
        if type(outcome) is not ResolvedWire:
            raise ValueError("capture did not contain a ContextPackage")
        package_document = raw.get("package")
        if type(package_document) is not dict:
            raise ValueError("capture Package is unavailable")
        if not verify_context_package_public_document(package_document):
            raise ValueError("capture Package digest is unavailable")
        return outcome.package, package_document
    package = _PACKAGE_ADAPTER.validate_python(raw)
    if not verify_context_package_public_document(raw):
        raise ValueError("capture Package digest is unavailable")
    return package, raw


def _require_exact_outcome_package_digest(
    raw: object,
    outcome: ResolutionOutcomeWire,
) -> None:
    if type(outcome) is not ResolvedWire:
        return
    if type(raw) is not dict:
        raise ValueError("resolve outcome is unavailable")
    package_document = raw.get("package")
    if not verify_context_package_public_document(package_document):
        raise ValueError("resolve Package digest is unavailable")


def _render_human(package: ContextPackageWire) -> str:
    evidence_by_ref = {item.evidenceRef: item for item in package.evidence}
    usage = package.budgetUsage
    coverage = package.coverage
    coverage_value: str = coverage.status
    if coverage.reason is not None:
        coverage_value = f"{coverage_value} ({coverage.reason})"
    lines = [
        f"packageId: {package.packageId}",
        f"packageDigest: {package.packageDigest}",
        f"purpose: {package.purpose}",
        f"asOf: {_instant(package.asOf)}",
        f"expiresAt: {_instant(package.expiresAt)} (current)",
        f"coverage: {coverage_value}",
        "budgetUsage:",
        f"  tokens: {usage.tokens}",
        f"  providerCalls: {usage.providerCalls}",
        f"  costMicrounits: {usage.costMicrounits}",
        f"  elapsedMs: {usage.elapsedMs}",
        f"blocks: {len(package.blocks)}",
    ]
    for ordinal, block in enumerate(package.blocks, start=1):
        evidence_ref = block.evidenceRefs[0]
        evidence = evidence_by_ref[evidence_ref]
        lines.extend(
            (
                "",
                f"block {ordinal}:",
                f"  blockId: {block.blockId}",
                f"  text: {block.text}",
                f"  evidenceRef: {evidence_ref}",
                "  citationLineage:",
                f"    sourceRef: {evidence.sourceRef}",
                f"    resourceRef: {evidence.resourceRef}",
                f"    revisionRef: {evidence.revisionRef}",
                f"    fragmentRef: {evidence.fragmentRef}",
                "    projectedFields: "
                + json.dumps(list(evidence.projectedFields), separators=(",", ":")),
                f"    runRef: {evidence.runRef}",
                f"    purpose: {evidence.purpose}",
                f"    authorizationAsOf: {_instant(evidence.authorizationAsOf)}",
                f"    decisionRef: {evidence.decisionRef}",
                f"    policySnapshotRef: {evidence.policySnapshotRef}",
                f"    policyEpoch: {evidence.policyEpoch}",
                "    sourceAclEvidence: "
                + json.dumps(
                    evidence.sourceAclEvidence.model_dump(
                        mode="json",
                        exclude_none=True,
                    ),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                f"    citationOpenRef: {evidence.citationOpenRef or 'unavailable'}",
                "    citationOpen: NOT_ACTIVE",
            )
        )
    return "\n".join(lines)


def _coverage_refusal(package: ContextPackageWire) -> str | None:
    if package.coverage.status == "sufficient":
        return None
    reason = package.coverage.reason
    if reason == "no_authorized_evidence":
        return "empty_authorized_set"
    return reason or "explicit_refusal"


def _validate_lifetime(package: ContextPackageWire) -> None:
    as_of = package.asOf.astimezone(UTC)
    expires_at = package.expiresAt.astimezone(UTC)
    if expires_at - as_of != timedelta(seconds=package.ttlSeconds):
        raise ValueError("Package lifetime is inconsistent")


def _reject_configured_secret(value: object) -> None:
    secret = os.environ.get(DOGFOOD_SECRET_ENV)
    if secret is None:
        return
    configuration = DogfoodHttpConfiguration(
        base_url="http://127.0.0.1:1",
        secret=secret,
    )
    configuration.reject_secret_material(value)


def _render_refusal(
    *,
    format_name: str,
    raw: object,
    category: str,
) -> None:
    if format_name == "json":
        print(json.dumps(raw, ensure_ascii=False, separators=(",", ":")))
    else:
        print(f"context-engine-context: {category}", file=sys.stderr)


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None) -> None:
    arguments = _parser().parse_args(argv)
    if arguments.subcommand == "query":
        code = _query(arguments)
    elif arguments.subcommand == "inspect":
        code = _inspect(arguments)
    else:  # pragma: no cover - argparse closes the subcommand set
        raise AssertionError("closed maintainer subcommand")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
