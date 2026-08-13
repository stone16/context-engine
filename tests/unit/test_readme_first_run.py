from __future__ import annotations

import re
import shlex
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
README = ROOT / "README.md"
README_ZH_CN = ROOT / "README.zh-CN.md"
FIRST_RUN_ENVIRONMENT_NAMES = frozenset(
    {
        "CONTEXT_ENGINE_FILE_ROOT_REF",
        "CONTEXT_ENGINE_FILE_SOURCE_DISPLAY_NAME",
        "CONTEXT_ENGINE_FILE_SOURCE_IDEMPOTENCY_KEY",
        "CONTEXT_ENGINE_FILE_SOURCE_REF",
        "CONTEXT_ENGINE_RELEASE_EVIDENCE_FILE",
    }
)


def _first_run_section() -> str:
    return _readme_section(README, "First run: File corpus to public query")


def _readme_section(path: Path, heading: str) -> str:
    readme = path.read_text(encoding="utf-8")
    match = re.search(
        rf"^### {re.escape(heading)}\n(?P<body>.*?)(?=^### |^## )",
        readme,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"{path.name} must expose {heading!r}"
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
    commands = _logical_commands(shell)
    ordered_commands = (
        ("make", "install"),
        ("make", "db-up"),
        ("source", ".context-engine/database.env"),
        ("source", ".context-engine/operators.env"),
        ("uv", "run", "context-engine-control", "migrate"),
        ("uv", "run", "context-engine-model-materializer"),
        ("uv", "run", "context-engine-dogfood-seed"),
        ("uv", "run", "context-engine-control", "register-file-source"),
        ("uv", "run", "context-engine-control", "activate-change-feed"),
        ("uv", "run", "context-engine-control", "activate-delete-observations"),
        ("uv", "run", "context-engine-control", "scan"),
        ("uv", "run", "context-engine-worker", "--dispatch-file-once"),
        ("uv", "run", "context-engine-control", "promote-release"),
        ("uv", "run", "context-engine-api", "--host", "127.0.0.1"),
        ("uv", "run", "context-engine-context", "query"),
    )
    positions: list[int] = []
    for expected in ordered_commands:
        matches = [
            index
            for index, command in enumerate(commands)
            if command[: len(expected)] == expected
        ]
        assert matches, expected
        positions.append(matches[0])
    assert positions == sorted(positions), ordered_commands

    preflight = ("uv", "run", "context-engine-control", "preflight")
    preflight_positions = [
        index
        for index, command in enumerate(commands)
        if command[: len(preflight)] == preflight
    ]
    migration_position = next(
        index
        for index, command in enumerate(commands)
        if command[:4] == ("uv", "run", "context-engine-control", "migrate")
    )
    worker_position = next(
        index
        for index, command in enumerate(commands)
        if command[:3] == ("uv", "run", "context-engine-worker")
    )
    api_position = next(
        index
        for index, command in enumerate(commands)
        if command[:3] == ("uv", "run", "context-engine-api")
    )
    promotion_position = next(
        index
        for index, command in enumerate(commands)
        if command[:4] == ("uv", "run", "context-engine-control", "promote-release")
    )
    materializer_position = next(
        index
        for index, command in enumerate(commands)
        if command[:3] == ("uv", "run", "context-engine-model-materializer")
    )
    seed_position = next(
        index
        for index, command in enumerate(commands)
        if command[:3] == ("uv", "run", "context-engine-dogfood-seed")
    )
    assert len(preflight_positions) == 4
    assert preflight_positions[0] < migration_position
    assert commands[preflight_positions[1]][4:] == ("--plane", "migration")
    assert migration_position < preflight_positions[1] < materializer_position
    assert commands[preflight_positions[2]][4:] == (
        "--plane",
        "control",
        "--plane",
        "supply",
        "--plane",
        "release",
    )
    assert materializer_position < preflight_positions[2] < seed_position
    assert seed_position < worker_position < promotion_position
    assert promotion_position < preflight_positions[3] < api_position

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    make_targets = set(re.findall(r"^([a-zA-Z0-9_.-]+):", makefile, re.MULTILINE))
    documented_make_targets = {
        command[1] for command in commands if command[0] == "make"
    }
    assert documented_make_targets <= make_targets

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    console_scripts = set(project["project"]["scripts"])
    documented_scripts = {
        command[2] for command in commands if command[:2] == ("uv", "run")
    }
    assert documented_scripts <= console_scripts

    assert "reviewed four-gate release evidence" in section
    assert "does not supply Reliability, Quality, or Budget PASS" in section
    assert "loopback-only" in section
    assert re.search(r"separate\s+credential planes", section)

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


def _comment_out_migration_with_exact_decoy(section: str) -> str:
    migration = "uv run context-engine-control migrate"
    return section.replace(migration, f"# {migration}", 1)


def _comment_out_public_query_with_exact_decoy(section: str) -> str:
    query = (
        "uv run context-engine-context query \\\n"
        '  "Which current evidence governs this question?"'
    )
    return section.replace(
        query,
        "# uv run context-engine-context query\n"
        '# "Which current evidence governs this question?"',
        1,
    )


def _move_api_before_promotion_with_commented_decoy(section: str) -> str:
    api = "uv run context-engine-api --host 127.0.0.1"
    promotion = "uv run context-engine-control promote-release"
    without_original_api = section.replace(api, "# API moved above promotion", 1)
    return without_original_api.replace(
        promotion,
        f"# {promotion}\n{api}\n{promotion}",
        1,
    )


def _move_readiness_gate_after_seed(section: str) -> str:
    gate = (
        "uv run context-engine-control preflight \\\n"
        "  --plane control \\\n"
        "  --plane supply \\\n"
        "  --plane release"
    )
    seed = "uv run context-engine-dogfood-seed"
    without_gate = section.replace(gate, "# readiness gate moved", 1)
    return without_gate.replace(seed, f"{seed}\n{gate}", 1)


def test_readme_first_run_is_executable_and_ordered() -> None:
    _validate_first_run(_first_run_section())


def test_first_run_environment_template_covers_journey_specific_names() -> None:
    shell = _first_run_shell(_first_run_section())
    used_names = frozenset(re.findall(r"\$\{?(CONTEXT_ENGINE_[A-Z0-9_]+)", shell))
    template_rows = (
        (ROOT / "deploy/local-preflight.env.example")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert used_names >= FIRST_RUN_ENVIRONMENT_NAMES
    assert (
        frozenset(row.removesuffix("=") for row in template_rows if row.endswith("="))
        >= FIRST_RUN_ENVIRONMENT_NAMES
    )
    assert all("=" in row and not row.partition("=")[2] for row in template_rows)


@pytest.mark.parametrize(
    "mutate",
    (
        _comment_out_migration_with_exact_decoy,
        _move_api_before_promotion_with_commented_decoy,
        _move_readiness_gate_after_seed,
        lambda section: section.replace(
            "uv run context-engine-control migrate",
            "uv run context-engine-unknown migrate",
            1,
        ),
        _comment_out_public_query_with_exact_decoy,
    ),
    ids=(
        "missing-migration",
        "api-before-promotion",
        "readiness-after-seed",
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


def test_chinese_readme_tracks_the_bounded_first_run_and_active_surfaces() -> None:
    english_section = _first_run_section()
    chinese_section = _readme_section(
        README_ZH_CN,
        "首次运行：从 File corpus 到公开查询",
    )
    assert _logical_commands(_first_run_shell(chinese_section)) == _logical_commands(
        _first_run_shell(english_section)
    )
    assert (
        "经审阅的 Security、Reliability、Quality、Budget 四门发布证据"
        in chinese_section
    )
    assert "M0 Security gate" in chinese_section
    assert "不会提供 Reliability、Quality 或 Budget PASS" in chinese_section
    assert "loopback-only" in chinese_section
    assert "`NOT_ACTIVE`" in chinese_section

    readme = README_ZH_CN.read_text(encoding="utf-8")
    for workspace in (
        "action_plane/typescript",
        "bot_delivery/typescript",
        "sdk/typescript",
        "sdk/typescript-v1",
    ):
        assert f"`{workspace}/`" in readme
    assert "四个 TypeScript 工作区" in readme
    assert "只使用 pgvector 候选发现" in readme
    assert re.search(
        r"Hybrid retrieval 已实现，但未在该 carrier\s+中激活",
        readme,
    )
    assert (
        "一个 maintainer-local、spawn-per-session 的 stdio MCP `Acquire` translator"
        in readme
    )
    assert (
        "connector、HTTP server ingress、ADR-0103 的本地 MCP translator；"
        "generated SDK 属于 client 产物"
        in readme
    )
    assert "所有 broader MCP carrier" in readme
    assert "Issue #217 已完成累计 public accounting contract" in readme
    assert "不再是 blocker" in readme
    assert "External/network worker embeddings 仍为 `NOT_ACTIVE`" in readme
    assert re.search(r"没有 accepted activation\s+decision", readme)
    assert "Evidence Console 内的 private File citation reopening" in readme

    documentation_links = {
        "Daily-driver 部署": "docs/operations/daily-driver-deployment.md",
        "Evidence Console": (
            "docs/decisions/0090-admit-a-co-resident-local-evidence-console.md"
        ),
        "本地 MCP": (
            "docs/decisions/0103-activate-one-local-mcp-acquire-translation.md"
        ),
        "端到端 walkthrough": (
            "docs/design/2026-07-28-end2end-dogfood-walkthrough.md"
        ),
    }
    for label, relative_path in documentation_links.items():
        assert f"[{label}](./{relative_path})" in readme
