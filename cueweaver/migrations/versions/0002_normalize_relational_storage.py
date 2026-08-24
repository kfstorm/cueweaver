"""Replace application JSON persistence with relational SQLite storage.

The previous schema was an internal compatibility format.  Application data is
deliberately discarded here; the Alembic version table is the only retained
state during this one-step migration.
"""

from alembic import op

revision = "0002_normalize_relational_storage"
down_revision = "0001_application_schema"
branch_labels = None
depends_on = None

_SNAPSHOT_ENTRY_COLUMNS = """
            source VARCHAR NOT NULL CHECK (length(source) > 0),
            source_folded VARCHAR NOT NULL CHECK (length(source_folded) > 0),
            target VARCHAR NOT NULL CHECK (length(target) > 0),
"""


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS job_term_map_snapshots")
    op.execute("DROP TABLE IF EXISTS job_status_history")
    op.execute("DROP TABLE IF EXISTS jobs")
    op.execute("DROP TABLE IF EXISTS term_map_entries")
    op.execute("DROP TABLE IF EXISTS directory_term_map_bindings")
    op.execute("DROP TABLE IF EXISTS term_maps")
    op.execute("DROP TABLE IF EXISTS app_metadata")

    op.execute(
        """
        CREATE TABLE jobs (
            id VARCHAR NOT NULL PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
            status VARCHAR NOT NULL CHECK (
                status IN ('Queued', 'Extracting', 'Translating', 'Completed',
                           'Failed', 'Interrupted', 'Cancelled')
            ),
            attempt INTEGER NOT NULL CHECK (attempt >= 1),
            created_at VARCHAR NOT NULL,
            started_at VARCHAR,
            finished_at VARCHAR,
            error_code VARCHAR,
            error_message VARCHAR,
            error_field VARCHAR,
            error_media_path VARCHAR,
            error_output_path VARCHAR,
            error_path VARCHAR,
            error_stream_index INTEGER CHECK (
                error_stream_index IS NULL OR error_stream_index >= 0
            ),
            queue_sequence INTEGER NOT NULL CHECK (queue_sequence >= 0),
            media_path VARCHAR NOT NULL CHECK (length(media_path) > 0),
            subtitle_path VARCHAR,
            stream_index INTEGER CHECK (stream_index IS NULL OR stream_index >= 0),
            target_language_code VARCHAR NOT NULL CHECK (length(target_language_code) > 0),
            term_map_mode VARCHAR NOT NULL CHECK (
                term_map_mode IN ('follow', 'selected', 'none')
            ),
            term_map_id VARCHAR,
            term_map_name VARCHAR,
            output_path VARCHAR NOT NULL CHECK (length(output_path) > 0),
            source_format VARCHAR NOT NULL CHECK (length(source_format) > 0),
            dynamic_terminology_enabled BOOLEAN NOT NULL,
            subtitle_terminology_filter_enabled BOOLEAN NOT NULL,
            output_suffix VARCHAR NOT NULL CHECK (length(output_suffix) > 0),
            output_conflict_policy VARCHAR NOT NULL CHECK (
                output_conflict_policy IN ('append-number', 'overwrite', 'skip')
            ),
            extraction_status VARCHAR,
            extraction_path VARCHAR,
            extraction_format VARCHAR,
            extraction_content_digest VARCHAR,
            CHECK (
                (subtitle_path IS NOT NULL AND stream_index IS NULL)
                OR (subtitle_path IS NULL AND stream_index IS NOT NULL)
            ),
            CHECK ((error_code IS NULL) = (error_message IS NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE job_status_history (
            job_id VARCHAR NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence >= 0),
            status VARCHAR NOT NULL CHECK (
                status IN ('Queued', 'Extracting', 'Translating', 'Completed',
                           'Failed', 'Interrupted', 'Cancelled')
            ),
            attempt INTEGER NOT NULL CHECK (attempt >= 1),
            started_at VARCHAR NOT NULL,
            finished_at VARCHAR,
            PRIMARY KEY (job_id, sequence),
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE job_term_map_snapshots (
            job_id VARCHAR NOT NULL,
            position INTEGER NOT NULL CHECK (position >= 0),
            {_SNAPSHOT_ENTRY_COLUMNS}
            PRIMARY KEY (job_id, position),
            UNIQUE (job_id, source_folded),
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE term_maps (
            id VARCHAR NOT NULL PRIMARY KEY,
            name VARCHAR NOT NULL CHECK (length(name) > 0),
            name_folded VARCHAR NOT NULL UNIQUE,
            updated_at VARCHAR NOT NULL,
            sequence INTEGER NOT NULL
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE term_map_entries (
            term_map_id VARCHAR NOT NULL,
            position INTEGER NOT NULL CHECK (position >= 0),
            {_SNAPSHOT_ENTRY_COLUMNS}
            PRIMARY KEY (term_map_id, position),
            UNIQUE (term_map_id, source_folded),
            FOREIGN KEY(term_map_id) REFERENCES term_maps(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE directory_term_map_bindings (
            directory VARCHAR NOT NULL PRIMARY KEY,
            term_map_id VARCHAR NOT NULL,
            FOREIGN KEY(term_map_id) REFERENCES term_maps(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_jobs_queue ON jobs (status, queue_sequence, created_at, id)"
    )
    op.execute("CREATE INDEX ix_jobs_history ON jobs (status, created_at, id)")
    op.execute(
        "CREATE INDEX ix_job_status_history_job ON job_status_history (job_id, sequence)"
    )
    op.execute(
        "CREATE INDEX ix_job_term_map_snapshots_job "
        "ON job_term_map_snapshots (job_id, position)"
    )
    op.execute(
        "CREATE INDEX ix_term_map_entries_map ON term_map_entries (term_map_id, position)"
    )
    op.execute(
        "CREATE INDEX ix_directory_term_map_bindings_term_map_id "
        "ON directory_term_map_bindings (term_map_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS directory_term_map_bindings")
    op.execute("DROP TABLE IF EXISTS term_map_entries")
    op.execute("DROP TABLE IF EXISTS term_maps")
    op.execute("DROP TABLE IF EXISTS job_term_map_snapshots")
    op.execute("DROP TABLE IF EXISTS job_status_history")
    op.execute("DROP TABLE IF EXISTS jobs")
