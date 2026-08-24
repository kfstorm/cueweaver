import sqlite3
from pathlib import Path

from cueweaver.application.database import SqliteDatabase


def test_sqlite_database_bootstraps_the_application_schema(tmp_path: Path):
    database = SqliteDatabase(tmp_path / "nested" / "cueweaver.sqlite3")

    with database.connection() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert tables == {"jobs", "app_metadata"}
    with sqlite3.connect(tmp_path / "nested" / "cueweaver.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone() == (0,)
