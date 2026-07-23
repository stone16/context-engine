"""Deterministic live PostgreSQL RLS inventory evidence for the M0 gate."""

from __future__ import annotations

import json
import re
from collections import Counter, deque
from collections.abc import Collection, Mapping, Sequence
from hashlib import sha256
from typing import cast

from sqlalchemy import Connection, text

PUBLIC_SCHEMA = "public"
GLOBAL_CLASSIFICATION = "global"
TENANT_CLASSIFICATION = "tenant_owned"
PINNED_GLOBAL_TABLES = frozenset({"alembic_version", "organization", "user_account"})
NON_OWNER_EVIDENCE_BY_TABLE: Mapping[str, str] = {
    "context_source": "PG-FILE-SOURCE-RLS-021",
    "membership": "PG-SCOPE-INTERSECTION-004",
    "organization_record": "PG-TENANT-FK-002",
    "context_resource": "PG-INDEX-NOT-AUTHORITY-005",
    "context_revision": "PG-INDEX-NOT-AUTHORITY-005",
    "context_fragment": "PG-INDEX-NOT-AUTHORITY-005",
    "exact_phrase_candidate": "PG-FILE-IMPORT-023",
    "file_acquisition": "PG-FILE-IMPORT-023",
    "file_acquisition_result": "PG-FILE-IMPORT-023",
    "file_import_job": "PG-FILE-IMPORT-023",
    "file_resource_ingestion_guard": "PG-FILE-IMPORT-023",
    "file_revision_snapshot": "PG-FILE-IMPORT-023",
    "file_revision_replacement_plan": "PG-FILE-IMPORT-023",
    "file_revision_supersession": "PG-FILE-IMPORT-023",
    "organization_policy_epoch": "PG-REVOCATION-006",
    "resource_access_policy": "PG-REVOCATION-006",
    "context_run": "PG-TRACE-REDACTION-012",
    "context_run_operator_read_ticket": "PG-TRACE-REDACTION-012",
    "decision_audit": "PG-TRACE-REDACTION-012",
    "service_principal": "PG-WORKER-LEASE-007",
    "source_version": "PG-FILE-SOURCE-RLS-021",
    "worker_noop_job": "PG-WORKER-LEASE-007",
    "context_fragment_field": "PG-FIELD-PROJECTION-RLS-048",
    "membership_resource_field_right": "PG-FIELD-PROJECTION-RLS-048",
    "release_manifest": "PG-RELEASE-OWNER-019",
    "release_candidate": "PG-RELEASE-OWNER-019",
    "release_evaluation": "PG-RELEASE-OWNER-019",
    "release_operator_grant": "PG-RELEASE-OWNER-019",
    "active_release_manifest": "PG-RELEASE-OWNER-019",
    "release_promotion_audit": "PG-RELEASE-OWNER-019",
    "revision_publication_event": "PG-FILE-IMPORT-023",
}

_SQL_TOKEN = re.compile(
    r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|::|<=|>=|<>|!=|"
    r"[A-Za-z_][A-Za-z0-9_$]*|\d+(?:\.\d+)?|[^\s]"
)


