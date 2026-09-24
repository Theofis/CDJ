"""Berechnung eines sanften, noch nicht an Audio gekoppelten Sync-Ziels."""

from __future__ import annotations

import logging

from ..deck.state import DeckState
from .models import MasterState, SyncTarget

LOG = logging.getLogger("SYNC")

# Austauschbare erste Reglerparameter. Die Korrektur bleibt bewusst klein;
# sie ist ein Zielwert und wird in Phase 1 nirgends auf Audio angewandt.
PHASE_GAIN = 0.02
MAX_CORRECTION = 0.08
BPM_TOLERANCE = 0.05
PHASE_TOLERANCE = 0.02


class SyncEngine:
    """Interpretation von Master- und lokalem Deck-Timing."""

    def __init__(self) -> None:
        self._last_signature: tuple | None = None

    def target_for(
        self,
        local: DeckState,
        master: MasterState | None,
        *,
        enabled: bool | None = None,
    ) -> SyncTarget:
        active = local.sync if enabled is None else bool(enabled)
        if not active:
            return self._remember(SyncTarget(enabled=False))
        if master is None or master.bpm is None or master.bpm <= 0:
            return self._remember(SyncTarget(enabled=True))

        target_bpm = master.bpm
        beat_period_ms = 60_000.0 / target_bpm
        phase_error = self._phase_error(master.phase, local.beat_phase)
        local_bpm = local.current_bpm

        tempo_error = (
            target_bpm / local_bpm - 1.0 if local_bpm > 0 else 0.0
        )
        phase_term = (phase_error or 0.0) * PHASE_GAIN
        correction = max(
            -MAX_CORRECTION,
            min(MAX_CORRECTION, tempo_error + phase_term),
        )
        synchronized = (
            local_bpm > 0
            and abs(local_bpm - target_bpm) <= BPM_TOLERANCE
            and phase_error is not None
            and abs(phase_error) <= PHASE_TOLERANCE
        )
        return self._remember(
            SyncTarget(
                enabled=True,
                master_player_id=master.player_id,
                target_bpm=target_bpm,
                phase_error_beats=phase_error,
                beat_period_ms=beat_period_ms,
                correction=correction,
                synchronized=synchronized,
            )
        )

    @staticmethod
    def _phase_error(master: float | None, local: float) -> float | None:
        if master is None:
            return None
        # Kürzester signierter Weg im Beat. Beispiel .31 - .77 = -.46.
        error = (master - local + 0.5) % 1.0 - 0.5
        return 0.0 if abs(error) < 1e-12 else error

    def _remember(self, target: SyncTarget) -> SyncTarget:
        signature = (
            target.enabled,
            target.master_player_id,
            round(target.target_bpm or 0.0, 3),
            target.synchronized,
        )
        if signature != self._last_signature:
            if target.enabled and target.target_bpm is not None:
                LOG.info(
                    "[SYNC] Target BPM = %.3f, correction = %.5f",
                    target.target_bpm,
                    target.correction,
                )
            self._last_signature = signature
        return target
