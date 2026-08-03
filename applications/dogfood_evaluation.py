"""Real loopback caller and deterministic golden-set evaluator for dogfood."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Final, Protocol

from adapters.http.dogfood_client import (
    DOGFOOD_BASE_URL_ENV as DOGFOOD_BASE_URL_ENV,
)
from adapters.http.dogfood_client import (
    DOGFOOD_SECRET_ENV as DOGFOOD_SECRET_ENV,
)
from adapters.http.dogfood_client import (
    MAX_QUERY_CHARACTERS,
    MAX_RESPONSE_BYTES,
    _as_object,
    _package_from_outcome,
    _require_exact_text,
    _require_opaque_ref,
)
from adapters.http.dogfood_client import (
    DogfoodEvaluationUnavailable as DogfoodEvaluationUnavailable,
)
from adapters.http.dogfood_client import (
    DogfoodHttpConfiguration as DogfoodHttpConfiguration,
)
from adapters.http.dogfood_client import (
    DogfoodResolveClient as DogfoodResolveClient,
)
from adapters.http.dogfood_client import (
    DogfoodSecretExclusionUnavailable as DogfoodSecretExclusionUnavailable,
)
from engine.learning.golden_storage import (
    durable_golden_root,
    require_durable_golden_path,
)

GOLDEN_SET_SCHEMA_VERSION: Final = "context-engine-golden-set-v0"
EVAL_REPORT_VERSION: Final = "context-engine-dogfood-eval-v0"
DEFAULT_GOLDEN_SET_FILENAME: Final = "golden-set-v0.lineage-eligible.json"
MIN_GOLDEN_CASES: Final = 20
MAX_GOLDEN_CASES: Final = 50


def _require_relative_path(value: object) -> str:
    path = _require_exact_text("golden expected path", value, maximum=1_024)
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or str(parsed) != path
        or path.startswith("./")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise DogfoodEvaluationUnavailable("golden expected path is unavailable")
    return path


@dataclass(frozen=True, slots=True, order=True)
class EvidenceIdentity:
    """Content-free public lineage used by one quality expectation."""

    source_ref: str = field(repr=False)
    resource_ref: str = field(repr=False)
    revision_ref: str = field(repr=False)
    fragment_ref: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_opaque_ref("Evidence source_ref", self.source_ref)
        _require_opaque_ref("Evidence resource_ref", self.resource_ref)
        _require_opaque_ref("Evidence revision_ref", self.revision_ref)
        _require_opaque_ref("Evidence fragment_ref", self.fragment_ref)

    def public_document(self) -> dict[str, str]:
        return {
            "fragmentRef": self.fragment_ref,
            "resourceRef": self.resource_ref,
            "revisionRef": self.revision_ref,
            "sourceRef": self.source_ref,
        }


@dataclass(frozen=True, slots=True)
class GoldenExpectation:
    """Maintainer-provided path annotation plus matchable public lineage."""

    path: str
    identity: EvidenceIdentity

    def __post_init__(self) -> None:
        _require_relative_path(self.path)
        if type(self.identity) is not EvidenceIdentity:
            raise TypeError("golden expectation requires EvidenceIdentity")


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """One real maintainer query and its expected Evidence lineage."""

    case_ref: str
    query: str = field(repr=False)
    expected_evidence: tuple[GoldenExpectation, ...] = field(repr=False)

    def __post_init__(self) -> None:
        case_ref = _require_exact_text("golden case_ref", self.case_ref, maximum=128)
        if (
            case_ref[0] not in "abcdefghijklmnopqrstuvwxyz"
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789_-"
                for character in case_ref
            )
        ):
            raise DogfoodEvaluationUnavailable("golden case_ref is unavailable")
        _require_exact_text(
            "golden query",
            self.query,
            maximum=MAX_QUERY_CHARACTERS,
        )
        if (
            type(self.expected_evidence) is not tuple
            or not self.expected_evidence
            or any(
                type(expectation) is not GoldenExpectation
                for expectation in self.expected_evidence
            )
        ):
            raise DogfoodEvaluationUnavailable(
                "golden expected Evidence is unavailable"
            )
        identities = tuple(value.identity for value in self.expected_evidence)
        if len(identities) != len(set(identities)):
            raise DogfoodEvaluationUnavailable(
                "golden expected Evidence must be unique"
            )


@dataclass(frozen=True, slots=True)
class GoldenSet:
    """Frozen real-query dataset; construction rejects partial seed sets."""

    name: str
    cases: tuple[GoldenCase, ...] = field(repr=False)
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        _require_exact_text("golden set name", self.name, maximum=128)
        if (
            type(self.cases) is not tuple
            or not MIN_GOLDEN_CASES <= len(self.cases) <= MAX_GOLDEN_CASES
            or any(type(case) is not GoldenCase for case in self.cases)
        ):
            raise DogfoodEvaluationUnavailable(
                f"golden set requires {MIN_GOLDEN_CASES}-{MAX_GOLDEN_CASES} cases"
            )
        refs = tuple(case.case_ref for case in self.cases)
        if len(refs) != len(set(refs)) or refs != tuple(sorted(refs)):
            raise DogfoodEvaluationUnavailable(
                "golden cases must have unique canonical case_ref order"
            )
        canonical = _golden_set_document(self)
        object.__setattr__(
            self,
            "digest",
            sha256(
                json.dumps(
                    canonical,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
        )


def _golden_set_document(golden_set: GoldenSet) -> dict[str, object]:
    return {
        "entries": [
            {
                "caseRef": case.case_ref,
                "expectedEvidence": [
                    {
                        "fragmentRef": expectation.identity.fragment_ref,
                        "path": expectation.path,
                        "resourceRef": expectation.identity.resource_ref,
                        "revisionRef": expectation.identity.revision_ref,
                        "sourceRef": expectation.identity.source_ref,
                    }
                    for expectation in case.expected_evidence
                ],
                "query": case.query,
            }
            for case in golden_set.cases
        ],
        "name": golden_set.name,
        "schemaVersion": GOLDEN_SET_SCHEMA_VERSION,
    }


def _closed_object(
    value: object,
    name: str,
    fields: frozenset[str],
) -> dict[str, object]:
    document = _as_object(value, name)
    if frozenset(document) != fields:
        raise DogfoodEvaluationUnavailable(f"{name} is unavailable")
    return document


def load_golden_set(path: Path) -> GoldenSet:
    """Load the exact v0 schema without adding a runtime schema dependency."""

    if not isinstance(path, Path):
        raise TypeError("golden set path must be Path")
    try:
        raw = path.read_bytes()
        document = _closed_object(
            json.loads(raw),
            "golden set",
            frozenset({"schemaVersion", "name", "entries"}),
        )
        if document["schemaVersion"] != GOLDEN_SET_SCHEMA_VERSION:
            raise DogfoodEvaluationUnavailable("golden set version is unavailable")
        entries = document["entries"]
        if type(entries) is not list:
            raise DogfoodEvaluationUnavailable("golden entries are unavailable")
        cases: list[GoldenCase] = []
        for raw_case in entries:
            case = _closed_object(
                raw_case,
                "golden case",
                frozenset({"caseRef", "query", "expectedEvidence"}),
            )
            expected = case["expectedEvidence"]
            if type(expected) is not list:
                raise DogfoodEvaluationUnavailable(
                    "golden expected Evidence is unavailable"
                )
            cases.append(
                GoldenCase(
                    case_ref=_require_exact_text("golden case_ref", case["caseRef"]),
                    query=_require_exact_text(
                        "golden query",
                        case["query"],
                        maximum=MAX_QUERY_CHARACTERS,
                    ),
                    expected_evidence=tuple(
                        _parse_expectation(value) for value in expected
                    ),
                )
            )
        return GoldenSet(
            name=_require_exact_text("golden set name", document["name"]),
            cases=tuple(cases),
        )
    except DogfoodEvaluationUnavailable:
        raise
    except (OSError, UnicodeDecodeError, ValueError, TypeError, KeyError):
        raise DogfoodEvaluationUnavailable("golden set is unavailable") from None


def _durable_golden_set_path(configured: Path | None) -> Path:
    try:
        root = durable_golden_root()
        path = root / DEFAULT_GOLDEN_SET_FILENAME if configured is None else configured
        require_durable_golden_path(path, root=root)
        return path
    except (OSError, ValueError):
        raise DogfoodEvaluationUnavailable(
            "durable golden set is unavailable"
        ) from None


def _parse_expectation(value: object) -> GoldenExpectation:
    document = _closed_object(
        value,
        "golden expectation",
        frozenset(
            {"path", "sourceRef", "resourceRef", "revisionRef", "fragmentRef"}
        ),
    )
    return GoldenExpectation(
        path=_require_relative_path(document["path"]),
        identity=EvidenceIdentity(
            source_ref=_require_opaque_ref(
                "Evidence source_ref",
                document["sourceRef"],
            ),
            resource_ref=_require_opaque_ref(
                "Evidence resource_ref",
                document["resourceRef"],
            ),
            revision_ref=_require_opaque_ref(
                "Evidence revision_ref",
                document["revisionRef"],
            ),
            fragment_ref=_require_opaque_ref(
                "Evidence fragment_ref",
                document["fragmentRef"],
            ),
        ),
    )


def reject_secret_retention(
    configuration: DogfoodHttpConfiguration,
    golden_set: GoldenSet,
) -> None:
    """Refuse the configured bearer value anywhere in tracked eval input."""

    if type(configuration) is not DogfoodHttpConfiguration:
        raise TypeError("configuration must be DogfoodHttpConfiguration")
    if type(golden_set) is not GoldenSet:
        raise TypeError("golden_set must be GoldenSet")
    for case in golden_set.cases:
        if (
            configuration.secret in case.case_ref
            or configuration.secret in case.query
            or any(
                configuration.secret in expectation.path
                or configuration.secret in expectation.identity.source_ref
                or configuration.secret in expectation.identity.resource_ref
                or configuration.secret in expectation.identity.revision_ref
                or configuration.secret in expectation.identity.fragment_ref
                for expectation in case.expected_evidence
            )
        ):
            raise DogfoodEvaluationUnavailable(
                "golden set contains configured secret material"
            )


class ResolveCaller(Protocol):
    """Evaluation consumes only the public resolve behavior."""

    def acquire(self, *, query: str, request_id: str) -> dict[str, object]: ...


def _observed_evidence(
    outcome: dict[str, object],
) -> tuple[EvidenceIdentity, ...]:
    package = _package_from_outcome(outcome)
    values = package["evidence"]
    if type(values) is not list:
        raise DogfoodEvaluationUnavailable("ContextPackage Evidence is unavailable")
    identities = tuple(
        EvidenceIdentity(
            source_ref=_require_opaque_ref(
                "Evidence source_ref",
                _as_object(value, "Evidence").get("sourceRef"),
            ),
            resource_ref=_require_opaque_ref(
                "Evidence resource_ref",
                _as_object(value, "Evidence").get("resourceRef"),
            ),
            revision_ref=_require_opaque_ref(
                "Evidence revision_ref",
                _as_object(value, "Evidence").get("revisionRef"),
            ),
            fragment_ref=_require_opaque_ref(
                "Evidence fragment_ref",
                _as_object(value, "Evidence").get("fragmentRef"),
            ),
        )
        for value in values
    )
    if len(identities) != len(set(identities)):
        raise DogfoodEvaluationUnavailable("ContextPackage Evidence is unavailable")
    return identities


def render_resolve(outcome: dict[str, object]) -> str:
    """Render authorized blocks and content-free citation lineage for a human."""

    package = _package_from_outcome(outcome)
    evidence_values = package["evidence"]
    block_values = package["blocks"]
    if type(evidence_values) is not list or type(block_values) is not list:
        raise DogfoodEvaluationUnavailable("ContextPackage is unavailable")
    by_ref: dict[str, dict[str, object]] = {}
    for value in evidence_values:
        evidence = _as_object(value, "Evidence")
        evidence_ref = _require_opaque_ref(
            "Evidence evidence_ref",
            evidence.get("evidenceRef"),
        )
        by_ref[evidence_ref] = evidence
    lines = [
        "coverage: "
        + _require_exact_text(
            "coverage status",
            _as_object(package.get("coverage"), "coverage").get("status"),
        ),
        f"evidence: {len(evidence_values)}",
    ]
    for ordinal, value in enumerate(block_values, start=1):
        block = _as_object(value, "ContextBlock")
        text = _require_exact_text(
            "ContextBlock text",
            block.get("text"),
            maximum=MAX_RESPONSE_BYTES,
        )
        refs = block.get("evidenceRefs")
        if type(refs) is not list or len(refs) != 1:
            raise DogfoodEvaluationUnavailable("ContextBlock Evidence is unavailable")
        block_evidence = by_ref.get(_require_opaque_ref("Evidence ref", refs[0]))
        if block_evidence is None:
            raise DogfoodEvaluationUnavailable("ContextBlock Evidence is unavailable")
        source_ref = _require_opaque_ref(
            "Evidence source_ref",
            block_evidence.get("sourceRef"),
        )
        resource_ref = _require_opaque_ref(
            "Evidence resource_ref",
            block_evidence.get("resourceRef"),
        )
        revision_ref = _require_opaque_ref(
            "Evidence revision_ref",
            block_evidence.get("revisionRef"),
        )
        fragment_ref = _require_opaque_ref(
            "Evidence fragment_ref",
            block_evidence.get("fragmentRef"),
        )
        lines.extend(
            (
                "",
                f"[{ordinal}] {text}",
                "    citation: "
                f"source={source_ref} "
                f"resource={resource_ref} "
                f"revision={revision_ref} "
                f"fragment={fragment_ref}",
            )
        )
    return "\n".join(lines)


def evaluate_golden_set(
    golden_set: GoldenSet,
    client: ResolveCaller,
) -> dict[str, object]:
    """Replay every query and return a canonical content-free quality report."""

    if type(golden_set) is not GoldenSet:
        raise TypeError("golden_set must be GoldenSet")
    if not callable(getattr(client, "acquire", None)):
        raise TypeError("client must provide public resolve behavior")
    reports: list[dict[str, object]] = []
    total_expected = 0
    total_hits = 0
    passed_cases = 0
    for case in golden_set.cases:
        outcome = client.acquire(
            query=case.query,
            request_id=f"dogfood-eval-{case.case_ref}",
        )
        observed = frozenset(_observed_evidence(outcome))
        expected = tuple(value.identity for value in case.expected_evidence)
        hits = tuple(sorted(value for value in expected if value in observed))
        misses = tuple(sorted(value for value in expected if value not in observed))
        total_expected += len(expected)
        total_hits += len(hits)
        if not misses:
            passed_cases += 1
        reports.append(
            {
                "caseRef": case.case_ref,
                "expectedCount": len(expected),
                "hits": [value.public_document() for value in hits],
                "misses": [value.public_document() for value in misses],
                "status": "hit" if not misses else "miss",
            }
        )
    return {
        "budget": {"status": "not-evaluated"},
        "cases": reports,
        "goldenSet": {
            "caseCount": len(golden_set.cases),
            "digest": golden_set.digest,
            "name": golden_set.name,
            "schemaVersion": GOLDEN_SET_SCHEMA_VERSION,
        },
        "quality": {
            "casePassRate": passed_cases / len(golden_set.cases),
            "measured": True,
            "evidenceRecall": {
                "hits": total_hits,
                "totalExpected": total_expected,
                "value": total_hits / total_expected,
            },
            "status": "measured",
        },
        "reliability": {"status": "not-evaluated"},
        "reportVersion": EVAL_REPORT_VERSION,
    }


def _write_report(report: dict[str, object], output: Path | None) -> None:
    rendered = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    if output is None:
        print(rendered, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(f"dogfood eval report written: {output}", flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Call or evaluate the loopback dogfood Runtime"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    query = subparsers.add_parser("query", help="render one real resolve")
    query.add_argument("query")
    query.add_argument("--request-id", default="dogfood-maintainer-query")
    run = subparsers.add_parser("run", help="replay golden set v0")
    run.add_argument("--golden-set", type=Path)
    run.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "query":
            configuration = DogfoodHttpConfiguration.load()
            client = DogfoodResolveClient(configuration)
            print(
                render_resolve(
                    client.acquire(query=args.query, request_id=args.request_id)
                )
            )
            return
        if args.command == "run":
            golden_set = load_golden_set(_durable_golden_set_path(args.golden_set))
            configuration = DogfoodHttpConfiguration.load()
            reject_secret_retention(configuration, golden_set)
            _write_report(
                evaluate_golden_set(
                    golden_set,
                    DogfoodResolveClient(configuration),
                ),
                args.output,
            )
            return
    except DogfoodEvaluationUnavailable as error:
        parser.exit(1, f"dogfood evaluation unavailable: {error}\n")
    raise AssertionError("closed parser returned an unknown command")


if __name__ == "__main__":
    main()
