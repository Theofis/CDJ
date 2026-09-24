"""Quellenunabhängige Zustände an der ProLink-Grenze.

Die Modelle enthalten weder GUI- noch Audio-Objekte.  Analysebestandteile,
die das Projekt bereits besitzt, werden wiederverwendet; insbesondere gibt
es kein zweites Beatgrid-, Cue- oder Waveform-Format.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..deck.state import BeatGrid, HotCue, MemoryCue, WaveformData


@dataclass(frozen=True, slots=True)
class PlayerState:
    """Momentaufnahme eines beobachteten externen Players."""

    # Identität
    player_id: int
    device_name: str | None = None
    device_type: str | None = None
    ip_address: str | None = None

    # Verbindung
    online: bool = False
    last_update_ns: int = 0

    # Transport
    playing: bool = False
    paused: bool = False
    reverse: bool = False

    # Track
    track_id: str | None = None
    track_source_player: int | None = None
    duration_ms: int | None = None

    # Position
    position_ms: float | None = None
    position_valid: bool = False

    # Tempo: Analysewert und tatsächliche Wiedergabegeschwindigkeit sind
    # mit Absicht zwei unabhängige Felder.
    original_bpm: float | None = None
    effective_bpm: float | None = None
    pitch_percent: float | None = None

    # Beat
    beat_number: int | None = None
    beat_in_bar: int | None = None

    # Sync
    sync_enabled: bool = False
    is_master: bool = False

    # Mixer / Status
    on_air: bool | None = None

    # Tonart
    key: str | None = None

    # Diagnose / Herkunft
    timing_source: str = "unknown"


@dataclass(frozen=True, slots=True)
class BeatEvent:
    """Zeitkritische Beat-Flanke eines Players.

    ``timestamp_ns`` liegt nach dem Provider-Adapter auf der monotonen
    Zeitbasis des empfangenden Prozesses. Dadurch rechnet die Sync-Schicht
    nicht mit GUI-Framezeiten und muss keine Uhren verschiedener Prozesse
    miteinander vergleichen.
    """

    player_id: int
    timestamp_ns: int
    beat_number: int
    beat_in_bar: int
    bpm: float
    position_ms: float | None = None
    is_master: bool = False


@dataclass(frozen=True, slots=True)
class TrackMetadata:
    """Selten geänderte beschreibende Trackdaten."""

    track_id: str
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    genre: str | None = None
    bpm: float | None = None
    key: str | None = None
    rating: int | None = None
    color: str | None = None
    duration_ms: int | None = None
    artwork_id: str | None = None


@dataclass(frozen=True, slots=True)
class TrackAnalysis:
    """Analysedaten eines Tracks, getrennt vom Live-Playerzustand."""

    track_id: str
    beatgrid: BeatGrid | None = None
    waveform_preview: WaveformData | None = None
    waveform_detail: WaveformData | None = None
    hot_cues: tuple[HotCue, ...] = ()
    memory_cues: tuple[MemoryCue, ...] = ()
    memory_loops: tuple[MemoryCue, ...] = ()


@dataclass(frozen=True, slots=True)
class RemotePlayer:
    """Beobachteter Player ohne lokale Audio- oder Transporthoheit."""

    player_state: PlayerState
    metadata: TrackMetadata | None = None
    analysis: TrackAnalysis | None = None
