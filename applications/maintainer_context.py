"""Read-only maintainer CLI for fresh resolve and Package inspection."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, cast
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from adapters.http.contracts import (
    AcquireWire,
    CitationNotAvailableWire,
    ContextPackageWire,
    RequestNotAvailableWire,
    ResolutionOutcomeWire,
    ResolvedWire,
)
from adapters.http.dogfood_client import (
    DOGFOOD_SECRET_ENV,
    DogfoodEvaluationUnavailable,
    DogfoodHttpConfiguration,
    DogfoodResolveClient,
    DogfoodSecretExclusionUnavailable,
    reject_secret_material,
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
REDACTED_EGRESS_GRANT: Final = "REDACTED-EGRESS-GRANT"

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
        document = _redacted_outcome(outcome)
    except (ValidationError, ValueError):
        print("context-engine-context: malformed_package", file=sys.stderr)
        return EXIT_MALFORMED_PACKAGE
    except DogfoodSecretExclusionUnavailable:
        print("context-engine-context: invalid_configuration", file=sys.stderr)
        return EXIT_INVALID_CONFIGURATION
    except DogfoodEvaluationUnavailable:
        print("context-engine-context: service_unavailable", file=sys.stderr)
        return EXIT_SERVICE_UNAVAILABLE
    if type(validated) is not ResolvedWire:
        _render_refusal(
            format_name=arguments.format,
            raw=document,
            category=_refusal_category(validated),
        )
        return EXIT_EXPLICIT_REFUSAL
    package = validated.package
    try:
        expires_at = _validated_expiry(package)
    except ValueError:
        print("context-engine-context: malformed_package", file=sys.stderr)
        return EXIT_MALFORMED_PACKAGE
    if datetime.now(tz=UTC) >= expires_at:
        print("context-engine-context: expired_package", file=sys.stderr)
        return EXIT_EXPIRED_PACKAGE
    refusal = _coverage_refusal(package)
    if refusal is not None:
        _render_refusal(
            format_name=arguments.format,
            raw=document,
            category=refusal,
        )
        return EXIT_EXPLICIT_REFUSAL
    if arguments.format == "json":
        print(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
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
        capture = _validated_capture(raw)
    except (
        OSError,
        RecursionError,
        UnicodeDecodeError,
        ValueError,
        ValidationError,
    ):
        print("context-engine-context: malformed_package", file=sys.stderr)
        return EXIT_MALFORMED_PACKAGE
    try:
        _reject_configured_secret(raw)
    except (DogfoodEvaluationUnavailable, DogfoodSecretExclusionUnavailable):
        print("context-engine-context: invalid_configuration", file=sys.stderr)
        return EXIT_INVALID_CONFIGURATION
    if isinstance(capture, _RefusedCapture):
        _render_refusal(
            format_name=arguments.format,
            raw=capture.document,
            category=capture.category,
        )
        return EXIT_EXPLICIT_REFUSAL
    if datetime.now(tz=UTC) >= capture.expires_at:
        print("context-engine-context: expired_package", file=sys.stderr)
        return EXIT_EXPIRED_PACKAGE
    refusal = _coverage_refusal(capture.package)
    if refusal is not None:
        _render_refusal(
            format_name=arguments.format,
            raw=capture.document,
            category=refusal,
        )
        return EXIT_EXPLICIT_REFUSAL
    if arguments.format == "json":
        print(
            json.dumps(
                capture.document,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    else:
        print(_render_human(capture.package))
    return EXIT_SUCCESS


@dataclass(frozen=True, slots=True)
class _PackageCapture:
    """One validated capture Package with its exact rendered document."""

    package: ContextPackageWire
    document: object
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _RefusedCapture:
    """One schema-valid closed public refusal captured without a Package."""

    category: str
    document: object


def _validated_capture(raw: object) -> _PackageCapture | _RefusedCapture:
    if type(raw) is dict and "kind" in raw:
        outcome = _OUTCOME_ADAPTER.validate_python(_capture_envelope(raw))
        if type(outcome) is not ResolvedWire:
            return _RefusedCapture(
                category=_refusal_category(outcome),
                document=raw,
            )
        package_document = raw.get("package")
        if type(package_document) is not dict:
            raise ValueError("capture Package is unavailable")
        if not verify_context_package_public_document(package_document):
            raise ValueError("capture Package digest is unavailable")
        return _PackageCapture(
            package=outcome.package,
            document=package_document,
            expires_at=_validated_expiry(outcome.package),
        )
    package = _PACKAGE_ADAPTER.validate_python(raw)
    if not verify_context_package_public_document(raw):
        raise ValueError("capture Package digest is unavailable")
    return _PackageCapture(
        package=package,
        document=raw,
        expires_at=_validated_expiry(package),
    )


def _capture_envelope(raw: dict[str, object]) -> dict[str, object]:
    """Validate this caller's own redacted capture without a live grant."""

    grant = raw.get("egressGrant")
    if type(grant) is not dict:
        return raw
    if cast(dict[str, object], grant).get("value") != REDACTED_EGRESS_GRANT:
        return raw
    envelope = dict(raw)
    envelope["egressGrant"] = None
    return envelope


def _redacted_outcome(outcome: dict[str, object]) -> dict[str, object]:
    """Replace only the redeemable grant value with the fixed sentinel.

    This read caller never performs egress, so the one-hop capability is the
    single exact-wire value it refuses to emit or persist.  Every other field
    and the grant's own structure stay exactly as the server sent them.
    """

    grant = outcome.get("egressGrant")
    if type(grant) is not dict:
        return outcome
    redacted = dict(cast(dict[str, object], grant))
    live_value = redacted.get("value")
    if type(live_value) is not str:
        return outcome
    redacted["value"] = REDACTED_EGRESS_GRANT
    document = dict(outcome)
    document["egressGrant"] = redacted
    reject_secret_material(live_value, document)
    return document


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


def _refusal_category(outcome: ResolutionOutcomeWire) -> str:
    if type(outcome) is RequestNotAvailableWire:
        return "request_not_available"
    if type(outcome) is CitationNotAvailableWire:
        return "citation_not_available"
    return "explicit_refusal"


def _utc_instant(value: datetime) -> datetime:
    """Require one timezone-aware public instant normalizable to UTC.

    A captured naive instant would otherwise be reinterpreted in the local
    zone, which can report an already-expired Package as current.
    """

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("public instant must be timezone-aware")
    try:
        return value.astimezone(UTC)
    except (OverflowError, OSError, ValueError):
        raise ValueError("public instant is not UTC-normalizable") from None


def _validated_expiry(package: ContextPackageWire) -> datetime:
    as_of = _utc_instant(package.asOf)
    expires_at = _utc_instant(package.expiresAt)
    try:
        lifetime = timedelta(seconds=package.ttlSeconds)
    except (OverflowError, ValueError):
        raise ValueError("Package lifetime is unavailable") from None
    if expires_at - as_of != lifetime:
        raise ValueError("Package lifetime is inconsistent")
    return expires_at


def _reject_configured_secret(value: object) -> None:
    secret = os.environ.get(DOGFOOD_SECRET_ENV)
    if secret is None:
        return
    reject_secret_material(secret, value)


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
    return _utc_instant(value).isoformat().replace("+00:00", "Z")


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
