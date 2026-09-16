"""Audioschicht: Dekodieren, Analyse, Cache, Wiedergabe.

Signalweg:

    Track File -> Decoder -> AudioBuffer -> Analysis -> AnalysisCache
                                                 |
                                                 v
                                            TrackLoader
                                                 |
                                    +------------+------------+
                                    v                         v
                              DeckVoice (Audio)          TrackInfo (Zustand)
                                    |                         |
                              AudioEngine                    Deck
                                                               |
                                                          DeckState -> GUI

Diese Schicht kennt kein Tkinter und keine Hardware.
"""

from .analysis import ANALYSIS_VERSION, TrackAnalysis, TrackAnalyzer
from .cache import AnalysisCache, CacheKey, CacheStats
from .decoder import (
    AudioDecoder,
    AudioMetadata,
    DecodeError,
    SoundFileDecoder,
    UnsupportedFormat,
    open_decoder,
    register_backend,
    supported_extensions,
)
from .engine import AudioEngine, DeckVoice
from .format import ENGINE_SAMPLE_RATE, AudioBuffer, to_internal
from .loader import LoadedTrack, TrackLoader
from .metrics import AnalysisMetrics, AudioMetrics
from .resample import resample
from .tempo_proc import (
    KeyLockTempoProcessor,
    SimpleResamplerTempoProcessor,
    TempoProcessor,
)
from .worker import AnalysisWorker, LoadRequest, LoadResult

__all__ = [
    "ANALYSIS_VERSION",
    "AnalysisCache",
    "AnalysisMetrics",
    "AnalysisWorker",
    "AudioBuffer",
    "AudioDecoder",
    "AudioEngine",
    "AudioMetadata",
    "AudioMetrics",
    "CacheKey",
    "CacheStats",
    "DecodeError",
    "DeckVoice",
    "ENGINE_SAMPLE_RATE",
    "KeyLockTempoProcessor",
    "LoadRequest",
    "LoadResult",
    "LoadedTrack",
    "SimpleResamplerTempoProcessor",
    "SoundFileDecoder",
    "TempoProcessor",
    "TrackAnalysis",
    "TrackAnalyzer",
    "TrackLoader",
    "UnsupportedFormat",
    "open_decoder",
    "register_backend",
    "resample",
    "supported_extensions",
    "to_internal",
]
