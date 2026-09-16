"""CDJ-Bildschirmoberflaeche.

Getrennt vom Hardware-Simulator in ``virtual_cdj.ui``. Diese Schicht zeigt
einen ``CdjDisplayState`` an und sendet ``DeckCommand``-Objekte. Sie kennt
keine GPIOs, keine Audio-Engine und keine Analyse.
"""

from .browser import CdjBrowser
from .debug_overlay import CdjDebugOverlay
from .master_waveform import CdjMasterWaveform
from .overview_waveform import CdjOverviewWaveform
from .panels import BeatJumpPanel, BeatLoopPanel, KeyShiftPanel
from .screen import CdjScreen
from .scrolling_waveform import CdjScrollingWaveform
from .source_screen import CdjSourceScreen
from .status_bar import CdjStatusBar
from .top_bar import CdjTopBar
from .track_info_popup import CdjTrackInfoPopup
from .waveform_header import CdjWaveformHeader
from .waveform_render import CanvasWaveform, WaveformRenderer
from .window import CdjDisplayApp, CdjDisplayWindow

__all__ = [
    "BeatJumpPanel",
    "BeatLoopPanel",
    "CanvasWaveform",
    "CdjBrowser",
    "CdjDebugOverlay",
    "CdjDisplayApp",
    "CdjDisplayWindow",
    "CdjMasterWaveform",
    "CdjOverviewWaveform",
    "CdjScreen",
    "CdjScrollingWaveform",
    "CdjSourceScreen",
    "CdjStatusBar",
    "CdjTopBar",
    "CdjTrackInfoPopup",
    "CdjWaveformHeader",
    "KeyShiftPanel",
    "WaveformRenderer",
]
