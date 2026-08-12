"""Production composition root for CueWeaver operations."""

from ..adapters.media import FfmpegMediaAdapter
from ..adapters.output import AtomicOutputPublisher
from ..adapters.translation import PySubtransTranslator
from .discovery import Discovery
from .extraction import Extraction
from .translation import Translation


class CueWeaverApplication:
    """Production application composition with explicit operations."""

    def __init__(self) -> None:
        media = FfmpegMediaAdapter()
        output = AtomicOutputPublisher()
        self.discovery = Discovery(media)
        self.extraction = Extraction(media, output)
        self.translation = Translation(PySubtransTranslator(), output)


__all__ = ["CueWeaverApplication"]
