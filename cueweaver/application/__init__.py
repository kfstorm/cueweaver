"""Production composition root for CueWeaver operations."""

from pathlib import Path

from ..adapters.directory_term_maps import FileDirectoryTermMapStore
from ..adapters.locking import DurableFileLock
from ..adapters.media import FfmpegMediaAdapter
from ..adapters.output import AtomicOutputPublisher
from ..adapters.term_maps import FileTermMapStore
from ..adapters.translation import PySubtransTranslator
from ..work import WorkRoot
from .browsing import MediaBrowser
from .directory_term_maps import DirectoryTermMaps
from .discovery import Discovery
from .extraction import Extraction
from .jobs import Jobs
from .term_maps import TermMaps
from .translation import Translator


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
        configured_work_root = WorkRoot(work_root or Path.cwd())
        storage_lock = DurableFileLock(
            configured_work_root.term_maps_directory / ".lock"
        )
        directory_term_map_store = FileDirectoryTermMapStore(
            configured_work_root, lock=storage_lock
        )
        term_map_store = FileTermMapStore(
            configured_work_root,
            directory_bindings=directory_term_map_store,
            lock=storage_lock,
        )
        self.term_maps = TermMaps(term_map_store)
        if media_root is not None:
            self.directory_term_maps = DirectoryTermMaps(
                directory_term_map_store, self.term_maps, media_root
            )
        if work_root is not None and media_root is not None:
            self.jobs = Jobs(
                configured_translator,
                media_root,
                work_root,
                self.term_maps,
                self.extraction,
            )


__all__ = ["CueWeaverApplication"]
