"""Fail-closed code and schema binding for the durable daily driver."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from applications.preflight import (
    PreflightConfigurationMalformed,
    PreflightConfigurationMissing,
    load_preflight_configuration,
    packaged_schema_head,
    probe_schema_readiness,
)

READY_DEPLOYMENT_MANIFEST: Final = "launchd/render-manifest.json"
_REVISION = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class SchemaBindingRefused(ValueError):
    """The database is not proven to match the packaged migration head."""


class DeploymentBindingRefused(ValueError):
    """The process checkout is not bound to the current exact schema state."""


@dataclass(frozen=True)
class DeploymentBinding:
    """Validated content-free binding between one checkout and schema state."""

    code_revision: str
    schema_state_digest: str

    def __post_init__(self) -> None:
        if (
            _REVISION.fullmatch(self.code_revision) is None
            or _DIGEST.fullmatch(self.schema_state_digest) is None
        ):
            raise ValueError("deployment binding is invalid")


@dataclass(frozen=True)
class DeploymentManifest:
    """The single v2 contract shared by renderer and process verification."""

    binding: DeploymentBinding
    label_prefix: str
    plists: frozenset[str]
    status: str

    def __post_init__(self) -> None:
        if (
            not self.label_prefix
            or not self.plists
            or self.status not in {"preparing", "ready"}
            or any(
                Path(name).name != name or not name.endswith(".plist")
                for name in self.plists
            )
        ):
            raise ValueError("deployment manifest is invalid")

    @classmethod
    def from_document(cls, document: object) -> DeploymentManifest:
        if (
            type(document) is not dict
            or set(document)
            != {
                "codeRevision",
                "labelPrefix",
                "plists",
                "schemaStateDigest",
                "schemaVersion",
                "status",
            }
            or document["schemaVersion"] != 2
            or type(document["codeRevision"]) is not str
            or type(document["schemaStateDigest"]) is not str
            or type(document["labelPrefix"]) is not str
            or type(document["plists"]) is not list
            or any(type(name) is not str for name in document["plists"])
            or len(set(document["plists"])) != len(document["plists"])
            or type(document["status"]) is not str
        ):
            raise ValueError("deployment manifest is invalid")
        return cls(
            binding=DeploymentBinding(
                code_revision=document["codeRevision"],
                schema_state_digest=document["schemaStateDigest"],
            ),
            label_prefix=document["labelPrefix"],
            plists=frozenset(document["plists"]),
            status=document["status"],
        )

    def to_document(self) -> dict[str, object]:
        return {
            "codeRevision": self.binding.code_revision,
            "labelPrefix": self.label_prefix,
            "plists": sorted(self.plists),
            "schemaStateDigest": self.binding.schema_state_digest,
            "schemaVersion": 2,
            "status": self.status,
        }


def current_code_revision(checkout: Path) -> str:
    """Return the exact committed code revision without accepting dirty state."""

    try:
        status = subprocess.run(
            ("git", "-C", str(checkout), "status", "--porcelain"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        revision = subprocess.run(
            ("git", "-C", str(checkout), "rev-parse", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise DeploymentBindingRefused from None
    if status or _REVISION.fullmatch(revision) is None:
        raise DeploymentBindingRefused
    return revision


def require_exact_schema_binding(environment: Mapping[str, str]) -> str:
    """Reuse preflight's closed classifier and return an exact-state digest."""

    try:
        configuration = load_preflight_configuration(
            environment,
            selected_planes=("migration",),
        )
        if probe_schema_readiness(configuration) != "ready":
            raise SchemaBindingRefused
        head = packaged_schema_head()
    except (
        PreflightConfigurationMalformed,
        PreflightConfigurationMissing,
        RuntimeError,
        SchemaBindingRefused,
    ):
        raise SchemaBindingRefused from None
    document = json.dumps(
        {"alembicHeads": [head]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(document).hexdigest()}"


def current_deployment_binding(
    checkout: Path,
    environment: Mapping[str, str],
) -> DeploymentBinding:
    """Construct the one validated binding setup may publish."""

    return DeploymentBinding(
        code_revision=current_code_revision(checkout),
        schema_state_digest=require_exact_schema_binding(environment),
    )


def verify_ready_deployment(
    *,
    checkout: Path,
    database_environment: Mapping[str, str],
) -> None:
    """Refuse unless manifest, checkout, and live schema are one exact binding."""

    path = checkout / ".context-engine" / READY_DEPLOYMENT_MANIFEST
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o777 != 0o600
        ):
            raise DeploymentBindingRefused
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            descriptor = -1
            raw = handle.read()
        try:
            manifest = DeploymentManifest.from_document(json.loads(raw))
        except ValueError:
            raise DeploymentBindingRefused from None
        if manifest.status != "ready":
            raise DeploymentBindingRefused
        if manifest.binding.code_revision != current_code_revision(checkout):
            raise DeploymentBindingRefused
        try:
            schema_digest = require_exact_schema_binding(database_environment)
        except SchemaBindingRefused:
            raise DeploymentBindingRefused from None
        if manifest.binding.schema_state_digest != schema_digest:
            raise DeploymentBindingRefused
    except (
        DeploymentBindingRefused,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
    ):
        raise DeploymentBindingRefused from None
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)


__all__ = [
    "DeploymentBindingRefused",
    "DeploymentBinding",
    "DeploymentManifest",
    "READY_DEPLOYMENT_MANIFEST",
    "SchemaBindingRefused",
    "current_code_revision",
    "current_deployment_binding",
    "require_exact_schema_binding",
    "verify_ready_deployment",
]
