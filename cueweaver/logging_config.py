"""Timestamp formatting shared by server and translation logs."""

import logging
from datetime import datetime, timezone


class TimestampedFormatter(logging.Formatter):
    def formatTime(  # noqa: N802 - Override the standard logging formatter API.
        self, record: logging.LogRecord, datefmt: str | None = None
    ) -> str:
        del datefmt
        return datetime.fromtimestamp(record.created, timezone.utc).isoformat(
            timespec="milliseconds"
        )
