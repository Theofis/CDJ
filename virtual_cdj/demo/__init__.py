"""Demo-Modus.

Klar getrennt vom Produktionscode. Nur hier duerfen synthetische Analyse-,
Audio- und Sensordaten entstehen (Abschnitt 31 des Auftrags). Erkennbar
bleiben die Tracks an ``TrackInfo.source == "DEMO"``.

Weder ``audio/``, ``deck/``, ``jog/`` noch ``cdj_ui/`` importieren dieses
Paket - angebunden wird es in ``virtual_cdj/app.py`` (Tracks) und in
``run_jog.py`` (Jog-Signal zur Kalibrierung ohne Geraet).
"""

from .jog import SimulatedJog
from .tracks import (
    BROKEN_WINDOW,
    SPECS,
    STEEL_PRESSURE,
    DemoTrack,
    DemoTrackProvider,
    DemoTrackSpec,
    Section,
    build_audio,
    build_beat_grid,
    build_hot_cues,
    build_track_info,
    build_waveform,
)

__all__ = [
    "BROKEN_WINDOW",
    "DemoTrack",
    "DemoTrackProvider",
    "DemoTrackSpec",
    "SPECS",
    "STEEL_PRESSURE",
    "Section",
    "SimulatedJog",
    "build_audio",
    "build_beat_grid",
    "build_hot_cues",
    "build_track_info",
    "build_waveform",
]
