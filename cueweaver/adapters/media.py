"""ffprobe and ffmpeg adapter for media subtitle streams."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..application.errors import ServiceError


class FfmpegMediaAdapter:
    def probe_subtitle_streams(self, media_path: Path) -> list[dict[str, object]]:
        try:
            completed = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "s",
                    "-show_streams",
                    "-of",
                    "json",
                    str(media_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
        except (OSError, subprocess.CalledProcessError, TypeError, ValueError) as error:
            raise ServiceError(
                "discovery_failed", "ffprobe failed", path=media_path
            ) from error
        if not isinstance(payload, dict) or not isinstance(
            payload.get("streams"), list
        ):
            raise ServiceError(
                "discovery_failed", "ffprobe returned invalid container metadata"
            )
        return [stream for stream in payload["streams"] if isinstance(stream, dict)]

    def extract_subtitle(
        self, media_path: Path, stream_index: int, output_path: Path
    ) -> None:
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(media_path),
                    "-map",
                    f"0:{stream_index}",
                    "-c:s",
                    "copy",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise ServiceError("extraction_failed", "ffmpeg failed") from error
