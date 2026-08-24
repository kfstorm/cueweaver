import json
import sqlite3
from pathlib import Path

from cueweaver.application.database import SqliteDatabase
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
        "app_metadata",
        "directory_term_map_bindings",
        "jobs",
        "term_maps",
    }
    with sqlite3.connect(tmp_path / "nested" / "cueweaver.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone() == (0,)
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0001_application_schema",)


def test_migration_adopts_the_database_created_by_issue_193(tmp_path: Path):
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
        assert connection.execute("SELECT id FROM jobs").fetchone() == ("job-1",)
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0001_application_schema",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'term_maps'"
        ).fetchone() == ("term_maps",)

    loaded = SqliteJobRecordStore(database).load()
    assert loaded[0]["id"] == "job-1"
    assert loaded[0]["schema_version"] == 1


def test_sqlite_database_supports_question_marks_in_the_work_root_path(
    tmp_path: Path,
):
    database_path = tmp_path / "work?special" / "cueweaver.sqlite3"
    database = SqliteDatabase(database_path)

    database.initialize()

    assert database_path.is_file()
    assert not (tmp_path / "work").exists()