def snapshot_public_schema(connection: Connection) -> dict[str, object]:
    """Read the live public-table controls through the injected DB connection."""

    role = (
        connection.execute(
            text(
                """
            SELECT
                current_user AS name,
                role.rolsuper AS superuser,
                role.rolbypassrls AS bypasses_rls,
                role.rolinherit AS inherits
            FROM pg_roles AS role
            WHERE role.rolname = current_user
            """
            )
        )
        .mappings()
        .one()
    )
    relations = connection.execute(
        text(
            """
            SELECT
                relation.relname AS table_name,
                pg_get_userbyid(relation.relowner) AS owner,
                relation.relrowsecurity AS rls_enabled,
                relation.relforcerowsecurity AS rls_forced
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'public'
              AND relation.relkind IN ('r', 'p')
            ORDER BY relation.relname
            """
        )
    ).mappings()

    tables: dict[str, dict[str, object]] = {}
    for relation in relations:
        name = cast(str, relation["table_name"])
        tables[name] = {
            "columns": [],
            "owner": cast(str, relation["owner"]),
            "rlsEnabled": cast(bool, relation["rls_enabled"]),
            "rlsForced": cast(bool, relation["rls_forced"]),
            "policies": [],
            "foreignKeys": [],
        }

    columns = connection.execute(
        text(
            """
            SELECT
                relation.relname AS table_name,
                attribute.attname AS column_name
            FROM pg_attribute AS attribute
            JOIN pg_class AS relation
              ON relation.oid = attribute.attrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'public'
              AND relation.relkind IN ('r', 'p')
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
            ORDER BY relation.relname, attribute.attnum
            """
        )
    ).mappings()
    for column in columns:
        table = tables.get(cast(str, column["table_name"]))
        if table is not None:
            cast(list[str], table["columns"]).append(cast(str, column["column_name"]))

    policies = connection.execute(
        text(
            """
            SELECT
                policy.tablename AS table_name,
                policy.policyname AS policy_name,
                policy.permissive = 'PERMISSIVE' AS permissive,
                policy.cmd AS command,
                policy.roles,
                policy.qual AS using_expression,
                policy.with_check AS with_check_expression
            FROM pg_policies AS policy
            WHERE policy.schemaname = 'public'
            ORDER BY policy.tablename, policy.policyname
            """
        )
    ).mappings()
    for policy in policies:
        table = tables.get(cast(str, policy["table_name"]))
        if table is not None:
            cast(list[dict[str, object]], table["policies"]).append(
                {
                    "name": cast(str, policy["policy_name"]),
                    "permissive": cast(bool, policy["permissive"]),
                    "command": cast(str, policy["command"]),
                    "roles": list(cast(Sequence[str], policy["roles"])),
                    "using": cast(str | None, policy["using_expression"]),
                    "withCheck": cast(str | None, policy["with_check_expression"]),
                }
            )

    foreign_keys = connection.execute(
        text(
            """
            SELECT
                source.relname AS table_name,
                constraint_record.conname AS constraint_name,
                array_agg(
                    source_attribute.attname ORDER BY key_pair.ordinality
                ) AS columns,
                target.relname AS referenced_table,
                array_agg(
                    target_attribute.attname ORDER BY key_pair.ordinality
                ) AS referenced_columns
            FROM pg_constraint AS constraint_record
            JOIN pg_class AS source
              ON source.oid = constraint_record.conrelid
            JOIN pg_namespace AS source_namespace
              ON source_namespace.oid = source.relnamespace
            JOIN pg_class AS target
              ON target.oid = constraint_record.confrelid
            CROSS JOIN LATERAL unnest(
                constraint_record.conkey,
                constraint_record.confkey
            ) WITH ORDINALITY AS key_pair(
                source_attribute_number,
                target_attribute_number,
                ordinality
            )
            JOIN pg_attribute AS source_attribute
              ON source_attribute.attrelid = source.oid
             AND source_attribute.attnum = key_pair.source_attribute_number
            JOIN pg_attribute AS target_attribute
              ON target_attribute.attrelid = target.oid
             AND target_attribute.attnum = key_pair.target_attribute_number
            WHERE constraint_record.contype = 'f'
              AND source_namespace.nspname = 'public'
            GROUP BY
                source.relname,
                constraint_record.conname,
                target.relname
            ORDER BY source.relname, constraint_record.conname
            """
        )
    ).mappings()
    for foreign_key in foreign_keys:
        table = tables.get(cast(str, foreign_key["table_name"]))
        if table is not None:
            cast(list[dict[str, object]], table["foreignKeys"]).append(
                {
                    "name": cast(str, foreign_key["constraint_name"]),
                    "columns": list(cast(Sequence[str], foreign_key["columns"])),
                    "referencedTable": cast(str, foreign_key["referenced_table"]),
                    "referencedColumns": list(
                        cast(Sequence[str], foreign_key["referenced_columns"])
                    ),
                }
            )

    return {
        "schema": PUBLIC_SCHEMA,
        "currentRole": {
            "name": cast(str, role["name"]),
            "superuser": cast(bool, role["superuser"]),
            "bypassesRls": cast(bool, role["bypasses_rls"]),
            "inherits": cast(bool, role["inherits"]),
        },
        "tables": tables,
    }


