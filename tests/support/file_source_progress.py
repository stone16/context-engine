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
            change_tables_exist = connection.execute(
                text(
                    """
                    SELECT to_regclass('public.file_source_change_page') IS NOT NULL
                       AND to_regclass('public.file_source_change') IS NOT NULL
                    """
                )
            ).scalar_one()
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
            if change_tables_exist:
                connection.execute(
                    text(
                        "ALTER TABLE file_source_change "
                        "DISABLE TRIGGER file_source_change_immutable"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE file_source_change_page "
                        "DISABLE TRIGGER file_source_change_page_immutable"
                    )
                )
                scheduling_columns_exist = connection.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'file_acquisition'
                              AND column_name = 'change_page_ref'
                        )
                        """
                    )
                ).scalar_one()
                scheduled_acquisitions_exist = (
                    scheduling_columns_exist
                    and connection.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM file_acquisition "
                            "WHERE change_page_ref IS NOT NULL)"
                        )
                    ).scalar_one()
                )
                if not scheduled_acquisitions_exist:
                    connection.execute(text("DELETE FROM file_source_change"))
                    connection.execute(text("DELETE FROM file_source_change_page"))
                connection.execute(
                    text(
                        "ALTER TABLE file_source_change_page "
                        "ENABLE TRIGGER file_source_change_page_immutable"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE file_source_change "
                        "ENABLE TRIGGER file_source_change_immutable"
                    )
                )
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
