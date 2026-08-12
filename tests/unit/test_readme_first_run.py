from __future__ import annotations

import re
import shlex
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
README = ROOT / "README.md"


def _first_run_section() -> str:
    readme = README.read_text(encoding="utf-8")
    match = re.search(
        r"^### First run: File corpus to public query\n(?P<body>.*?)(?=^### |^## )",
        readme,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "README must expose one ordered first-run journey"
    return match.group("body")


def _first_run_shell(section: str) -> str:
    match = re.search(r"```bash\n(?P<body>.*?)```", section, re.DOTALL)
    assert match is not None, "first-run journey must be one copyable shell block"
    return match.group("body")


def _logical_commands(shell: str) -> tuple[tuple[str, ...], ...]:
    commands: list[tuple[str, ...]] = []
    logical_line = ""
    for raw_line in shell.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        logical_line = f"{logical_line} {line}".strip()
        if logical_line.endswith("\\"):
            logical_line = logical_line[:-1].rstrip()
            continue
        commands.append(tuple(shlex.split(logical_line, comments=True)))
        logical_line = ""
    assert not logical_line
    return tuple(commands)


def _validate_first_run(section: str) -> None:
    shell = _first_run_shell(section)
    ordered_commands = (
        "make install",
        "make db-up",
        "source .context-engine/database.env",
        "source .context-engine/operators.env",
        "uv run context-engine-control migrate",
        "uv run context-engine-dogfood-seed",
        "uv run context-engine-control register-file-source",
        "uv run context-engine-control activate-change-feed",
        "uv run context-engine-control activate-delete-observations",
        "uv run context-engine-control scan",
        "uv run context-engine-worker --dispatch-file-once",
        "uv run context-engine-control promote-release",
        "uv run context-engine-api --host 127.0.0.1",
        "uv run context-engine-context query",
    )
    positions = [shell.find(command) for command in ordered_commands]
    assert all(position >= 0 for position in positions), ordered_commands
    assert positions == sorted(positions), ordered_commands

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    make_targets = set(re.findall(r"^([a-zA-Z0-9_.-]+):", makefile, re.MULTILINE))
    documented_make_targets = set(re.findall(r"(?m)^make ([a-zA-Z0-9_.-]+)", shell))
    assert documented_make_targets <= make_targets

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    console_scripts = set(project["project"]["scripts"])
    documented_scripts = set(
        re.findall(r"uv run (context-engine-[a-z0-9-]+)", shell)
    )
    assert documented_scripts <= console_scripts

    assert "reviewed four-gate release evidence" in section
    assert "does not supply Reliability, Quality, or Budget PASS" in section
    assert "loopback-only" in section
    assert re.search(r"separate\s+credential planes", section)

    commands = _logical_commands(shell)
    allowed_shell_builtins = {"export", "set", "source"}
    for command in commands:
        if command[:2] == ("uv", "run"):
            assert command[2] in console_scripts
        elif command[0] == "make":
            assert command[1] in make_targets
        else:
            assert command[0] in allowed_shell_builtins

    seed = next(
        command for command in commands if "context-engine-dogfood-seed" in command
    )
    assert "--provision-release-operator-grant" in seed
    assert "--file-import-service-principal-id" in seed

    api = next(command for command in commands if "context-engine-api" in command)
    assert api[api.index("--host") + 1] == "127.0.0.1"
    assert "uvicorn" not in shell
    assert "alembic" not in shell
    assert "--test-mode" not in shell
    assert "twin" not in shell


def _move_api_before_promotion(section: str) -> str:
    api = "uv run context-engine-api --host 127.0.0.1"
    promotion = "uv run context-engine-control promote-release"
    return section.replace(promotion, "FIRST_RUN_API_PLACEHOLDER", 1).replace(
        api,
        promotion,
        1,
    ).replace("FIRST_RUN_API_PLACEHOLDER", api, 1)


def test_readme_first_run_is_executable_and_ordered() -> None:
    _validate_first_run(_first_run_section())


@pytest.mark.parametrize(
    "mutate",
    (
        lambda section: section.replace(
            "uv run context-engine-control migrate",
            "# migration removed",
            1,
        ),
        _move_api_before_promotion,
        lambda section: section.replace(
            "uv run context-engine-control migrate",
            "uv run context-engine-unknown migrate",
            1,
        ),
        lambda section: section.replace(
            "uv run context-engine-context query",
            "# public query removed",
            1,
        ),
    ),
    ids=(
        "missing-migration",
        "api-before-promotion",
        "unknown-script",
        "missing-query",
    ),
)
def test_readme_first_run_contract_rejects_unsafe_drift(
    mutate: Callable[[str], str],
) -> None:
    section = _first_run_section()
    with pytest.raises(AssertionError):
        _validate_first_run(mutate(section))


def test_readme_adjacent_authority_matches_active_repository_surfaces() -> None:
    readme = README.read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    installed_workspaces = set(
        re.findall(r"npm --prefix ([^ ]+) ci --ignore-scripts", makefile)
    )
    assert installed_workspaces == {
        "action_plane/typescript",
        "bot_delivery/typescript",
        "sdk/typescript",
        "sdk/typescript-v1",
    }
    assert all(f"`{workspace}/`" in readme for workspace in installed_workspaces)
    assert "four TypeScript workspaces" in readme
    assert "Issue #217 completed" in readme
    assert "no longer a blocker" in readme
    assert "uses pgvector candidate discovery only" in readme
    assert re.search(
        r"Hybrid\s+retrieval is implemented but is not active",
        readme,
    )

    documentation_links = {
        "Daily-driver deployment": "docs/operations/daily-driver-deployment.md",
        "Evidence Console": (
            "docs/decisions/0090-admit-a-co-resident-local-evidence-console.md"
        ),
        "Local MCP": (
            "docs/decisions/0103-activate-one-local-mcp-acquire-translation.md"
        ),
        "End-to-end walkthrough": (
            "docs/design/2026-07-28-end2end-dogfood-walkthrough.md"
        ),
    }
    for label, relative_path in documentation_links.items():
        assert f"[{label}](./{relative_path})" in readme
        assert (ROOT / relative_path).is_file()