def audit_live_rls(
    connection: Connection,
    manifest: Mapping[str, object],
    passed_evidence_ids: Collection[str],
) -> dict[str, object]:
    """Audit the live schema and return stable JSON-safe gate evidence."""

    return audit_rls_snapshot(
        manifest=manifest,
        snapshot=snapshot_public_schema(connection),
        passed_evidence_ids=passed_evidence_ids,
    )


def audit_rls_snapshot(
    *,
    manifest: Mapping[str, object],
    snapshot: Mapping[str, object],
    passed_evidence_ids: Collection[str],
) -> dict[str, object]:
    """Validate an injected catalog snapshot against the declared denominator."""

    failures: list[str] = []
    raw_entries = manifest.get("tables", [])
    manifest_entries = (
        [entry for entry in raw_entries if isinstance(entry, Mapping)]
        if isinstance(raw_entries, list)
        else []
    )
    names = [entry.get("name") for entry in manifest_entries]
    manifest_names = [name for name in names if isinstance(name, str)]
    duplicate_names = sorted(
        name for name, count in Counter(manifest_names).items() if count > 1
    )
    entries = {
        cast(str, entry["name"]): entry
        for entry in manifest_entries
        if isinstance(entry.get("name"), str)
    }

    raw_live_tables = snapshot.get("tables", {})
    live_tables = (
        cast(Mapping[str, object], raw_live_tables)
        if isinstance(raw_live_tables, Mapping)
        else {}
    )
    declared_names = set(entries)
    live_names = {name for name in live_tables if isinstance(name, str)}
    missing_from_manifest = sorted(live_names - declared_names)
    missing_from_database = sorted(declared_names - live_names)
    if duplicate_names:
        failures.append(
            "manifest has duplicate table entries: " + ", ".join(duplicate_names)
        )
    if missing_from_manifest:
        failures.append(
            "live public tables missing from manifest: "
            + ", ".join(missing_from_manifest)
        )
    if missing_from_database:
        failures.append(
            "manifest tables missing from live public schema: "
            + ", ".join(missing_from_database)
        )

    invalid_classifications = sorted(
        name
        for name, entry in entries.items()
        if entry.get("classification")
        not in {GLOBAL_CLASSIFICATION, TENANT_CLASSIFICATION}
    )
    if invalid_classifications:
        failures.append(
            "tables have invalid classifications: " + ", ".join(invalid_classifications)
        )

    global_names = sorted(
        name
        for name, entry in entries.items()
        if entry.get("classification") == GLOBAL_CLASSIFICATION
    )
    tenant_names = sorted(
        name
        for name, entry in entries.items()
        if entry.get("classification") == TENANT_CLASSIFICATION
    )
    global_name_set = set(global_names)
    unexpected_global_names = sorted(global_name_set - PINNED_GLOBAL_TABLES)
    missing_global_names = sorted(PINNED_GLOBAL_TABLES - global_name_set)
    global_inventory_exact = not unexpected_global_names and not missing_global_names
    if not global_inventory_exact:
        failures.append("global table allowlist differs from the pinned M0 allowlist")
    global_allowlist: list[dict[str, object]] = []
    for name in global_names:
        rationale = entries[name].get("classificationRationale")
        rationale_present = isinstance(rationale, str) and bool(rationale.strip())
        if not rationale_present:
            failures.append(f"{name}: global classification rationale is blank")
        global_allowlist.append(
            {
                "table": name,
                "rationale": rationale if isinstance(rationale, str) else "",
                "passed": rationale_present and name in live_names,
            }
        )

    current_role = _object_mapping(snapshot.get("currentRole"))
    session_role = _string(current_role.get("name"))
    role_is_non_superuser = current_role.get("superuser") is False
    role_cannot_bypass_rls = current_role.get("bypassesRls") is False
    passed_ids = set(passed_evidence_ids)
    ownership_edges = _verified_ownership_edges(entries, live_tables, tenant_names)

    tenant_reports: list[dict[str, object]] = []
    numerator = 0
    for name in tenant_names:
        entry = entries[name]
        live = _object_mapping(live_tables.get(name))
        organization_column = _string(entry.get("organizationColumn"))
        live_columns = _string_list(live.get("columns"))
        organization_column_present = (
            bool(organization_column) and organization_column in live_columns
        )
        ownership_path = _find_organization_path(name, ownership_edges)
        ownership_passed = organization_column_present and bool(ownership_path)

        rls_enabled = live.get("rlsEnabled") is True
        rls_forced = live.get("rlsForced") is True
        declared_rls = _object_mapping(entry.get("rowLevelSecurity"))
        declared_policy_rows = _mapping_list(declared_rls.get("policies"))
        live_policy_rows = _mapping_list(live.get("policies"))
        declared_policy_names = sorted(
            _string(policy.get("name")) for policy in declared_policy_rows
        )
        live_policy_names = sorted(
            _string(policy.get("name")) for policy in live_policy_rows
        )
        duplicate_declared_policy_names = sorted(
            policy_name
            for policy_name, count in Counter(declared_policy_names).items()
            if policy_name and count > 1
        )
        duplicate_live_policy_names = sorted(
            policy_name
            for policy_name, count in Counter(live_policy_names).items()
            if policy_name and count > 1
        )
        policy_inventory_matches = (
            bool(declared_policy_names)
            and declared_policy_names == live_policy_names
            and not duplicate_declared_policy_names
            and not duplicate_live_policy_names
        )
        expected_policy_semantics = _normalized_policy_semantics(
            name, declared_policy_rows
        )
        observed_policy_semantics = _normalized_policy_semantics(name, live_policy_rows)
        policy_semantics_match = (
            policy_inventory_matches
            and expected_policy_semantics == observed_policy_semantics
        )
        policies_passed = policy_inventory_matches and policy_semantics_match

        evidence = _object_mapping(entry.get("nonOwnerEvidence"))
        evidence_id = _string(evidence.get("evidenceId"))
        expected_evidence_id = NON_OWNER_EVIDENCE_BY_TABLE.get(name, "")
        selector = _object_mapping(evidence.get("selector"))
        selector_table = _string(selector.get("table"))
        evidence_mapping_exact = (
            bool(expected_evidence_id)
            and evidence_id == expected_evidence_id
            and selector_table == name
        )
        evidence_passed = evidence_mapping_exact and evidence_id in passed_ids
        owner = _string(live.get("owner"))
        role_is_non_owner = bool(session_role) and bool(owner) and owner != session_role
        non_owner_passed = (
            evidence_passed
            and role_is_non_owner
            and role_is_non_superuser
            and role_cannot_bypass_rls
        )

        table_failures: list[str] = []
        if not organization_column_present:
            table_failures.append(
                f"{name}: Organization ownership column is absent from the live table"
            )
        if not ownership_path:
            table_failures.append(
                f"{name}: no verified Organization-inclusive ownership path"
            )
        if not rls_enabled:
            table_failures.append(f"{name}: row level security is disabled")
        if not rls_forced:
            table_failures.append(f"{name}: FORCE ROW LEVEL SECURITY is disabled")
        if not policy_inventory_matches:
            table_failures.append(
                f"{name}: live RLS policy inventory differs from manifest"
            )
        elif not policy_semantics_match:
            table_failures.append(
                f"{name}: live RLS policy semantics differ from manifest"
            )
        if not evidence_mapping_exact:
            table_failures.append(
                f"{name}: non-owner evidence mapping differs from the pinned "
                "behavioral probe"
            )
        if not evidence_passed:
            table_failures.append(
                f"{name}: mapped non-owner PostgreSQL evidence did not pass"
            )
        if not role_is_non_owner:
            table_failures.append(
                f"{name}: audit role is missing or owns the live table"
            )
        if not role_is_non_superuser:
            table_failures.append(f"{name}: audit role is a superuser")
        if not role_cannot_bypass_rls:
            table_failures.append(f"{name}: audit role can bypass RLS")

        table_passed = not table_failures
        if table_passed:
            numerator += 1
        failures.extend(table_failures)
        tenant_reports.append(
            {
                "table": name,
                "ownershipPath": {
                    "organizationColumn": organization_column,
                    "columnPresent": organization_column_present,
                    "edges": ownership_path,
                    "passed": ownership_passed,
                },
                "rlsEnabled": rls_enabled,
                "rlsForced": rls_forced,
                "policies": {
                    "declared": declared_policy_names,
                    "live": live_policy_names,
                    "duplicateDeclared": duplicate_declared_policy_names,
                    "duplicateLive": duplicate_live_policy_names,
                    "inventoryMatchesDeclared": policy_inventory_matches,
                    "expected": expected_policy_semantics,
                    "observed": observed_policy_semantics,
                    "expectedDigest": _stable_digest(expected_policy_semantics),
                    "observedDigest": _stable_digest(observed_policy_semantics),
                    "semanticsMatchDeclared": policy_semantics_match,
                    "passed": policies_passed,
                },
                "nonOwnerIsolation": {
                    "evidenceId": evidence_id,
                    "expectedEvidenceId": expected_evidence_id,
                    "selector": {"table": selector_table},
                    "mappingExact": evidence_mapping_exact,
                    "evidencePassed": evidence_passed,
                    "sessionRole": session_role,
                    "tableOwner": owner,
                    "roleIsNonOwner": role_is_non_owner,
                    "roleIsNonSuperuser": role_is_non_superuser,
                    "roleCannotBypassRls": role_cannot_bypass_rls,
                    "passed": non_owner_passed,
                },
                "passed": table_passed,
                "failures": table_failures,
            }
        )

    denominator = len(tenant_names)
    inventory_exact = (
        not duplicate_names
        and not missing_from_manifest
        and not missing_from_database
        and not invalid_classifications
        and global_inventory_exact
    )
    coverage_percent = round((numerator / denominator) * 100, 2) if denominator else 0.0
    return {
        "passed": inventory_exact and not failures and numerator == denominator,
        "schema": _string(snapshot.get("schema")) or PUBLIC_SCHEMA,
        "inventory": {
            "declared": sorted(declared_names),
            "live": sorted(live_names),
            "missingFromManifest": missing_from_manifest,
            "missingFromDatabase": missing_from_database,
            "duplicateManifestEntries": duplicate_names,
            "exact": inventory_exact,
        },
        "denominator": {
            "allTables": len(declared_names),
            "tenantOwned": denominator,
            "global": len(global_names),
        },
        "coverage": {
            "numerator": numerator,
            "denominator": denominator,
            "percent": coverage_percent,
        },
        "globalAllowlist": global_allowlist,
        "globalAllowlistInventory": {
            "expected": sorted(PINNED_GLOBAL_TABLES),
            "declared": global_names,
            "missing": missing_global_names,
            "unexpected": unexpected_global_names,
            "exact": global_inventory_exact,
        },
        "tenantTables": tenant_reports,
        "failures": failures,
    }


