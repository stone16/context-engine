from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY_PATH: Final = REPOSITORY_ROOT / "eval/catalogs/active-carriers-v1.json"
DEFAULT_SCHEMA_PATH: Final = (
    REPOSITORY_ROOT / "eval/catalogs/active-carriers-v1.schema.json"
)
RESULT_SCHEMA_VERSION: Final = "active-carrier-validation-result-v1"
_RELATIONSHIP_CATEGORIES: Final = {
    "authorityRefs": "DANGLING_AUTHORITY",
    "entrypointRefs": "DANGLING_ENTRYPOINT",
    "gateTargets": "DANGLING_GATE",
    "highestPublicTestRefs": "DANGLING_PUBLIC_PROOF",
    "migration": "DANGLING_MIGRATION",
    "operatorDocRefs": "DANGLING_OPERATOR_DOCUMENT",
    "status": "INACTIVE_STATUS_CONFLICT",
    "statusOwner": "DANGLING_STATUS_OWNER",
}
_STATUS_COVERAGE_PATTERN: Final = re.compile(
    r"<!-- active-carrier-registry-v1:start -->\n(?P<body>.*?)"
    r"<!-- active-carrier-registry-v1:end -->",
    re.DOTALL,
)
_STATUS_CARRIER_PATTERN: Final = re.compile(
    r"^- `(?P<id>[a-z0-9-]+)`: `(?P<status>ACTIVE_BOUNDED|NOT_ACTIVE)`$",
    re.MULTILINE,
)
_PUBLIC_PROOF_GATE_BY_DIRECTORY: Final = {
    "tests/unit/": "test",
    "tests/catalog/": "catalog",
    "tests/process/": "smoke",
    "tests/integration/": "integration",
}


@dataclass(frozen=True)
class CarrierValidationReport:
    carrier_count: int
    active_count: int
    inactive_count: int


class CarrierValidationError(ValueError):
    def __init__(self, categories: Sequence[str]) -> None:
        self.categories = tuple(sorted(set(categories)))
        super().__init__(", ".join(self.categories))


def _schema_error_categories(error: Any) -> set[str]:
    path = tuple(error.absolute_path)
    categories = {
        category
        for relationship, category in _RELATIONSHIP_CATEGORIES.items()
        if relationship in path
    }
    if error.validator == "required" and isinstance(error.instance, dict):
        required = error.validator_value
        if isinstance(required, list):
            categories.update(
                _RELATIONSHIP_CATEGORIES[name]
                for name in required
                if name not in error.instance and name in _RELATIONSHIP_CATEGORIES
            )
    return categories or {"REGISTRY_SCHEMA_INVALID"}


def _load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise CarrierValidationError(("REGISTRY_SCHEMA_INVALID",))
    return document


def _logical_makefile_lines(makefile: str) -> tuple[str, ...]:
    logical_lines: list[str] = []
    current: str | None = None
    for physical_line in makefile.splitlines():
        trailing_backslashes = len(physical_line) - len(physical_line.rstrip("\\"))
        continues = trailing_backslashes % 2 == 1
        fragment = physical_line[:-1] if continues else physical_line
        current = (
            fragment if current is None else f"{current.rstrip()} {fragment.lstrip()}"
        )
        if not continues:
            logical_lines.append(current)
            current = None
    if current is not None:
        logical_lines.append(current)
    return tuple(logical_lines)


def _make_rules(makefile: str) -> dict[str, set[str]]:
    rules: dict[str, set[str]] = {}
    for line in _logical_makefile_lines(makefile):
        if not line or line[0].isspace() or ":" not in line:
            continue
        targets_text, prerequisites_text = line.split(":", maxsplit=1)
        targets = {target for target in targets_text.split() if target != "\\"}
        prerequisites = {
            prerequisite
            for prerequisite in prerequisites_text.split(";", maxsplit=1)[0].split()
            if prerequisite != "\\"
        }
        for target in targets:
            rules.setdefault(target, set()).update(prerequisites)
    return rules


def _reference_is_live(reference: Mapping[str, object], repository_root: Path) -> bool:
    raw_path = reference.get("path")
    if not isinstance(raw_path, str):
        return False
    resolved_root = repository_root.resolve()
    candidate = (resolved_root / raw_path).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        return False
    if not candidate.is_file():
        return False
    marker = reference.get("marker")
    return (
        isinstance(marker, str)
        and bool(marker)
        and marker in candidate.read_text(encoding="utf-8")
    )


def _references_are_live(value: object, repository_root: Path) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(reference, dict)
            and _reference_is_live(reference, repository_root)
            for reference in value
        )
    )


def _public_proof_gate_targets(value: object) -> set[str] | None:
    if not isinstance(value, list) or not value:
        return None
    required_targets: set[str] = set()
    for reference in value:
        if not isinstance(reference, dict):
            return None
        path = reference.get("path")
        if not isinstance(path, str):
            return None
        matching_targets = {
            target
            for directory, target in _PUBLIC_PROOF_GATE_BY_DIRECTORY.items()
            if path.startswith(directory)
        }
        if len(matching_targets) != 1:
            return None
        required_targets.update(matching_targets)
    return required_targets


