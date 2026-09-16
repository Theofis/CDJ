"""Deck-Schicht: Zustand, Kommandos, Engine, Zustandsquelle.

Diese Schicht kennt weder Tkinter noch Hardware. Sie ist die einzige Wahrheit
ueber den Zustand eines Decks.

Achtung: Es gibt hier **keine** Audio-Engine und **keine** Track-Analyse.
Siehe docs/ANALYSIS_CDJ_SCREEN.md.
"""

from .backend import CdjBackend, DeckBackend, MidiBackend
from .commands import CommandType, DeckCommand, Views, command
from .controller import DeckController
from .engine import Deck
from .loop import LoopEngine
from .mapping import InputMapper
from .mode_manager import ModeManager, OperatingMode
from .provider import (
    DeckStateProvider,
    LocalDeckStateProvider,
    NetworkDeckStateProvider,
)
from .state import (
    AudioStatus,
    BeatGrid,
    CueKind,
    DeckState,
    Direction,
    HotCue,
    JogMode,
    LoopAdjust,
    LoopState,
    MemoryCue,
    PadMode,
    PlayState,
    TrackInfo,
    WaveformData,
    empty_state,
)

__all__ = [
    "AudioStatus",
    "BeatGrid",
    "CdjBackend",
    "CommandType",
    "CueKind",
    "Deck",
    "DeckBackend",
    "DeckCommand",
    "DeckController",
    "DeckState",
    "DeckStateProvider",
    "Direction",
    "HotCue",
    "InputMapper",
    "JogMode",
    "LocalDeckStateProvider",
    "LoopAdjust",
    "LoopEngine",
    "LoopState",
    "MemoryCue",
    "MidiBackend",
    "ModeManager",
    "NetworkDeckStateProvider",
    "OperatingMode",
    "PadMode",
    "PlayState",
    "TrackInfo",
    "Views",
    "WaveformData",
    "command",
    "empty_state",
]