def _verified_ownership_edges(
    entries: Mapping[str, Mapping[str, object]],
    live_tables: Mapping[str, object],
    tenant_names: Sequence[str],
) -> dict[str, list[dict[str, str]]]:
    edges: dict[str, list[dict[str, str]]] = {name: [] for name in tenant_names}
    for table_name in tenant_names:
        entry = entries[table_name]
        organization_column = _string(entry.get("organizationColumn"))
        live = _object_mapping(live_tables.get(table_name))
        live_foreign_keys = _mapping_list(live.get("foreignKeys"))
        live_by_name = {
            _string(foreign_key.get("name")): foreign_key
            for foreign_key in live_foreign_keys
        }
        for declared in _mapping_list(entry.get("foreignKeys")):
            name = _string(declared.get("name"))
            live_foreign_key = live_by_name.get(name)
            if live_foreign_key is None:
                continue
            references = _object_mapping(declared.get("references"))
            declared_columns = _string_list(declared.get("columns"))
            declared_referenced_table = _string(references.get("table"))
            declared_referenced_columns = _string_list(references.get("columns"))
            if (
                declared_columns != _string_list(live_foreign_key.get("columns"))
                or declared_referenced_table
                != _string(live_foreign_key.get("referencedTable"))
                or declared_referenced_columns
                != _string_list(live_foreign_key.get("referencedColumns"))
            ):
                continue
            try:
                organization_index = declared_columns.index(organization_column)
            except ValueError:
                continue
            if (
                organization_index >= len(declared_referenced_columns)
                or declared_referenced_columns[organization_index] != "organization_id"
            ):
                continue
            edges[table_name].append(
                {
                    "constraint": name,
                    "fromTable": table_name,
                    "toTable": declared_referenced_table,
                }
            )
        edges[table_name].sort(key=lambda edge: (edge["toTable"], edge["constraint"]))
    return edges


