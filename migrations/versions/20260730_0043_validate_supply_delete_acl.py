"""Validate delete ACL observations at the Supply acceptance boundary.

Revision ID: 20260730_0043
Revises: 20260730_0042
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260730_0043"
down_revision: str | None = "20260730_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "context_engine_worker_lease_definer"
_ACCEPT_REGPROCEDURE = (
    "context_supply_accept_connector_page"
    "(uuid,uuid,uuid,uuid,text,bytea,bigint,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone,bigint,text,"
    "uuid[],text[],timestamp with time zone)"
)
_PRIOR_VALIDATION = """\
                END LOOP;
                staged_checkpoint := decode(
"""
_DELETE_VALIDATION = """\
                END LOOP;
                FOR payload_envelope IN
                    SELECT value
                    FROM jsonb_array_elements(
                        payload_document->'deleted_document_refs'
                    )
                LOOP
                    IF jsonb_typeof(payload_envelope)
                            IS DISTINCT FROM 'object'
                    THEN RETURN; END IF;
                    payload_acl := payload_envelope->'acl_observation';
                    IF jsonb_typeof(payload_envelope->'document_ref')
                            IS DISTINCT FROM 'string'
                       OR btrim(payload_envelope->>'document_ref') = ''
                       OR char_length(payload_envelope->>'document_ref') > 512
                       OR payload_envelope->>'document_ref' ~ '[[:space:]]'
                       OR EXISTS (
                            SELECT 1
                            FROM jsonb_object_keys(payload_envelope) AS item(key)
                            WHERE item.key NOT IN (
                                'acl_observation', 'document_ref'
                            )
                       )
                       OR jsonb_typeof(payload_acl) IS DISTINCT FROM 'object'
                       OR payload_acl->>'organization_id'
                            IS DISTINCT FROM requested_organization_id::text
                       OR payload_acl->>'evidence_class' IS NULL
                       OR payload_acl->>'evidence_class'
                            NOT IN ('live', 'mirrored', 'weak')
                       OR (
                            payload_acl->>'evidence_class' IN ('live', 'mirrored')
                            AND (
                                jsonb_typeof(payload_acl->'evidence_payload')
                                    IS DISTINCT FROM 'string'
                                OR octet_length(
                                    decode(
                                        payload_acl->>'evidence_payload',
                                        'base64'
                                    )
                                ) NOT BETWEEN 1 AND 1048576
                                OR payload_acl->'source_lacks_stronger_acl'
                                    IS DISTINCT FROM 'null'::jsonb
                            )
                       )
                       OR (
                            payload_acl->>'evidence_class' = 'weak'
                            AND (
                                payload_acl->'evidence_payload'
                                    IS DISTINCT FROM 'null'::jsonb
                                OR jsonb_typeof(
                                    payload_acl->'source_lacks_stronger_acl'
                                ) IS DISTINCT FROM 'string'
                                OR btrim(
                                    payload_acl->>'source_lacks_stronger_acl'
                                ) = ''
                                OR payload_acl->>'source_lacks_stronger_acl'
                                    <> btrim(
                                        payload_acl->>
                                            'source_lacks_stronger_acl'
                                    )
                                OR char_length(
                                    payload_acl->>'source_lacks_stronger_acl'
                                ) > 512
                            )
                       )
                    THEN RETURN; END IF;
                END LOOP;
                staged_checkpoint := decode(
"""


def _replace_acceptance_validation(searched: str, replacement: str) -> None:
    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        DO $block$
        DECLARE
            definition text;
            replacement_definition text;
        BEGIN
            definition := pg_catalog.pg_get_functiondef(
                'public.{_ACCEPT_REGPROCEDURE}'::regprocedure
            );
            replacement_definition := pg_catalog.replace(
                definition,
                $search${searched}$search$,
                $replacement${replacement}$replacement$
            );
            IF replacement_definition = definition THEN
                RAISE EXCEPTION
                    'Supply acceptance validation body was not recognized';
            END IF;
            EXECUTE replacement_definition;
        END;
        $block$
        """
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def upgrade() -> None:
    """Re-verify every worker-authored delete ACL observation."""

    _replace_acceptance_validation(_PRIOR_VALIDATION, _DELETE_VALIDATION)


def downgrade() -> None:
    """Restore the Issue #125 Supply acceptance function body."""

    _replace_acceptance_validation(_DELETE_VALIDATION, _PRIOR_VALIDATION)
