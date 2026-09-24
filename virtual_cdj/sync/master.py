"""Quellenunabhängige Master-Auswahl."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ..deck.state import DeckState
from ..prolink.models import BeatEvent, PlayerState
from .models import MasterState

LOG = logging.getLogger("MASTER")


@dataclass(slots=True)
class _RemoteObservation:
    state: PlayerState
    received_ns: int


@dataclass(slots=True)
class _BeatObservation:
    event: BeatEvent
    received_ns: int


class MasterManager:
    """Bestimmt genau einen Master aus lokalen und beobachteten Playern.

    Externe States werden anhand ihrer lokalen Empfangszeit auf Alterung
    geprüft. Der vom Sender stammende ``last_update_ns`` bleibt für Diagnose
    erhalten, wird aber nicht mit einer womöglich fremden monotonen Uhr
    verglichen.
    """

    def __init__(self, *, stale_after_s: float = 3.0) -> None:
        self.stale_after_ns = int(max(0.05, stale_after_s) * 1_000_000_000)
        self._remote: dict[int, _RemoteObservation] = {}
        self._beats: dict[int, _BeatObservation] = {}
        self._local: dict[int, tuple[DeckState, int]] = {}
        self._last_identity: tuple[str, int | None] | None = None

    def update_remote(self, state: PlayerState, *, received_ns: int | None = None) -> None:
        now = time.monotonic_ns() if received_ns is None else received_ns
        self._remote[state.player_id] = _RemoteObservation(state, now)
        if not state.online:
            self._beats.pop(state.player_id, None)

    def update_beat(self, event: BeatEvent, *, received_ns: int | None = None) -> None:
        # Provider normalisieren externe Flanken zuvor auf die lokale
        # monotone Zeitbasis. Damit bleibt der Ereigniszeitpunkt die
        # Rechengrundlage statt der spätere GUI-/poll-Aufruf.
        now = event.timestamp_ns if received_ns is None else received_ns
        self._beats[event.player_id] = _BeatObservation(event, now)

    def update_local(self, state: DeckState, *, observed_ns: int | None = None) -> None:
        now = time.monotonic_ns() if observed_ns is None else observed_ns
        self._local[state.deck_id] = (state, now)

    def remove_remote(self, player_id: int) -> None:
        self._remote.pop(player_id, None)
        self._beats.pop(player_id, None)

    def get_master(self, *, now_ns: int | None = None) -> MasterState | None:
        now = time.monotonic_ns() if now_ns is None else now_ns
        result = self._select_remote(now)
        if result is None:
            result = self._select_local()
        identity = (
            (result.source_type, result.player_id)
            if result is not None
            else ("none", None)
        )
        if identity != self._last_identity:
            before = self._last_identity or ("none", None)
            LOG.info("[MASTER] Master changed %s -> %s", before, identity)
            self._last_identity = identity
        return result

    def _select_remote(self, now_ns: int) -> MasterState | None:
        candidates = [
            observation
            for observation in self._remote.values()
            if observation.state.online
            and observation.state.is_master
            and now_ns - observation.received_ns <= self.stale_after_ns
        ]
        if not candidates:
            return None
        # Bei kurz widersprüchlichen Netzstates gewinnt der zuletzt lokal
        # empfangene Status; Player-ID ist ein stabiler Tie-Breaker.
        observation = max(
            candidates,
            key=lambda item: (item.received_ns, item.state.player_id),
        )
        state = observation.state
        bpm = state.effective_bpm
        beat = self._beats.get(state.player_id)
        phase: float | None = None
        beat_number = state.beat_number
        beat_in_bar = state.beat_in_bar
        if beat is not None and bpm is not None and bpm > 0:
            elapsed_s = max(0.0, (now_ns - beat.received_ns) / 1_000_000_000)
            elapsed_beats = elapsed_s * bpm / 60.0 if state.playing else 0.0
            whole = int(elapsed_beats)
            phase = elapsed_beats - whole
            beat_number = beat.event.beat_number + whole
            beat_in_bar = ((beat.event.beat_in_bar - 1 + whole) % 4) + 1

        position = state.position_ms if state.position_valid else None
        if (
            position is not None
            and state.playing
            and state.original_bpm
            and state.effective_bpm
            and state.original_bpm > 0
        ):
            elapsed_ms = max(0.0, (now_ns - observation.received_ns) / 1_000_000)
            position += elapsed_ms * state.effective_bpm / state.original_bpm
            if state.duration_ms is not None:
                position = min(position, float(state.duration_ms))

        return MasterState(
            source_type="prolink",
            player_id=state.player_id,
            bpm=bpm,
            position_ms=position,
            beat_number=beat_number,
            beat_in_bar=beat_in_bar,
            phase=phase,
            key=state.key,
            track_id=state.track_id,
            playing=state.playing,
            last_update_ns=state.last_update_ns,
            timing_source=state.timing_source,
        )

    def _select_local(self) -> MasterState | None:
        candidates = [
            pair for pair in self._local.values() if pair[0].is_master
        ]
        if not candidates:
            return None
        state, observed_ns = max(
            candidates, key=lambda item: (item[1], item[0].deck_id)
        )
        track = state.track
        grid = track.beat_grid if track is not None else None
        beat_number = (
            grid.beat_number_at(state.position_s) + 1
            if grid is not None and grid.is_valid
            else None
        )
        return MasterState(
            source_type="local",
            player_id=state.deck_id,
            bpm=state.current_bpm if state.current_bpm > 0 else None,
            position_ms=state.position_s * 1000.0,
            beat_number=beat_number,
            beat_in_bar=state.beat if state.beat > 0 else None,
            phase=state.beat_phase if state.has_beat_grid else None,
            key=state.key or None,
            track_id=track.track_id if track is not None else None,
            playing=state.is_playing,
            last_update_ns=observed_ns,
            timing_source="local-deck",
        )
