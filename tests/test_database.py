import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine

from cueweaver.application.database import (
    JobRow,
    JobStatusHistoryRow,
    JobTermMapSnapshotRow,
    SqliteDatabase,
    _migration_config,
)
from cueweaver.application.jobs.store import SqliteJobRecordStore


def test_sqlite_database_bootstraps_the_application_schema(tmp_path: Path):
    database = SqliteDatabase(tmp_path / "nested" / "cueweaver.sqlite3")

    database.initialize()
    with sqlite3.connect(tmp_path / "nested" / "cueweaver.sqlite3") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert tables == {
        "alembic_version",
        "directory_term_map_bindings",
        "jobs",
        "job_status_history",
        "job_term_map_snapshots",
        "term_map_entries",
        "term_maps",
    }
    with sqlite3.connect(tmp_path / "nested" / "cueweaver.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone() == (0,)
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0002_normalize_relational_storage",)
        assert connection.execute("PRAGMA table_info(jobs)").fetchall()
        assert {
            row[1] for row in connection.execute("PRAGMA table_info(jobs)")
        }.isdisjoint({"record_json", "content_json"})

    with database.read_session() as session:
        assert session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_write_transaction_commits_a_multi_table_change(tmp_path: Path):
    database = SqliteDatabase(tmp_path / "cueweaver.sqlite3")

    with database.write_transaction() as session:
        _add_job_rows(session)

    with database.read_session() as session:
        assert session.get(JobRow, "job-1") is not None
        assert session.get(JobStatusHistoryRow, {"job_id": "job-1", "sequence": 0})
        assert session.get(JobTermMapSnapshotRow, {"job_id": "job-1", "position": 0})


def test_write_transaction_rolls_back_when_the_scope_fails(tmp_path: Path):
    database = SqliteDatabase(tmp_path / "cueweaver.sqlite3")

    with (
        pytest.raises(RuntimeError, match="abort"),
        database.write_transaction() as session,
    ):
        _add_job_rows(session)
        raise RuntimeError("abort")

    with database.read_session() as session:
        assert session.get(JobRow, "job-1") is None
        assert (
            session.get(JobStatusHistoryRow, {"job_id": "job-1", "sequence": 0}) is None
        )
        assert (
            session.get(JobTermMapSnapshotRow, {"job_id": "job-1", "position": 0})
            is None
        )


def _job_row() -> JobRow:
    return JobRow(
        id="job-1",
        schema_version=1,
        status="Queued",
        attempt=1,
        created_at="2026-08-24T00:00:00Z",
        queue_sequence=0,
        media_path="Movie.mkv",
        subtitle_path="Movie.en.srt",
        target_language_code="zh-Hans",
        term_map_mode="none",
        output_path="Movie.zh-Hans.srt",
        source_format="srt",
        dynamic_terminology_enabled=True,
        subtitle_terminology_filter_enabled=True,
        output_suffix="zh-Hans",
        output_conflict_policy="skip",
    )


def _add_job_rows(session) -> None:
    session.add(_job_row())
    session.add(
        JobStatusHistoryRow(
            job_id="job-1",
            sequence=0,
            status="Queued",
            attempt=1,
            started_at="2026-08-24T00:00:00Z",
        )
    )
    session.add(
        JobTermMapSnapshotRow(
            job_id="job-1",
            position=0,
            source="Captain",
            source_folded="captain",
            target="队长",
        )
    )


def test_migration_discards_issue_193_application_data(tmp_path: Path):
    path = tmp_path / "cueweaver.sqlite3"
    record = {
        "id": "job-1",
        "status": "Failed",
        "request": {
            "media_path": "Movie.mkv",
            "subtitle_path": "Movie.en.srt",
            "target_language_code": "zh-Hans",
            "output_path": "Movie.zh-Hans.srt",
            "source_format": "srt",
        },
    }
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                record_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                queue_sequence INTEGER NOT NULL
            );
            CREATE TABLE app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """,
        )
        connection.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?)",
            ("job-1", json.dumps(record), "2026-08-24T00:00:00Z", 0),
        )

    database = SqliteDatabase(path)
    database.initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT id FROM jobs").fetchone() is None
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'app_metadata'"
            ).fetchone()
            is None
        )
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0002_normalize_relational_storage",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'term_maps'"
        ).fetchone() == ("term_maps",)

    assert SqliteJobRecordStore(database).load() == []


def test_sqlite_database_supports_question_marks_in_the_work_root_path(
    tmp_path: Path,
):
    database_path = tmp_path / "work?special" / "cueweaver.sqlite3"
    database = SqliteDatabase(database_path)

    database.initialize()

    assert database_path.is_file()
    assert not (tmp_path / "work").exists()


def test_sqlite_schema_rejects_partial_extraction_state(tmp_path: Path):
    database_path = tmp_path / "cueweaver.sqlite3"
    SqliteDatabase(database_path).initialize()

    with (
        sqlite3.connect(database_path) as connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        connection.execute(
            """
                INSERT INTO jobs (
                    id, schema_version, status, attempt, created_at, queue_sequence,
                    media_path, stream_index, target_language_code, term_map_mode,
                    output_path, source_format, dynamic_terminology_enabled,
                    subtitle_terminology_filter_enabled, output_suffix,
                    output_conflict_policy, extraction_status, extraction_format,
                    extraction_content_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
            (
                "partial-extraction",
                1,
                "Queued",
                1,
                "2026-08-24T00:00:00Z",
                0,
                "Movie.mkv",
                0,
                "zh-Hans",
                "none",
                "Movie.zh-Hans.srt",
                "srt",
                True,
                True,
                "zh-Hans",
                "skip",
                "Completed",
                "srt",
                "0" * 64,
            ),
        )


def test_normalized_migration_downgrade_recreates_the_legacy_schema(
    tmp_path: Path,
):
    database_path = tmp_path / "cueweaver.sqlite3"
    database = SqliteDatabase(database_path)
    database.initialize()
    database.close()

    engine = create_engine(URL.create("sqlite+pysqlite", database=str(database_path)))
    config: Config = _migration_config()
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0001_application_schema")

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert tables == {
            "alembic_version",
            "app_metadata",
            "directory_term_map_bindings",
            "jobs",
            "term_maps",
        }
        assert {row[1] for row in connection.execute("PRAGMA table_info(jobs)")} == {
            "id",
            "record_json",
            "created_at",
            "queue_sequence",
        }
        assert {
            row[1] for row in connection.execute("PRAGMA table_info(term_maps)")
        } == {
            "id",
            "name",
            "name_folded",
            "entry_count",
            "updated_at",
            "sequence",
            "content_json",
        }
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0001_application_schema",)

    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    engine.dispose()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0002_normalize_relational_storage",)
