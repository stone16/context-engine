from __future__ import annotations

from sqlalchemy import text

from engine.persistence import DatabaseConfiguration, create_database_engine


def clear_file_source_progress_projection(
    configuration: DatabaseConfiguration,
) -> None:
    """Clear only disposable Issue #29 projections for migration tests."""

    engine = create_database_engine(configuration)
    try:
        with engine.begin() as connection:
            tables_exist = connection.execute(
                text(
                    """
                    SELECT to_regclass(
                               'public.file_source_acquisition_checkpoint'
                           ) IS NOT NULL
                       AND to_regclass(
                               'public.file_source_publish_watermark'
                           ) IS NOT NULL
                    """
                )
            ).scalar_one()
            if not tables_exist:
                return
            connection.execute(
                text(
                    "ALTER TABLE file_source_publish_watermark "
                    "DISABLE TRIGGER file_source_publish_watermark_immutable"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE file_source_acquisition_checkpoint "
                    "DISABLE TRIGGER file_source_acquisition_checkpoint_immutable"
                )
            )
            connection.execute(text("DELETE FROM file_source_publish_watermark"))
            connection.execute(text("DELETE FROM file_source_acquisition_checkpoint"))
            connection.execute(
                text(
                    "ALTER TABLE file_source_acquisition_checkpoint "
                    "ENABLE TRIGGER file_source_acquisition_checkpoint_immutable"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE file_source_publish_watermark "
                    "ENABLE TRIGGER file_source_publish_watermark_immutable"
                )
            )
    finally:
        engine.dispose()
