from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
MIGRATIONS = ROOT / "migrations" / "versions"
ENGINE_CODE = ROOT / "engine"
ACL_NAME = re.compile(
    r"(?:^|_)(?:acl|access_policy|permission|visibility|reader|writer|principal|"
    r"subject|allow|deny|grant|entitlement|role)(?:s)?(?:$|_)",
    re.IGNORECASE,
)
FRAGMENT_PERMISSION_IDENTIFIER = re.compile(
    r"\b(?:[a-z0-9_]*fragment[a-z0-9_]*(?:acl|access_policy|permission|visibility|"
    r"reader|writer|principal|subject|allow|deny|grant|entitlement|role)"
    r"|[a-z0-9_]*(?:acl|access_policy|permission|visibility|reader|writer|principal|"
    r"subject|allow|deny|grant|entitlement|role)[a-z0-9_]*fragment)"
    r"[a-z0-9_]*\b",
    re.IGNORECASE,
)
FRAGMENT_PERMISSION_DDL = re.compile(
    r"\b(?:alter|create)\s+(?:table|type)\s+[a-z0-9_.]*fragment[a-z0-9_]*"
    r"[\s\S]{0,512}\b(?:add\s+column\s+)?"
    r"(?:acl|access_policy|permission|visibility|readers?|writers?|principals?|"
    r"subjects?|allowed|denied|grants?|entitlements?|roles?)\b",
    re.IGNORECASE,
)
FRAGMENT_PERMISSION_FUNCTION = re.compile(
    r"\bcreate\s+(?:or\s+replace\s+)?function\b[\s\S]{0,1024}"
    r"\bfragment_ref\b[\s\S]{0,512}"
    r"\b(?:acl|access_policy|permission|visibility|readers?|writers?|principals?|"
    r"subjects?|allowed|denied|grants?|entitlements?|roles?)\b"
    r"[\s\S]{0,256}\breturns\b",
    re.IGNORECASE,
)


def _string_constants(tree: ast.AST) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign | ast.AnnAssign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    return constants


def _literal_string(node: ast.expr, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _migration_fragment_schema() -> tuple[set[str], set[str], set[str]]:
    tables: set[str] = set()
    columns: set[str] = set()
    sql_violations: set[str] = set()
    for migration in sorted(MIGRATIONS.glob("*.py")):
        source = migration.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(migration))
        constants = _string_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            literal = node.value
            if any(
                pattern.search(literal)
                for pattern in (
                    FRAGMENT_PERMISSION_IDENTIFIER,
                    FRAGMENT_PERMISSION_DDL,
                    FRAGMENT_PERMISSION_FUNCTION,
                )
            ):
                sql_violations.add(f"{migration.relative_to(ROOT)}:{node.lineno}")
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not isinstance(
                call.func, ast.Attribute
            ):
                continue
            if call.func.attr not in {"create_table", "add_column"} or not call.args:
                continue
            table_name = _literal_string(call.args[0], constants)
            if call.func.attr == "create_table" and table_name is not None:
                tables.add(table_name)
            column_nodes = (
                call.args[1:] if call.func.attr == "create_table" else call.args[1:2]
            )
            for node in column_nodes:
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "Column"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    column_name = node.args[0].value
                    if table_name is not None and (
                        "fragment" in table_name.lower()
                        or column_name == "fragment_ref"
                    ):
                        columns.add(column_name)
    return tables, columns, sql_violations


def _fragment_scoped_permission_fields() -> set[str]:
    violations: set[str] = set()
    for source_path in sorted(ENGINE_CODE.rglob("*.py")):
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"), filename=str(source_path)
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in ast.walk(node):
                field_names: list[str] = []
                if isinstance(statement, ast.AnnAssign) and isinstance(
                    statement.target, ast.Name
                ):
                    field_names.append(statement.target.id)
                elif isinstance(statement, ast.arg):
                    field_names.append(statement.arg)
                for field_name in field_names:
                    fragment_scoped = "fragment" in node.name.lower()
                    permission_named = ACL_NAME.search(field_name) is not None
                    article_decision_provenance = field_name in {
                        "source_acl_as_of",
                        "source_acl_projection_ref",
                    }
                    fragment_permission_named = (
                        "fragment" in field_name.lower() and permission_named
                    )
                    if (
                        fragment_scoped
                        and permission_named
                        and not article_decision_provenance
                    ) or fragment_permission_named:
                        violations.add(
                            f"{source_path.relative_to(ROOT)}:{node.name}.{field_name}"
                        )
    return violations


def test_no_fragment_scoped_permission_field_exists_in_schema_or_runtime_code() -> None:
    tables, columns, sql_violations = _migration_fragment_schema()
    table_violations = sorted(
        table_name
        for table_name in tables
        if "fragment" in table_name.lower() and ACL_NAME.search(table_name)
    )
    schema_violations = sorted(column for column in columns if ACL_NAME.search(column))

    assert table_violations == []
    assert schema_violations == []
    assert sql_violations == set()
    assert _fragment_scoped_permission_fields() == set()


def test_fragment_acl_scanner_detects_representative_schema_mutations() -> None:
    mutations = (
        "ALTER TABLE context_fragment ADD COLUMN readers text[]",
        "CREATE TABLE fragment_principal_grant (fragment_ref text, subject text)",
        "CREATE TYPE fragment_visibility AS ENUM ('allowed', 'denied')",
    )
    for mutation in mutations:
        assert any(
            pattern.search(mutation)
            for pattern in (
                FRAGMENT_PERMISSION_IDENTIFIER,
                FRAGMENT_PERMISSION_DDL,
                FRAGMENT_PERMISSION_FUNCTION,
            )
        ), mutation


def test_fragment_acl_scanner_detects_representative_runtime_fields() -> None:
    assert ACL_NAME.search("readers") is not None
    assert ACL_NAME.search("access_policy") is not None
    assert ACL_NAME.search("principal_grants") is not None
