from __future__ import annotations

import ast
import inspect
from pathlib import Path
from uuid import UUID

import pytest

from engine.learning import (
    ContentProfileRef,
    ContextLearning,
    CurationMode,
    CurationProfileRef,
    IndexProfileRef,
    ReleaseManifest,
    RuntimeProfileRef,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ENGINE_ROOT = REPOSITORY_ROOT / "engine"
MIGRATIONS_ROOT = REPOSITORY_ROOT / "migrations" / "versions"
BOOTSTRAP_ROOTS = (
    REPOSITORY_ROOT / "applications",
    REPOSITORY_ROOT / "infra",
    REPOSITORY_ROOT / "scripts",
)


def _python_sources(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.rglob("*.py")))


def _defined_method_owners(
    source_path: Path,
    method_names: frozenset[str],
) -> tuple[tuple[str, str, Path], ...]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=source_path)
    owners: list[tuple[str, str, Path]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for member in node.body:
            if (
                isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
                and member.name in method_names
            ):
                owners.append((node.name, member.name, source_path))
    return tuple(owners)


def test_context_learning_is_the_only_public_release_owner() -> None:
    release_owner_names = frozenset({"promote", "activate", "publish", "rollback"})
    owners = tuple(
        owner
        for path in _python_sources(ENGINE_ROOT)
        for owner in _defined_method_owners(path, release_owner_names)
    )

    assert owners == (
        ("ContextLearning", "promote", ENGINE_ROOT / "learning" / "module.py"),
    )
    assert {
        name
        for name, member in inspect.getmembers(
            ContextLearning,
            predicate=inspect.isfunction,
        )
        if not name.startswith("_")
    } == {"evaluate", "promote"}


def test_migration_and_bootstrap_sources_have_no_pointer_seed_or_promote_call() -> None:
    migration_sources = _python_sources(MIGRATIONS_ROOT)
    bootstrap_sources = tuple(
        path for root in BOOTSTRAP_ROOTS for path in _python_sources(root)
    )

    for path in migration_sources:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path)
        assert "seed_release_manifest" not in source.casefold()
        assert "bootstrap_release_manifest" not in source.casefold()
        for call in (
            node for node in ast.walk(tree) if isinstance(node, ast.Call)
        ):
            if not call.args or not isinstance(call.args[0], ast.Constant):
                continue
            argument = call.args[0].value
            if not isinstance(argument, str):
                continue
            normalized = " ".join(argument.casefold().split())
            assert not (
                normalized.startswith("select")
                and "context_learning_promote_release(" in normalized
            )

    forbidden_bootstrap_fragments = (
        "context_learning_promote_release(",
        "insert into public.active_release_manifest",
        "insert into active_release_manifest",
    )
    for path in bootstrap_sources:
        source = path.read_text(encoding="utf-8").casefold()
        assert all(
            fragment not in source for fragment in forbidden_bootstrap_fragments
        ), path


@pytest.mark.parametrize(
    ("snapshot_ref", "revision_refs", "evaluation_digest", "message"),
    [
        (None, ("revision-a",), "a" * 64, "CurationSnapshotRef"),
        ("snapshot-a", (), "a" * 64, "nonempty tuple"),
        ("snapshot-a", ("revision-a",), None, "evaluation digest"),
    ],
)
def test_curation_on_requires_snapshot_compatible_revision_set_and_evaluation_digest(
    snapshot_ref: str | None,
    revision_refs: tuple[str, ...],
    evaluation_digest: str | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        CurationProfileRef(
            profile_ref="curation-on-v1",
            profile_digest="4" * 64,
            mode=CurationMode.ON,
            curation_snapshot_ref=snapshot_ref,
            compatible_revision_refs=revision_refs,
            evaluation_digest=evaluation_digest,
        )


def test_curation_has_no_direct_activation_entry() -> None:
    public_curation_functions = {
        name
        for name, member in inspect.getmembers(CurationProfileRef)
        if not name.startswith("_")
        and callable(member)
    }

    assert public_curation_functions == {"off", "on"}
    assert not public_curation_functions.intersection(
        {"activate", "promote", "publish", "rollback"}
    )


def test_promote_rejects_curation_profile_with_incompatible_revision_references() -> None:  # noqa: E501
    content = ContentProfileRef(
        profile_ref="content-curation-on-v1",
        profile_digest="1" * 64,
        content_schema_ref="context-content-schema-v1",
    )
    index = IndexProfileRef(
        profile_ref="index-curation-on-v1",
        profile_digest="2" * 64,
        content_profile_digest=content.profile_digest,
        content_schema_ref=content.content_schema_ref,
        index_schema_ref="context-index-schema-v1",
    )
    runtime = RuntimeProfileRef(
        profile_ref="runtime-curation-on-v1",
        profile_digest="3" * 64,
        content_profile_digest=content.profile_digest,
        index_profile_digest=index.profile_digest,
        content_schema_ref=content.content_schema_ref,
        index_schema_ref=index.index_schema_ref,
        tokenizer_ref="empty-tokenizer-v1",
        package_schema_ref="context-package-v1",
    )
    curation = CurationProfileRef.on(
        profile_ref="curation-on-v1",
        profile_digest="4" * 64,
        curation_snapshot_ref="curation-snapshot-v1",
        compatible_revision_refs=("revision-a",),
        evaluation_digest="5" * 64,
    )

    with pytest.raises(ValueError, match="exactly match"):
        ReleaseManifest(
            organization_id=UUID("33c7b365-c705-45af-b676-067fd510f683"),
            manifest_ref="manifest-curation-on-v1",
            content_profile=content,
            index_profile=index,
            runtime_profile=runtime,
            curation_profile=curation,
            active_revision_refs=("revision-b",),
        )
