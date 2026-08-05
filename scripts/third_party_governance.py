#!/usr/bin/env python3
"""Validate registered third-party reuse and its distributed evidence."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator, FormatChecker

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
THIRD_PARTY_ROOT = REPOSITORY_ROOT / "third_party"
SCHEMA_PATH = REPOSITORY_ROOT / "schemas/third-party-upstream.schema.json"
ARTIFACT_EXEMPTIONS_RELATIVE_PATH = Path("third_party/ARTIFACT_EXEMPTIONS.toml")
ARTIFACT_EXEMPTIONS_SCHEMA_RELATIVE_PATH = Path(
    "schemas/third-party-artifact-exemptions.schema.json"
)
NOTICES_PATH = REPOSITORY_ROOT / "THIRD_PARTY_NOTICES.md"
SBOM_PATH = REPOSITORY_ROOT / "THIRD_PARTY_SBOM.cyclonedx.json"
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
GOVERNANCE_FILES = frozenset(
    {"UPSTREAM.toml", "LICENSE.upstream", "MODIFICATIONS.md", "sbom.cyclonedx.json"}
)
ArtifactKind = Literal["wheel", "sdist", "npm", "container"]
ARTIFACT_KINDS: tuple[ArtifactKind, ...] = (
    "wheel",
    "sdist",
    "npm",
    "container",
)


class GovernanceError(ValueError):
    """A deterministic third-party governance violation."""


@dataclass(frozen=True)
class Registration:
    name: str
    root: Path
    path: Path
    data: Mapping[str, Any]


@dataclass(frozen=True)
class ArtifactStatus:
    kind: ArtifactKind
    artifact: Path | None
    passed: bool
    detail: str
    exempted: bool = False


@dataclass(frozen=True)
class ArtifactExemption:
    kind: ArtifactKind
    approval_reference: str


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_errors(data: Any, schema_path: Path) -> list[Any]:
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return sorted(validator.iter_errors(data), key=lambda item: list(item.path))


def _raise_schema_error(errors: Sequence[Any], *, document_path: Path) -> None:
    if not errors:
        return
    error = errors[0]
    location = ".".join(str(part) for part in error.absolute_path) or "<root>"
    raise GovernanceError(
        f"{document_path}: schema violation at {location}: {error.message}"
    )


def load_artifact_exemptions(
    root: Path = REPOSITORY_ROOT,
) -> dict[ArtifactKind, ArtifactExemption]:
    path = root / ARTIFACT_EXEMPTIONS_RELATIVE_PATH
    schema_path = root / ARTIFACT_EXEMPTIONS_SCHEMA_RELATIVE_PATH
    if not path.is_file():
        raise GovernanceError(f"artifact exemptions: required policy missing: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise GovernanceError(f"{path}: invalid TOML: {error}") from error
    _raise_schema_error(_schema_errors(data, schema_path), document_path=path)

    exemptions: dict[ArtifactKind, ArtifactExemption] = {}
    for record in data["exemptions"]:
        kind = cast(ArtifactKind, record["artifact_kind"])
        if kind in exemptions:
            raise GovernanceError(f"{path}: duplicate exemption for {kind}")
        exemptions[kind] = ArtifactExemption(
            kind=kind,
            approval_reference=record["approval_reference"],
        )
    return exemptions


def discover_registrations(root: Path = REPOSITORY_ROOT) -> tuple[Registration, ...]:
    third_party_root = root / "third_party"
    if not third_party_root.is_dir():
        raise GovernanceError("registration discovery: third_party/ is missing")
    registrations: list[Registration] = []
    for child in sorted(third_party_root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        registration_path = child / "UPSTREAM.toml"
        if not registration_path.is_file():
            subtree = f"third_party/{child.name}"
            raise GovernanceError(
                f"registration discovery: subtree {subtree} lacks UPSTREAM.toml"
            )
        try:
            data = tomllib.loads(registration_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise GovernanceError(
                f"{registration_path}: invalid TOML: {error}"
            ) from error
        registrations.append(Registration(child.name, child, registration_path, data))
    if not registrations:
        raise GovernanceError(
            "registration discovery: no third-party registrations found"
        )
    return tuple(registrations)


def validate_schema(
    registration: Registration,
    schema_path: Path = SCHEMA_PATH,
) -> None:
    _raise_schema_error(
        _schema_errors(registration.data, schema_path),
        document_path=registration.path,
    )


def _contains(parent: PurePosixPath, child: PurePosixPath) -> bool:
    return child == parent or parent in child.parents


def _repository_path(root: Path, value: str, *, field: str) -> Path:
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or "\\" in value:
        raise GovernanceError(f"{field}: unsafe repository path {value!r}")
    candidate = root.joinpath(*relative.parts).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise GovernanceError(f"{field}: path escapes repository: {value!r}") from error
    return candidate


def _markdown_heading_anchors(document: str) -> set[str]:
    anchors: set[str] = set()
    occurrences: dict[str, int] = {}
    for line in document.splitlines():
        match = re.fullmatch(r" {0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*", line)
        if match is None:
            continue
        heading = match.group(1).strip().lower()
        anchor = re.sub(r"[^\w\- ]", "", heading, flags=re.UNICODE).replace(
            " ", "-"
        )
        duplicate = occurrences.get(anchor, 0)
        occurrences[anchor] = duplicate + 1
        anchors.add(anchor if duplicate == 0 else f"{anchor}-{duplicate}")
    return anchors


def _validate_decision_document(
    value: str, *, registration: Registration, root: Path
) -> None:
    document_value, separator, anchor = value.rpartition("#")
    if not separator or not document_value or not anchor:
        raise GovernanceError(
            f"{registration.path}: decision document must include a heading anchor"
        )
    document_path = _repository_path(
        root, document_value, field="approvals.decision_document"
    )
    if not document_path.is_file():
        raise GovernanceError(
            f"{registration.path}: decision document missing: {document_value}"
        )
    if anchor not in _markdown_heading_anchors(
        document_path.read_text(encoding="utf-8")
    ):
        raise GovernanceError(
            f"{registration.path}: decision document anchor is missing: {value}"
        )


def _top_level_function_region(source: bytes, name: str, *, label: str) -> bytes:
    try:
        text = source.decode("utf-8")
        module = ast.parse(text)
    except (UnicodeDecodeError, SyntaxError) as error:
        raise GovernanceError(
            f"selector source is not valid Python: {label}"
        ) from error
    matches = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == name
    ]
    if len(matches) != 1:
        raise GovernanceError(
            f"selector {name!r} missing or duplicated in {label} bytes"
        )
    node = matches[0]
    first_line = min(
        (decorator.lineno for decorator in node.decorator_list),
        default=node.lineno,
    )
    if node.end_lineno is None:
        raise GovernanceError(f"selector {name!r} has no end line in {label} bytes")
    return b"".join(source.splitlines(keepends=True)[first_line - 1 : node.end_lineno])


def _reconstruct_pinned_source(
    *, source_path: PurePosixPath, vendored: Path, patch_path: Path
) -> bytes:
    with tempfile.TemporaryDirectory(prefix="context-engine-upstream-") as directory:
        checkout = Path(directory)
        reconstructed = checkout.joinpath(*source_path.parts)
        reconstructed.parent.mkdir(parents=True)
        shutil.copyfile(vendored, reconstructed)
        command = ["git", "apply", "--reverse", str(patch_path)]
        result = subprocess.run(
            command,
            cwd=checkout,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise GovernanceError(
                f"selector patch cannot reconstruct pinned bytes: {patch_path}: "
                f"{detail}"
            )
        return reconstructed.read_bytes()


def _validate_selector(
    selector: Mapping[str, Any],
    *,
    registration: Registration,
    root: Path,
    vendored_by_upstream: Mapping[PurePosixPath, Path],
) -> None:
    source_path = PurePosixPath(selector["source_path"])
    vendored = vendored_by_upstream.get(source_path)
    if vendored is None or not vendored.is_file():
        raise GovernanceError(
            f"{registration.path}: selector source is missing from vendored bytes: "
            f"{source_path}"
        )
    name = selector["name"]
    vendored_region = _top_level_function_region(
        vendored.read_bytes(), name, label="vendored"
    )
    vendored_digest = hashlib.sha256(vendored_region).hexdigest()
    if vendored_digest != selector["vendored_sha256"]:
        raise GovernanceError(
            f"{registration.path}: selector {name!r} vendored hash mismatch: "
            f"expected {selector['vendored_sha256']}, got {vendored_digest}"
        )
    patch_path = _repository_path(
        root, selector["patch_path"], field="source_selectors.patch_path"
    )
    try:
        patch_path.relative_to((registration.root / "patches").resolve())
    except ValueError as error:
        raise GovernanceError(
            f"{registration.path}: selector patch is outside its registered patches"
        ) from error
    if not patch_path.is_file():
        raise GovernanceError(
            f"{registration.path}: selector patch is missing: {patch_path}"
        )
    pinned_source = _reconstruct_pinned_source(
        source_path=source_path,
        vendored=vendored,
        patch_path=patch_path,
    )
    pinned_region = _top_level_function_region(pinned_source, name, label="pinned")
    pinned_digest = hashlib.sha256(pinned_region).hexdigest()
    if pinned_digest != selector["pinned_sha256"]:
        raise GovernanceError(
            f"{registration.path}: selector {name!r} pinned hash mismatch: "
            f"expected {selector['pinned_sha256']}, got {pinned_digest}"
        )


def validate_registration(
    registration: Registration, root: Path = REPOSITORY_ROOT
) -> set[Path]:
    validate_schema(registration, root / "schemas/third-party-upstream.schema.json")
    data = registration.data
    commit = data["commit"]
    if not isinstance(commit, str) or COMMIT_PATTERN.fullmatch(commit) is None:
        raise GovernanceError(f"{registration.path}: commit is not an exact 40-hex SHA")

    source_paths = tuple(PurePosixPath(value) for value in data["source_paths"])
    excluded_paths = tuple(PurePosixPath(value) for value in data["excluded_paths"])
    if len(set(source_paths)) != len(source_paths):
        raise GovernanceError(
            f"{registration.path}: source_paths contains duplicate canonical paths"
        )
    if len(set(excluded_paths)) != len(excluded_paths):
        raise GovernanceError(
            f"{registration.path}: excluded_paths contains duplicate canonical paths"
        )
    approval_owners: dict[PurePosixPath, str] = {}
    selector_owners: dict[tuple[PurePosixPath, str, str], str] = {}
    selectors: list[Mapping[str, Any]] = []
    for approval in data["approvals"]:
        if "decision_document" in approval:
            _validate_decision_document(
                approval["decision_document"], registration=registration, root=root
            )
        approval_paths = tuple(
            PurePosixPath(value) for value in approval.get("source_paths", [])
        )
        if len(set(approval_paths)) != len(approval_paths):
            raise GovernanceError(
                f"{registration.path}: approval contains duplicate canonical paths"
            )
        for selector in approval.get("source_selectors", []):
            selector_path = PurePosixPath(selector["source_path"])
            selector_key = (selector_path, selector["kind"], selector["name"])
            if selector_key in selector_owners:
                raise GovernanceError(
                    f"{registration.path}: source selector is claimed by multiple "
                    "approval records"
                )
            if selector_path not in source_paths:
                raise GovernanceError(
                    f"{registration.path}: source selector {selector_path} is not "
                    "a registered copied source"
                )
            selector_owners[selector_key] = approval["reference"]
            selectors.append(selector)
        for approval_path in approval_paths:
            if approval_path in approval_owners:
                raise GovernanceError(
                    f"{registration.path}: source region {approval_path} is claimed by "
                    "multiple approval records"
                )
            approval_owners[approval_path] = approval["reference"]
    source_path_set = set(source_paths)
    approval_path_set = set(approval_owners)
    selector_path_set = {key[0] for key in selector_owners}
    ambiguous = sorted(str(path) for path in approval_path_set & selector_path_set)
    if ambiguous:
        raise GovernanceError(
            f"{registration.path}: source has ambiguous whole-file and selector "
            f"approval ownership: {ambiguous[0]}"
        )
    covered_path_set = approval_path_set | selector_path_set
    if covered_path_set != source_path_set:
        missing = sorted(str(path) for path in source_path_set - covered_path_set)
        unknown = sorted(str(path) for path in covered_path_set - source_path_set)
        detail = f"missing={missing}, unknown={unknown}"
        raise GovernanceError(
            f"{registration.path}: approval coverage must match source_paths "
            f"exactly: {detail}"
        )
    overlaps = sorted(str(path) for path in set(source_paths) & set(excluded_paths))
    if overlaps:
        detail = f"path listed as both copied and excluded: {overlaps[0]}"
        raise GovernanceError(f"{registration.path}: {detail}")
    for copied in source_paths:
        for excluded in excluded_paths:
            if _contains(excluded, copied):
                detail = (
                    f"copied path {copied} resolves into excluded region {excluded}"
                )
                raise GovernanceError(f"{registration.path}: {detail}")

    required = (
        registration.root / "LICENSE.upstream",
        registration.root / "MODIFICATIONS.md",
    )
    for path in required:
        if not path.is_file():
            raise GovernanceError(
                f"{registration.path}: required file missing: {path.name}"
            )
    patches = registration.root / "patches"
    if not patches.is_dir():
        raise GovernanceError(
            f"{registration.path}: required local patches directory missing"
        )

    claimed = {registration.root / name for name in GOVERNANCE_FILES}
    claimed.update(path for path in patches.rglob("*") if path.is_file())
    seen_upstream: set[PurePosixPath] = set()
    seen_vendored: set[Path] = set()
    vendored_by_upstream: dict[PurePosixPath, Path] = {}
    for index, file_data in enumerate(data["files"]):
        upstream = PurePosixPath(file_data["upstream_path"])
        for excluded in excluded_paths:
            if _contains(excluded, upstream):
                detail = (
                    f"copied file {upstream} resolves into excluded region {excluded}"
                )
                raise GovernanceError(f"{registration.path}: {detail}")
        if not any(_contains(source, upstream) for source in source_paths):
            raise GovernanceError(
                f"{registration.path}: copied file {upstream} is outside source_paths"
            )
        vendored = _repository_path(
            root, file_data["vendored_path"], field=f"files[{index}].vendored_path"
        )
        try:
            vendored.relative_to(registration.root.resolve())
        except ValueError as error:
            detail = f"vendored path is outside its registered subtree: {vendored}"
            raise GovernanceError(f"{registration.path}: {detail}") from error
        if upstream in seen_upstream or vendored in seen_vendored:
            raise GovernanceError(
                f"{registration.path}: duplicate copied file registration"
            )
        seen_upstream.add(upstream)
        seen_vendored.add(vendored)
        vendored_by_upstream[upstream] = vendored
        if not vendored.is_file():
            raise GovernanceError(
                f"{registration.path}: copied file missing: {vendored}"
            )
        actual_hash = hashlib.sha256(vendored.read_bytes()).hexdigest()
        if actual_hash != file_data["sha256"]:
            raise GovernanceError(
                f"{registration.path}: hash mismatch for {file_data['vendored_path']}: "
                f"expected {file_data['sha256']}, got {actual_hash}"
            )
        claimed.add(vendored)

    for selector in selectors:
        _validate_selector(
            selector,
            registration=registration,
            root=root,
            vendored_by_upstream=vendored_by_upstream,
        )

    for dependency in data.get("nested_dependencies", []):
        license_path = _repository_path(
            root, dependency["license_path"], field="nested_dependencies.license_path"
        )
        try:
            license_path.relative_to(registration.root.resolve())
        except ValueError as error:
            raise GovernanceError(
                f"{registration.path}: nested dependency license is outside its subtree"
            ) from error
        if not license_path.is_file():
            detail = f"nested dependency license missing: {license_path}"
            raise GovernanceError(f"{registration.path}: {detail}")
        claimed.add(license_path)
    return {path.resolve() for path in claimed}


def validate_tree(root: Path = REPOSITORY_ROOT) -> tuple[Registration, ...]:
    load_artifact_exemptions(root)
    registrations = discover_registrations(root)
    claimed: set[Path] = set()
    for registration in registrations:
        claimed.update(validate_registration(registration, root))
    files = {
        path.resolve()
        for registration in registrations
        for path in registration.root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    orphans = sorted(files - claimed)
    if orphans:
        relative = orphans[0].relative_to(root.resolve())
        raise GovernanceError(
            f"orphan file is not claimed by a registration: {relative}"
        )
    return registrations


def render_notices(registrations: Sequence[Registration]) -> str:
    lines = [
        "# Third-party notices",
        "",
        "This file is generated by `scripts/third_party_governance.py`. Do not edit it",
        "by hand. Each registered source is pinned and distributed with its license",
        "text.",
    ]
    for registration in registrations:
        data = registration.data
        license_path = f"third_party/{registration.name}/LICENSE.upstream"
        lines.extend(
            [
                "",
                f"## {registration.name}",
                "",
                f"- Upstream: {data['repository']}",
                f"- Commit: `{data['commit']}`",
                f"- License: {data['license']} (`{license_path}`)",
                f"- Reuse mode: `{data['reuse_mode']}`",
                "- Copied source provenance:",
                *(
                    f"  - `{path}` — {data['repository']}#{data['commit']}"
                    for path in data["source_paths"]
                ),
                "- Approvals by source region:",
            ]
        )
        for approval in data["approvals"]:
            decision = (
                f"; {approval['decision']} ({approval['decision_document']})"
                if "decision" in approval
                else ""
            )
            for path in approval.get("source_paths", []):
                lines.append(
                    f"  - `{path}` — {approval['reference']}{decision}"
                )
            for selector in approval.get("source_selectors", []):
                region = (
                    f"{selector['source_path']}::{selector['kind']}:"
                    f"{selector['name']}"
                )
                lines.append(
                    f"  - `{region}` — {approval['reference']}{decision}"
                )
        dependencies = data.get("nested_dependencies", [])
        if dependencies:
            lines.extend(["- Nested dependencies:"])
            lines.extend(
                f"  - {item['name']} {item['version']} — {item['license']} "
                f"(`{item['license_path']}`)"
                for item in dependencies
            )
    return "\n".join(lines) + "\n"


def render_sbom(registrations: Sequence[Registration]) -> str:
    components: list[dict[str, Any]] = []
    for registration in registrations:
        data = registration.data
        components.append(
            {
                "bom-ref": f"context-engine:third-party:{registration.name}",
                "type": "library",
                "name": registration.name,
                "version": data["commit"],
                "licenses": [{"license": {"id": data["license"]}}],
                "externalReferences": [
                    {"type": "vcs", "url": f"{data['repository']}#{data['commit']}"}
                ],
                "properties": [
                    {
                        "name": "context-engine:scope",
                        "value": f"third_party/{registration.name}",
                    },
                    *(
                        {
                            "name": "context-engine:source-provenance",
                            "value": (
                                f"{path}={data['repository']}#{data['commit']}"
                            ),
                        }
                        for path in data["source_paths"]
                    ),
                    *(
                        {
                            "name": "context-engine:file-sha256",
                            "value": f"{item['vendored_path']}={item['sha256']}",
                        }
                        for item in data["files"]
                    ),
                    *(
                        {
                            "name": "context-engine:source-approval",
                            "value": _sbom_approval_value(approval, path),
                        }
                        for approval in data["approvals"]
                        for path in approval.get("source_paths", [])
                    ),
                    *(
                        {
                            "name": "context-engine:source-approval",
                            "value": _sbom_selector_approval_value(
                                approval, selector
                            ),
                        }
                        for approval in data["approvals"]
                        for selector in approval.get("source_selectors", [])
                    ),
                ],
            }
        )
        components.extend(
            {
                "bom-ref": (
                    f"context-engine:third-party:{registration.name}:nested:"
                    f"{dependency['name']}@{dependency['version']}"
                ),
                "type": "library",
                "name": dependency["name"],
                "version": dependency["version"],
                "licenses": [{"license": {"id": dependency["license"]}}],
                "properties": [
                    {
                        "name": "context-engine:license-path",
                        "value": dependency["license_path"],
                    },
                    {
                        "name": "context-engine:parent-subtree",
                        "value": f"third_party/{registration.name}",
                    },
                ],
            }
            for dependency in data.get("nested_dependencies", [])
        )
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {
                "bom-ref": "context-engine:source-tree",
                "type": "application",
                "name": "context-engine",
            }
        },
        "components": components,
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def _sbom_approval_value(approval: dict[str, Any], path: str) -> str:
    decision = (
        f"; decision={approval['decision']}; "
        f"decision_document={approval['decision_document']}"
        if "decision" in approval
        else ""
    )
    return f"{path}={approval['reference']}{decision}"


def _sbom_selector_approval_value(
    approval: dict[str, Any], selector: Mapping[str, Any]
) -> str:
    region = (
        f"{selector['source_path']}::{selector['kind']}:{selector['name']}"
    )
    decision = (
        f"; decision={approval['decision']}; "
        f"decision_document={approval['decision_document']}"
        if "decision" in approval
        else ""
    )
    return f"{region}={approval['reference']}{decision}"


def _write_or_check(path: Path, content: str, *, check: bool, root: Path) -> None:
    if check:
        actual = path.read_text(encoding="utf-8") if path.is_file() else ""
        if actual != content:
            raise GovernanceError(f"generated file drift: {path.relative_to(root)}")
        return
    path.write_text(content, encoding="utf-8")


def generate_aggregates(*, root: Path = REPOSITORY_ROOT, check: bool) -> None:
    registrations = validate_tree(root)
    _write_or_check(
        root / NOTICES_PATH.name,
        render_notices(registrations),
        check=check,
        root=root,
    )
    sbom_content = render_sbom(registrations)
    _write_or_check(root / SBOM_PATH.name, sbom_content, check=check, root=root)
    validate_sbom_coverage(registrations, json.loads(sbom_content))


def validate_sbom_coverage(
    registrations: Sequence[Registration], sbom: Mapping[str, Any]
) -> None:
    references = {
        component.get("bom-ref")
        for component in sbom.get("components", [])
        if isinstance(component, Mapping)
    }
    for registration in registrations:
        expected = f"context-engine:third-party:{registration.name}"
        if expected not in references:
            raise GovernanceError(
                f"SBOM missing vendored subtree: third_party/{registration.name}"
            )


def _archive_members(path: Path, kind: ArtifactKind) -> dict[str, bytes]:
    if kind == "wheel" or path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            return {
                name.rstrip("/"): archive.read(name)
                for name in archive.namelist()
                if not name.endswith("/")
            }
    with tarfile.open(path, "r:*") as archive:
        members = {
            member.name.lstrip("./").rstrip("/"): extracted.read()
            for member in archive
            if member.isfile()
            and (extracted := archive.extractfile(member)) is not None
        }
        if kind != "container":
            return members
        layer_names = sorted(name for name in members if name.endswith("/layer.tar"))
        if not layer_names:
            return members
        filesystem: dict[str, bytes] = {}
        for layer_name in layer_names:
            with tarfile.open(
                fileobj=io.BytesIO(members[layer_name]), mode="r:"
            ) as layer:
                for member in layer:
                    extracted = layer.extractfile(member) if member.isfile() else None
                    if extracted is not None:
                        filesystem[member.name.lstrip("./").rstrip("/")] = (
                            extracted.read()
                        )
        return filesystem


def required_artifact_texts(registrations: Sequence[Registration]) -> tuple[str, ...]:
    required = ["LICENSE", "NOTICE", NOTICES_PATH.name, SBOM_PATH.name]
    for registration in registrations:
        required.append(f"third_party/{registration.name}/LICENSE.upstream")
        required.extend(
            dependency["license_path"]
            for dependency in registration.data.get("nested_dependencies", [])
        )
    return tuple(dict.fromkeys(required))


def _required_artifact_evidence(
    registrations: Sequence[Registration], root: Path
) -> dict[str, bytes]:
    return {
        path: (root / path).read_bytes()
        for path in required_artifact_texts(registrations)
    }


def inspect_artifact(
    kind: ArtifactKind,
    artifact: Path | None,
    registrations: Sequence[Registration],
    *,
    root: Path = REPOSITORY_ROOT,
    exemption: ArtifactExemption | None = None,
) -> ArtifactStatus:
    if artifact is None:
        if exemption is not None:
            return ArtifactStatus(
                kind,
                artifact,
                True,
                "maintainer decision",
                exempted=True,
            )
        return ArtifactStatus(kind, artifact, False, "artifact kind not produced")
    if not artifact.is_file():
        return ArtifactStatus(kind, artifact, False, "artifact path does not exist")
    try:
        members = _archive_members(artifact, kind)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        return ArtifactStatus(
            kind, artifact, False, f"cannot inspect artifact: {error}"
        )
    missing: list[str] = []
    stale: list[str] = []
    for required, expected_bytes in _required_artifact_evidence(
        registrations, root
    ).items():
        matches = [
            content
            for member, content in members.items()
            if member == required or member.endswith(f"/{required}")
        ]
        if not matches:
            missing.append(required)
        elif expected_bytes not in matches:
            stale.append(required)
    if missing:
        return ArtifactStatus(
            kind,
            artifact,
            False,
            "missing distributed evidence: " + ", ".join(missing),
        )
    if stale:
        return ArtifactStatus(
            kind,
            artifact,
            False,
            "distributed evidence differs from repository: " + ", ".join(stale),
        )
    return ArtifactStatus(kind, artifact, True, "all required evidence is present")


def inspect_all_artifacts(
    artifacts: Mapping[ArtifactKind, Path | None],
    registrations: Sequence[Registration],
    *,
    root: Path = REPOSITORY_ROOT,
    exemptions: Mapping[ArtifactKind, ArtifactExemption] | None = None,
) -> tuple[ArtifactStatus, ...]:
    exemptions = exemptions or {}
    return tuple(
        inspect_artifact(
            kind,
            artifacts.get(kind),
            registrations,
            root=root,
            exemption=exemptions.get(kind),
        )
        for kind in ARTIFACT_KINDS
    )


def report_artifact_statuses(statuses: Sequence[ArtifactStatus]) -> int:
    for status in statuses:
        if status.exempted:
            print(f"{status.kind}: NOT_PRODUCED (maintainer decision)")
            continue
        label = "PASS" if status.passed else "FAIL"
        artifact = str(status.artifact) if status.artifact is not None else "<absent>"
        print(f"{status.kind}: {label} — {status.detail} ({artifact})")
    return 0 if all(status.passed for status in statuses) else 1


def _only_artifact(paths: Iterable[Path]) -> Path | None:
    produced = sorted(path for path in paths if path.is_file())
    return produced[-1] if produced else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    generate = subparsers.add_parser("generate")
    generate.add_argument("--check", action="store_true")
    artifacts = subparsers.add_parser("artifacts")
    artifacts.add_argument("--wheel", type=Path)
    artifacts.add_argument("--sdist", type=Path)
    artifacts.add_argument("--npm", type=Path)
    artifacts.add_argument("--container", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            registrations = validate_tree()
            print(
                f"third-party governance: PASS ({len(registrations)} registration(s))"
            )
            return 0
        if args.command == "generate":
            generate_aggregates(check=args.check)
            mode = "check" if args.check else "write"
            print(f"third-party aggregates: PASS ({mode})")
            return 0
        registrations = validate_tree()
        artifact_paths: dict[ArtifactKind, Path | None] = {
            "wheel": args.wheel
            or _only_artifact((REPOSITORY_ROOT / "dist").glob("*.whl")),
            "sdist": args.sdist
            or _only_artifact((REPOSITORY_ROOT / "dist").glob("*.tar.gz")),
            "npm": args.npm
            or _only_artifact((REPOSITORY_ROOT / ".context-engine/sdk").glob("*.tgz")),
            "container": args.container,
        }
        exemptions = load_artifact_exemptions()
        statuses = inspect_all_artifacts(
            artifact_paths,
            registrations,
            exemptions=exemptions,
        )
        return report_artifact_statuses(statuses)
    except (GovernanceError, OSError, json.JSONDecodeError) as error:
        print(f"third-party governance: FAIL — {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
