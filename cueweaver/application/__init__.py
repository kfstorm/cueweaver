"""Production composition root for CueWeaver operations."""

from pathlib import Path

from ..adapters.directory_term_maps import FileDirectoryTermMapStore
from ..adapters.locking import DurableFileLock
from ..adapters.media import FfmpegMediaAdapter
from ..adapters.output import AtomicOutputPublisher
from ..adapters.term_maps import FileTermMapStore
from ..adapters.translation import PySubtransTranslator
from ..work import WorkRoot, WorkRootLease
from .browsing import MediaBrowser
from .database import SqliteDatabase
from .directory_term_maps import DirectoryTermMaps
from .discovery import Discovery
from .errors import ServiceError
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
        database_path = configured_work_root.path / "cueweaver.sqlite3"
        database = SqliteDatabase(database_path)
        lease = WorkRootLease(configured_work_root.path / ".cueweaver.lease")
        try:
            lease.acquire()
        except ValueError as error:
            raise ServiceError(
                "work_root_in_use", "Another CueWeaver process owns this Work root"
            ) from error
        except OSError as error:
            raise ServiceError(
                "invalid_work_directory", "Work root lease cannot be created"
            ) from error
        self._lease = lease
        self._database = database
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
        directory_term_map_store.set_recovery(term_map_store.recover_pending_deletions)
        try:
            term_map_store.recover_pending_deletions()
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
                    self.directory_term_maps,
                    database=database,
                )
        except Exception:
            self._lease.release()
            raise

    def close(self) -> None:
        """Stop application workers before releasing the Work-root lease."""
        try:
            jobs = getattr(self, "jobs", None)
            if jobs is not None:
                jobs.close()
                jobs.wait_closed()
        finally:
            self._lease.release()


__all__ = ["CueWeaverApplication"]
