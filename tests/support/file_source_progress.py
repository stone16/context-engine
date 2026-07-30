from __future__ import annotations

from sqlalchemy import text

from engine.persistence import DatabaseConfiguration, create_database_engine


def clear_file_source_progress_projection(
    configuration: DatabaseConfiguration,
) -> None:
    """Clear only disposable Issue #29 projections for migration tests."""

    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
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
        immutable_triggers = [
            (
                "file_source_publish_watermark",
                "file_source_publish_watermark_immutable",
            ),
            (
                "file_source_acquisition_checkpoint",
                "file_source_acquisition_checkpoint_immutable",
            ),
        ]
        if change_tables_exist:
            immutable_triggers.extend(
                [
                    ("file_source_change", "file_source_change_immutable"),
                    (
                        "file_source_change_page",
                        "file_source_change_page_immutable",
                    ),
                ]
            )
        with engine.begin() as connection:
            for table_name, trigger_name in immutable_triggers:
                connection.execute(
                    text(f"ALTER TABLE {table_name} DISABLE TRIGGER {trigger_name}")
                )
        try:
            with engine.begin() as connection:
                connection.execute(text("DELETE FROM file_source_publish_watermark"))
                connection.execute(
                    text("DELETE FROM file_source_acquisition_checkpoint")
                )
                if not change_tables_exist:
                    return
                delete_observation_table_exists = connection.execute(
                    text(
                        "SELECT to_regclass("
                        "'public.file_source_delete_observation_page'"
                        ") IS NOT NULL"
                    )
                ).scalar_one()
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
                    if delete_observation_table_exists:
                        connection.execute(
                            text("DELETE FROM file_source_delete_observation_page")
                        )
                    connection.execute(text("DELETE FROM file_source_change"))
                    connection.execute(text("DELETE FROM file_source_change_page"))
        finally:
            with engine.begin() as connection:
                for table_name, trigger_name in reversed(immutable_triggers):
                    connection.execute(
                        text(
                            f"ALTER TABLE {table_name} ENABLE TRIGGER {trigger_name}"
                        )
                    )
    finally:
        engine.dispose()
