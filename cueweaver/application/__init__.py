"""Production composition root for CueWeaver operations."""

from ..adapters.media import FfmpegMediaAdapter
from ..adapters.output import AtomicOutputPublisher
from ..adapters.translation import PySubtransTranslator
from .discovery import Discovery
from .extraction import Extraction
from .translation import Translation, Translator


class CueWeaverApplication:
    """Production application composition with explicit operations."""

    def __init__(self, translator: Translator | None = None) -> None:
        media = FfmpegMediaAdapter()
        output = AtomicOutputPublisher()
        self.discovery = Discovery(media)
        self.extraction = Extraction(media, output)
        configured_translator = (
            PySubtransTranslator() if translator is None else translator
        )
        self.translation = Translation(configured_translator, output)


__all__ = ["CueWeaverApplication"]
