"""Deterministically render tracked launchd templates without installing them."""

from __future__ import annotations

import json
import os
import plistlib
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from string import Template
from xml.sax.saxutils import escape

from engine.learning.golden_storage import require_durable_storage_root
from scripts.daily_driver.backup import require_safe_backup_root
from scripts.daily_driver.environment import EnvironmentRefused, load_owner_environment

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]+")
_RENDER_MANIFEST = "render-manifest.json"


class LaunchdRenderRefused(ValueError):
    """A required render input or tracked template is invalid."""


@dataclass(frozen=True)
class LaunchdRenderConfiguration:
    checkout: Path
    backup_root: Path
    docker_executable: Path
    uv_executable: Path
    label_prefix: str
    backup_hour: int
    scan_hour: int
    health_interval_seconds: int
    api_port: int

    def __post_init__(self) -> None:
        required = (
            self.checkout,
            self.backup_root,
            self.docker_executable,
            self.uv_executable,
            self.label_prefix,
            self.backup_hour,
            self.scan_hour,
            self.health_interval_seconds,
            self.api_port,
        )
        if any(value is None or value == "" for value in required):
            raise LaunchdRenderRefused("every launchd render input is required")
        if _LABEL.fullmatch(self.label_prefix) is None:
            raise LaunchdRenderRefused("launchd label prefix is invalid")
        if not 0 <= self.backup_hour <= 23 or not 0 <= self.scan_hour <= 23:
            raise LaunchdRenderRefused("launchd calendar hour is invalid")
        if self.health_interval_seconds < 60:
            raise LaunchdRenderRefused("health interval must be at least 60 seconds")
        if not 1 <= self.api_port <= 65535:
            raise LaunchdRenderRefused("API port is invalid")
        _require_executable(self.docker_executable, name="Docker")
        _require_executable(self.uv_executable, name="uv")


def render_launchd_templates(
    configuration: LaunchdRenderConfiguration,
) -> dict[str, str]:
    """Render each tracked template from explicit, non-secret inputs only."""

    checkout = _require_plain_checkout(configuration.checkout)
    backup_root = require_safe_backup_root(configuration.backup_root)
    state = checkout / ".context-engine"
    for environment_path in (
        state / "database.env",
        state / "operators.env",
    ):
        try:
            load_owner_environment(environment_path)
        except EnvironmentRefused as error:
            raise LaunchdRenderRefused(str(error)) from None
    values = {
        "checkout": escape(str(checkout)),
        "python": escape(str(checkout / ".venv" / "bin" / "python")),
        "backup_root": escape(str(backup_root)),
        "docker_executable": escape(
            str(configuration.docker_executable.resolve(strict=True))
        ),
        "uv_executable": escape(str(configuration.uv_executable.resolve(strict=True))),
        "database_environment": escape(str(state / "database.env")),
        "operator_environment": escape(str(state / "operators.env")),
        "log_root": escape(str(state / "logs")),
        "failure_root": escape(str(state / "scheduled-failures")),
        "label_prefix": escape(configuration.label_prefix),
        "backup_hour": str(configuration.backup_hour),
        "scan_hour": str(configuration.scan_hour),
        "health_interval_seconds": str(configuration.health_interval_seconds),
        "health_url": escape(f"http://127.0.0.1:{configuration.api_port}/health"),
        "api_port": str(configuration.api_port),
    }
    rendered: dict[str, str] = {}
    templates = sorted(
        (checkout / "deploy" / "daily-driver").glob("*.plist.template")
    )
    if not templates:
        raise LaunchdRenderRefused("tracked launchd templates are unavailable")
    for path in templates:
        try:
            content = Template(path.read_text(encoding="utf-8")).substitute(values)
            parsed = plistlib.loads(content.encode("utf-8"))
        except (KeyError, OSError, plistlib.InvalidFileException, UnicodeError):
            raise LaunchdRenderRefused("tracked launchd template is invalid") from None
        label = parsed.get("Label")
        if not isinstance(label, str) or not label.startswith(
            f"{configuration.label_prefix}."
        ):
            raise LaunchdRenderRefused("rendered launchd label is invalid")
        rendered[f"{label}.plist"] = content
    return rendered


