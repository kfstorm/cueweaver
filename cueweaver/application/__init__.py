"""Production composition root for CueWeaver operations."""

from pathlib import Path

from ..adapters.media import FfmpegMediaAdapter
from ..adapters.output import AtomicOutputPublisher
from ..adapters.term_maps import FileTermMapStore
from ..adapters.translation import PySubtransTranslator
from .browsing import MediaBrowser
from .discovery import Discovery
from .extraction import Extraction
from .jobs import Jobs
from .term_maps import TermMaps
from .translation import Translation, Translator


class CueWeaverApplication:
    """Production application composition with explicit operations."""

    def __init__(
        self,
        translator: Translator | None = None,
        work_root: Path | None = None,
        media_root: Path | None = None,
    ) -> None:
        media = FfmpegMediaAdapter()
        output = AtomicOutputPublisher()
        self.discovery = Discovery(media)
        self.extraction = Extraction(media, output)
        self.browsing = MediaBrowser(media_root) if media_root is not None else None
        configured_translator = (
            PySubtransTranslator() if translator is None else translator
        )
        self.translation = Translation(configured_translator, output)
        if work_root is not None and media_root is not None:
            self.jobs = Jobs(configured_translator, media_root, work_root)
        self.term_maps = TermMaps(FileTermMapStore(work_root or Path.cwd()))


__all__ = ["CueWeaverApplication"]