def _find_organization_path(
    start: str,
    edges: Mapping[str, Sequence[dict[str, str]]],
) -> list[dict[str, str]]:
    queue: deque[tuple[str, list[dict[str, str]]]] = deque([(start, [])])
    visited = {start}
    while queue:
        table_name, path = queue.popleft()
        for edge in edges.get(table_name, []):
            next_table = edge["toTable"]
            next_path = [*path, edge]
            if next_table == "organization":
                return next_path
            if next_table not in visited and next_table in edges:
                visited.add(next_table)
                queue.append((next_table, next_path))
    return []


def _normalized_policy_semantics(
    table_name: str,
    policies: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for policy in policies:
        using_expression = policy.get("using")
        with_check_expression = policy.get("withCheck")
        normalized.append(
            {
                "name": _string(policy.get("name")),
                "permissive": policy.get("permissive", True) is True,
                "command": _string(policy.get("command")).upper(),
                "roles": sorted(_string_list(policy.get("roles"))),
                "using": _normalize_postgresql_expression(
                    table_name,
                    using_expression if isinstance(using_expression, str) else None,
                ),
                "withCheck": _normalize_postgresql_expression(
                    table_name,
                    with_check_expression
                    if isinstance(with_check_expression, str)
                    else None,
                ),
            }
        )
    return sorted(normalized, key=lambda policy: cast(str, policy["name"]))


def _normalize_postgresql_expression(
    table_name: str,
    expression: str | None,
) -> str | None:
    """Canonicalize harmless pg_get_expr rendering differences, not semantics."""

    if expression is None:
        return None
    tokens = [
        token if token.startswith("'") else token.lower()
        for token in _SQL_TOKEN.findall(expression)
    ]
    normalized: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "public" and index + 1 < len(tokens) and tokens[index + 1] == ".":
            index += 2
            continue
        if (
            token == "pg_catalog"
            and index + 2 < len(tokens)
            and tokens[index + 1] == "."
            and tokens[index + 2] in {"decode"}
        ):
            index += 2
            continue
        if (
            token == table_name.lower()
            and index + 1 < len(tokens)
            and tokens[index + 1] == "."
        ):
            index += 2
            continue
        if token == "as" and index + 1 < len(tokens):
            index += 1
            continue
        if token == "::" and index + 1 < len(tokens):
            cast_type = tokens[index + 1]
            if cast_type == "text":
                index += 2
                continue
            if (
                cast_type == "timestamp"
                and index + 3 < len(tokens)
                and tokens[index + 2 : index + 4] == ["with", "time"]
                and index + 4 < len(tokens)
                and tokens[index + 4] == "zone"
            ):
                normalized.extend(["::", "timestamptz"])
                index += 5
                continue
        normalized.append(token)
        index += 1
    return _canonical_boolean_expression(normalized)


def _canonical_boolean_expression(tokens: Sequence[str]) -> str:
    current = _strip_redundant_parentheses(list(tokens))
    for operator in ("or", "and"):
        operands = _split_top_level(current, operator)
        if len(operands) > 1:
            return (
                f"{operator}("
                + ",".join(
                    _canonical_boolean_expression(operand) for operand in operands
                )
                + ")"
            )
    if current and current[0] == "exists":
        return _canonical_exists(current)
    comparison_index = _find_top_level_comparison(current)
    if comparison_index is not None:
        index, operator = comparison_index
        left = _canonical_term(current[:index])
        right_tokens = current[index + 1 :]
        if operator == "is" and right_tokens and right_tokens[0] == "not":
            operator = "is not"
            right_tokens = right_tokens[1:]
        if operator == "in":
            return f"any=({left},{_canonical_list(right_tokens)})"
        if operator == "=" and right_tokens and right_tokens[0] == "any":
            return f"any=({left},{_canonical_any(right_tokens)})"
        return f"{operator}({left},{_canonical_term(right_tokens)})"
    return _canonical_term(current)


def _canonical_exists(tokens: Sequence[str]) -> str:
    subquery = _strip_redundant_parentheses(list(tokens[1:]))
    where_index = _find_top_level_token(subquery, "where")
    if where_index is None:
        return "exists(" + " ".join(subquery) + ")"
    prefix = " ".join(subquery[:where_index])
    predicate = _canonical_boolean_expression(subquery[where_index + 1 :])
    return f"exists({prefix} where {predicate})"


def _canonical_term(tokens: Sequence[str]) -> str:
    current = _strip_redundant_parentheses(list(tokens))
    cast_index = _find_top_level_token(current, "::")
    if cast_index is not None:
        return (
            f"cast({_canonical_term(current[:cast_index])},"
            f"{' '.join(current[cast_index + 1 :])})"
        )
    if (
        len(current) >= 3
        and current[1] == "("
        and current[-1] == ")"
        and _matching_close(current, 1) == len(current) - 1
    ):
        arguments = _split_top_level(current[2:-1], ",")
        rendered_arguments = ",".join(
            _canonical_boolean_expression(argument) for argument in arguments
        )
        return f"{current[0]}({rendered_arguments})"
    return " ".join(current)


def _canonical_any(tokens: Sequence[str]) -> str:
    current = _strip_redundant_parentheses(list(tokens[1:]))
    if current and current[0] == "array":
        current = current[1:]
    if len(current) >= 2 and current[0] == "[" and current[-1] == "]":
        current = current[1:-1]
    return _canonical_list(current)


def _canonical_list(tokens: Sequence[str]) -> str:
    current = _strip_redundant_parentheses(list(tokens))
    return (
        "list("
        + ",".join(_canonical_term(item) for item in _split_top_level(current, ","))
        + ")"
    )


def _find_top_level_comparison(
    tokens: Sequence[str],
) -> tuple[int, str] | None:
    depth = 0
    bracket_depth = 0
    for index, token in enumerate(tokens):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif token == "[":
            bracket_depth += 1
        elif token == "]":
            bracket_depth -= 1
        elif (
            depth == 0
            and bracket_depth == 0
            and token
            in {
                "=",
                "!=",
                "<>",
                "<",
                "<=",
                ">",
                ">=",
                "is",
                "in",
            }
        ):
            return index, token
    return None


def _find_top_level_token(tokens: Sequence[str], expected: str) -> int | None:
    depth = 0
    bracket_depth = 0
    for index, token in enumerate(tokens):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif token == "[":
            bracket_depth += 1
        elif token == "]":
            bracket_depth -= 1
        elif depth == 0 and bracket_depth == 0 and token == expected:
            return index
    return None


def _split_top_level(tokens: Sequence[str], delimiter: str) -> list[list[str]]:
    depth = 0
    bracket_depth = 0
    start = 0
    parts: list[list[str]] = []
    for index, token in enumerate(tokens):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif token == "[":
            bracket_depth += 1
        elif token == "]":
            bracket_depth -= 1
        elif depth == 0 and bracket_depth == 0 and token == delimiter:
            parts.append(list(tokens[start:index]))
            start = index + 1
    parts.append(list(tokens[start:]))
    return parts


def _matching_close(tokens: Sequence[str], opening_index: int) -> int | None:
    depth = 0
    for index in range(opening_index, len(tokens)):
        if tokens[index] == "(":
            depth += 1
        elif tokens[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _strip_redundant_parentheses(tokens: list[str]) -> list[str]:
    current = tokens
    changed = True
    while changed and len(current) >= 2:
        changed = False
        if current[0] == "(" and current[-1] == ")":
            depth = 0
            encloses_all = True
            for index, token in enumerate(current):
                if token == "(":
                    depth += 1
                elif token == ")":
                    depth -= 1
                if depth == 0 and index != len(current) - 1:
                    encloses_all = False
                    break
            if encloses_all:
                current = current[1:-1]
                changed = True
    return current


def _stable_digest(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _object_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [
        cast(Mapping[str, object], item) for item in value if isinstance(item, Mapping)
    ]


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [item for item in value if isinstance(item, str)]