def write_rendered_templates(
    configuration: LaunchdRenderConfiguration,
    destination: Path,
) -> tuple[Path, ...]:
    """Idempotently publish owner-only rendered plists to ignored state."""

    checkout = _require_plain_checkout(configuration.checkout)
    expected_destination = checkout / ".context-engine" / "launchd"
    if destination.resolve(strict=False) != expected_destination.resolve(strict=False):
        raise LaunchdRenderRefused("rendered templates must stay in ignored state")
    for path in (expected_destination.parent, expected_destination):
        if path.is_symlink():
            raise LaunchdRenderRefused("rendered template state may not be a symlink")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination, 0o700)
    published: list[Path] = []
    rendered = render_launchd_templates(configuration)
    manifest = destination / _RENDER_MANIFEST
    previously_owned = _read_render_manifest(
        manifest,
        label_prefix=configuration.label_prefix,
    )
    discovered = {path.name for path in destination.glob("*.plist")}
    if previously_owned is None and discovered:
        raise LaunchdRenderRefused("unowned rendered template state is present")
    owned = frozenset() if previously_owned is None else previously_owned
    if not discovered <= owned:
        raise LaunchdRenderRefused("unowned rendered template state is present")
    _write_render_manifest(
        manifest,
        label_prefix=configuration.label_prefix,
        plists=owned | frozenset(rendered),
    )
    for stale_name in sorted(owned - rendered.keys()):
        stale = destination / stale_name
        if stale.is_symlink() or not stale.is_file():
            raise LaunchdRenderRefused("stale rendered template is unsafe")
        stale.unlink()
    for name, content in rendered.items():
        target = destination / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise LaunchdRenderRefused("rendered template target is unsafe")
        if target.exists() and target.read_text(encoding="utf-8") == content:
            os.chmod(target, 0o600)
            published.append(target)
            continue
        descriptor, temporary_name = tempfile.mkstemp(dir=destination, prefix=".plist-")
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                descriptor = -1
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            published.append(target)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
    _write_render_manifest(
        manifest,
        label_prefix=configuration.label_prefix,
        plists=frozenset(rendered),
    )
    return tuple(sorted(published))


def _read_render_manifest(
    path: Path,
    *,
    label_prefix: str,
) -> frozenset[str] | None:
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o777 != 0o600:
        raise LaunchdRenderRefused("render manifest is unsafe")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise LaunchdRenderRefused("render manifest is invalid") from None
    if (
        type(document) is not dict
        or set(document) != {"labelPrefix", "plists", "schemaVersion"}
        or document["schemaVersion"] != 1
        or document["labelPrefix"] != label_prefix
        or type(document["plists"]) is not list
        or not document["plists"]
        or any(
            type(name) is not str
            or Path(name).name != name
            or not name.endswith(".plist")
            for name in document["plists"]
        )
        or len(set(document["plists"])) != len(document["plists"])
    ):
        raise LaunchdRenderRefused(
            "launchd label prefix is immutable; uninstall before replacing it"
        )
    return frozenset(document["plists"])


def _write_render_manifest(
    path: Path,
    *,
    label_prefix: str,
    plists: frozenset[str],
) -> None:
    document = {
        "labelPrefix": label_prefix,
        "plists": sorted(plists),
        "schemaVersion": 1,
    }
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=".manifest-")
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            json.dump(document, output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _require_plain_checkout(checkout: Path) -> Path:
    if not checkout.is_absolute() or checkout.is_symlink():
        raise LaunchdRenderRefused("dedicated checkout is invalid")
    resolved = checkout.resolve(strict=True)
    if not (resolved / ".git").is_dir():
        raise LaunchdRenderRefused("dedicated checkout must be a plain checkout")
    try:
        require_durable_storage_root(resolved.parent)
    except ValueError as error:
        raise LaunchdRenderRefused(str(error)) from None
    return resolved


def _require_executable(path: Path, *, name: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise LaunchdRenderRefused(f"{name} executable is invalid") from None
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or not resolved.is_file()
        or not os.access(resolved, os.X_OK)
    ):
        raise LaunchdRenderRefused(f"{name} executable is invalid")
    return resolved
