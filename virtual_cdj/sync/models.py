"""Ausgaben der von Netzwerk, Deck, Audio und GUI getrennten Sync-Schicht."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MasterState:
    """Normalisierte Sicht auf den momentan gewählten Master."""

    source_type: str
    player_id: int | None = None
    bpm: float | None = None
    position_ms: float | None = None
    beat_number: int | None = None
    beat_in_bar: int | None = None
    phase: float | None = None
    key: str | None = None
    track_id: str | None = None
    playing: bool = False
    last_update_ns: int = 0
    timing_source: str = "unknown"


@dataclass(frozen=True, slots=True)
class SyncTarget:
    """Zielvorgabe; sie verändert weder Position noch Audio direkt."""

    enabled: bool
    master_player_id: int | None = None
    target_bpm: float | None = None
    phase_error_beats: float | None = None
    beat_period_ms: float | None = None
    #: Relative, begrenzte Geschwindigkeitskorrektur. ``0.01`` bedeutet
    #: konzeptionell ein Prozent schneller. Die AudioEngine wendet sie in
    #: Phase 1 noch nicht an.
    correction: float = 0.0
    synchronized: bool = False
