# ruff: noqa: E501
"""Seal acquired bounded-embedding document refusals.

Revision ID: 20260803_0054
Revises: 20260802_0053
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260803_0054"
down_revision: str | None = "20260802_0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKER = "context_engine_worker"
_DEFINER = "context_engine_worker_lease_definer"
_FUNCTION = "context_worker_refuse_acquired_embedding_document"
_SIGNATURE = (
    "(uuid,uuid,uuid,text,text,bigint,bigint,bytea,"
    "timestamp with time zone,timestamp with time zone)"
)


def upgrade() -> None:
    """Add one lease-bound rollback from acquired to closed failed status."""

    op.execute(f"GRANT CREATE ON SCHEMA public TO {_DEFINER}")
    op.execute(
        f"GRANT DELETE ON TABLE public.file_publication_recovery TO {_DEFINER}"
    )
    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(
        f"""
        CREATE FUNCTION public.{_FUNCTION}(
            requested_organization_id uuid, requested_job_id uuid,
            requested_service_principal_id uuid, requested_source_ref text,
            requested_compilation_refusal_category text,
            requested_lease_generation bigint,
            requested_signing_key_version bigint, requested_nonce bytea,
            requested_issued_at timestamptz,
            requested_expires_at timestamptz
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $function$
        DECLARE
            job_row public.file_import_job%ROWTYPE;
            recovery_row public.file_publication_recovery%ROWTYPE;
            failed_now timestamptz;
        BEGIN
            IF SESSION_USER <> '{_WORKER}'
               OR requested_compilation_refusal_category <>
                    'unsupported_document_shape'
            THEN RETURN false; END IF;
            PERFORM pg_catalog.set_config(
                'app.organization_id', requested_organization_id::text, true
            );
            PERFORM pg_catalog.set_config(
                'app.worker_job_id', requested_job_id::text, true
            );
            failed_now := pg_catalog.statement_timestamp();
            SELECT * INTO job_row FROM public.file_import_job AS job
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.service_principal_id = requested_service_principal_id
              AND job.source_id::text = requested_source_ref
              AND job.state = 'running'
              AND job.resource_ref IS NOT NULL
              AND job.revision_id IS NOT NULL
              AND job.fragment_ref IS NULL
              AND job.lease_generation = requested_lease_generation
              AND job.signing_key_version = requested_signing_key_version
              AND job.lease_nonce_digest = public.digest(requested_nonce, 'sha256')
              AND job.lease_issued_at = requested_issued_at
              AND job.lease_expires_at = requested_expires_at
              AND failed_now >= job.lease_issued_at
              AND failed_now < job.lease_expires_at
              AND EXISTS (
                  SELECT 1 FROM public.service_principal AS principal
                  WHERE principal.organization_id = job.organization_id
                    AND principal.service_principal_id = job.service_principal_id
                    AND principal.workload = job.workload
                    AND principal.worker_audience = job.worker_audience
                    AND principal.operation = job.operation
                    AND principal.enabled IS TRUE
              )
            FOR UPDATE;
            IF job_row.job_id IS NULL THEN RETURN false; END IF;
            SELECT * INTO recovery_row
            FROM public.file_publication_recovery AS recovery
            WHERE recovery.organization_id = requested_organization_id
              AND recovery.job_id = requested_job_id
              AND recovery.source_id = job_row.source_id
              AND recovery.resource_ref = job_row.resource_ref
              AND recovery.revision_id = job_row.revision_id
              AND recovery.checkpoint = 'acquired'
            FOR UPDATE;
            IF recovery_row.job_id IS NULL THEN RETURN false; END IF;
            DELETE FROM public.file_publication_recovery AS recovery
            WHERE recovery.organization_id = requested_organization_id
              AND recovery.job_id = requested_job_id;
            UPDATE public.file_import_job AS job
            SET state = 'failed', failed_at = failed_now,
                resource_ref = NULL, revision_id = NULL,
                compilation_refusal_category =
                    requested_compilation_refusal_category
            WHERE job.organization_id = requested_organization_id
              AND job.job_id = requested_job_id
              AND job.state = 'running';
            RETURN FOUND;
        END; $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION}{_SIGNATURE} FROM PUBLIC")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}{_SIGNATURE} TO {_WORKER}"
    )
    op.execute("RESET ROLE")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {_DEFINER}")


def downgrade() -> None:
    """Remove the dedicated acquired-refusal boundary."""

    op.execute(f"SET LOCAL ROLE {_DEFINER}")
    op.execute(f"DROP FUNCTION public.{_FUNCTION}{_SIGNATURE}")
    op.execute("RESET ROLE")
    op.execute(
        f"REVOKE DELETE ON TABLE public.file_publication_recovery FROM {_DEFINER}"
    )