def validate_active_carrier_registry(
    registry: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    repository_root: Path = REPOSITORY_ROOT,
) -> CarrierValidationReport:
    Draft202012Validator.check_schema(schema)
    schema_errors = tuple(Draft202012Validator(schema).iter_errors(registry))
    if schema_errors:
        schema_categories = {
            category
            for error in schema_errors
            for category in _schema_error_categories(error)
        }
        raise CarrierValidationError(tuple(schema_categories))

    categories: set[str] = set()
    carriers = registry["carriers"]
    owners: set[tuple[str, str]] = set()
    carrier_ids: set[str] = set()
    inactive_relations: set[str] = set()
    makefile = (repository_root / "Makefile").read_text(encoding="utf-8")
    make_rules = _make_rules(makefile)
    make_targets = set(make_rules)
    check_targets = make_rules.get("check", set()) | {"check"}
    for carrier in carriers:
        if carrier["id"] in carrier_ids:
            categories.add("DUPLICATE_CARRIER_OWNER")
        carrier_ids.add(carrier["id"])
        owner = carrier["statusOwner"]
        owner_key = (owner["path"], owner.get("marker", ""))
        if owner_key in owners:
            categories.add("DUPLICATE_CARRIER_OWNER")
        owners.add(owner_key)
        if not _reference_is_live(owner, repository_root):
            categories.add("DANGLING_STATUS_OWNER")
        if not _references_are_live(carrier["authorityRefs"], repository_root):
            categories.add("DANGLING_AUTHORITY")
        if not _references_are_live(carrier["entrypointRefs"], repository_root):
            categories.add("DANGLING_ENTRYPOINT")
        migration = carrier["migration"]
        if "refs" in migration and not _references_are_live(
            migration["refs"], repository_root
        ):
            categories.add("DANGLING_MIGRATION")
        public_proof_refs = carrier["highestPublicTestRefs"]
        public_proof_is_live = _references_are_live(public_proof_refs, repository_root)
        if not public_proof_is_live:
            categories.add("DANGLING_PUBLIC_PROOF")
        gate_targets = set(carrier["gateTargets"])
        gate_targets_are_live = gate_targets.issubset(make_targets & check_targets)
        public_proof_targets = (
            _public_proof_gate_targets(public_proof_refs)
            if public_proof_is_live
            else None
        )
        if not gate_targets_are_live or (
            public_proof_is_live
            and (
                public_proof_targets is None
                or gate_targets != public_proof_targets | {"check"}
            )
        ):
            categories.add("DANGLING_GATE")
        if not _references_are_live(carrier["operatorDocRefs"], repository_root):
            categories.add("DANGLING_OPERATOR_DOCUMENT")
        carrier_is_inactive = carrier["status"] == "NOT_ACTIVE"
        if carrier_is_inactive:
            inactive_relations.update(carrier["notActiveRelations"])
    if inactive_relations != set(registry["closedNotActiveRelations"]):
        categories.add("INACTIVE_STATUS_CONFLICT")
    status_text = (repository_root / "STATUS.md").read_text(encoding="utf-8")
    status_coverage = _STATUS_COVERAGE_PATTERN.search(status_text)
    covered_statuses = (
        dict(_STATUS_CARRIER_PATTERN.findall(status_coverage.group("body")))
        if status_coverage is not None
        else {}
    )
    registry_statuses = {carrier["id"]: carrier["status"] for carrier in carriers}
    if covered_statuses != registry_statuses:
        categories.add("DANGLING_STATUS_OWNER")
    if categories:
        raise CarrierValidationError(tuple(categories))

    active_count = sum(carrier["status"] == "ACTIVE_BOUNDED" for carrier in carriers)
    return CarrierValidationReport(
        carrier_count=len(carriers),
        active_count=active_count,
        inactive_count=len(carriers) - active_count,
    )


def _result(*, status: str, categories: Sequence[str]) -> dict[str, object]:
    return {
        "capabilityCompleteness": "COMPLETE" if status == "PASS" else "INCOMPLETE",
        "categories": list(categories),
        "schemaVersion": RESULT_SCHEMA_VERSION,
        "securityVeto": "SEPARATE_NOT_EVALUATED",
        "status": status,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA_PATH)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        try:
            registry = _load_json(arguments.registry)
            schema = _load_json(arguments.schema)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CarrierValidationError(("REGISTRY_SCHEMA_INVALID",)) from error
        validate_active_carrier_registry(
            registry,
            schema,
            repository_root=arguments.repository_root.resolve(),
        )
    except (CarrierValidationError, SchemaError) as error:
        categories = (
            error.categories
            if isinstance(error, CarrierValidationError)
            else ("REGISTRY_SCHEMA_INVALID",)
        )
        print(json.dumps(_result(status="FAIL", categories=categories), sort_keys=True))
        return 1
    except (OSError, UnicodeDecodeError):
        print(
            json.dumps(
                _result(status="FAIL", categories=("REPOSITORY_UNAVAILABLE",)),
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(_result(status="PASS", categories=()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
