from __future__ import annotations

import ast
from pathlib import Path

from engine.learning.contracts import CurationMode, CurationProfileRef

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_MODULES = (
    REPOSITORY_ROOT / "applications/eval_v1.py",
    REPOSITORY_ROOT / "engine/learning/feedback.py",
    REPOSITORY_ROOT / "engine/learning/curation_candidate.py",
    REPOSITORY_ROOT / "engine/learning/golden_intake.py",
    REPOSITORY_ROOT / "engine/learning/comparison.py",
    REPOSITORY_ROOT / "engine/persistence/feedback.py",
)

FORBIDDEN_AUTHORITY_SYMBOLS = frozenset(
    {
        "ContextLearning",
        "PromotionAuthorizationRequest",
        "PromotionReceipt",
        "ReleaseManifest",
        "ReleaseOperatorAuthority",
        "TrustedPromotionCall",
        "VerifiedReleaseOperatorIdentity",
        "promote",
        "promote_atomically",
        "release_operator_grant",
        "rollback",
    }
)
FORBIDDEN_EFFECT_CALLS = frozenset(
    {
        "activate",
        "authorize",
        "execute",
        "grant",
        "promote",
        "promote_atomically",
        "rollback",
    }
)


def _imported_symbols(tree: ast.AST) -> set[str]:
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            symbols.update(alias.name for alias in node.names)
    return symbols


def _called_attributes(tree: ast.AST) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def test_curation_workflow_has_a_closed_non_publication_authority_surface() -> None:
    for path in WORKFLOW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert not (
            FORBIDDEN_AUTHORITY_SYMBOLS
            & (_imported_symbols(tree) | names | attributes)
        ), path.name

    effects = _called_attributes(
        ast.parse(
            (REPOSITORY_ROOT / "engine/learning/golden_intake.py").read_text(
                encoding="utf-8"
            )
        )
    )
    assert not (effects & FORBIDDEN_EFFECT_CALLS)
    assert effects & {"read_text", "replace", "write_text"}


def test_curation_workflow_does_not_export_release_authority() -> None:
    import engine.learning.comparison as comparison
    import engine.learning.curation_candidate as candidate
    import engine.learning.feedback as feedback
    import engine.learning.golden_intake as intake

    for module in (comparison, candidate, feedback, intake):
        assert not any(hasattr(module, name) for name in FORBIDDEN_AUTHORITY_SYMBOLS)


def test_all_current_release_manifests_still_require_curation_off() -> None:
    profile = CurationProfileRef.off(
        profile_ref="curation-off-v0",
        profile_digest="0" * 64,
    )

    assert profile.mode is CurationMode.OFF
    assert profile.curation_snapshot_ref is None

    release_composition = (
        REPOSITORY_ROOT / "applications/release_promotion.py"
    ).read_text(encoding="utf-8")
    assert "CurationProfileRef.off(" in release_composition
    assert "CurationProfileRef.on(" not in release_composition


def test_feedback_workflow_is_not_scheduled() -> None:
    schedule = (
        REPOSITORY_ROOT / "deploy/daily-driver/scheduled-jobs.json"
    ).read_text(encoding="utf-8")

    assert "feedback-candidate" not in schedule
    assert "feedback-intake" not in schedule
    assert "compare-releases" not in schedule
