"""Legacy application schema shared by the first migration and its rollback."""

from collections.abc import Callable


def create_legacy_schema(
    execute: Callable[[str], object], *, if_not_exists: bool = True
) -> None:
    """Create the pre-normalization application tables and indexes."""
    clause = " IF NOT EXISTS" if if_not_exists else ""
    execute(
        f"""
        CREATE TABLE{clause} jobs (
            id VARCHAR NOT NULL PRIMARY KEY,
            record_json TEXT NOT NULL,
            created_at VARCHAR NOT NULL,
            queue_sequence INTEGER NOT NULL
        )
        """
    )
    execute(
        f"""
        CREATE TABLE{clause} app_metadata (
            key VARCHAR NOT NULL PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    execute(
        f"""
        CREATE TABLE{clause} term_maps (
            id VARCHAR NOT NULL PRIMARY KEY,
            name VARCHAR NOT NULL,
            name_folded VARCHAR NOT NULL,
            entry_count INTEGER NOT NULL,
            updated_at VARCHAR NOT NULL,
            sequence INTEGER NOT NULL,
            content_json TEXT NOT NULL
        )
        """
    )
    execute(
        f"""
        CREATE UNIQUE INDEX{clause} uq_term_maps_name_folded
        ON term_maps (name_folded)
        """
    )
    execute(
        f"""
        CREATE TABLE{clause} directory_term_map_bindings (
            directory VARCHAR NOT NULL PRIMARY KEY,
            term_map_id VARCHAR NOT NULL,
            FOREIGN KEY(term_map_id) REFERENCES term_maps(id) ON DELETE CASCADE
        )
        """
    )
    execute(
        f"""
        CREATE INDEX{clause} ix_directory_term_map_bindings_term_map_id
        ON directory_term_map_bindings (term_map_id)
        """
    )
