"""Gemeinsame Deck-Fassade zwischen Controls, GUI und ModeManager."""

from __future__ import annotations

from collections.abc import Callable

from .commands import DeckCommand
from .mode_manager import ModeManager, OperatingMode
from .provider import DeckStateProvider, Listener
from .state import DeckState, TrackInfo


class DeckController(DeckStateProvider):
    """Leitet jede Deck-Aktion an das vom ModeManager gewaehlte Backend.

    Gleichzeitig implementiert die Klasse ``DeckStateProvider``. Dadurch
    bleibt die vorhandene GUI unveraendert an einer einzigen State-/Command-
    Schnittstelle und kann keinen Backend-Typ erkennen.
    """

    def __init__(self, deck_id: int, modes: ModeManager) -> None:
        self._deck_id = deck_id
        self.modes = modes
        self._listeners: list[Listener] = []
        self._unsubscribe_state: Callable[[], None] | None = None
        self._unsubscribe_mode = modes.subscribe(self._on_mode)
        self._bind_active_backend()

    @property
    def deck_id(self) -> int:
        return self._deck_id

    @property
    def operating_mode(self) -> OperatingMode:
        return self.modes.current_mode

    @property
    def deck(self):
        """Direktzugriff nur fuer Anwendung und bestehende Integrationstests.

        Die GUI verwendet ausschliesslich ``get_state``/``send``. Die
        Eigenschaft erhaelt den bisherigen Diagnosevertrag des
        ``LocalDeckStateProvider`` und zeigt immer auf das aktive Backend.
        """
        backend = self.modes.active_backend
        decks = getattr(backend, "decks", None)
        if decks is None or self.deck_id not in decks:
            raise AttributeError("Das aktive Backend gibt kein Deck direkt frei")
        return decks[self.deck_id]

    def get_state(self) -> DeckState:
        return self.modes.active_backend.get_state(self.deck_id)

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def send(self, cmd: DeckCommand) -> None:
        if cmd.deck_id != self.deck_id:
            return
        self.modes.active_backend.send(cmd)

    def tick(self) -> None:
        self.modes.tick(self.deck_id)

    def get_library(self) -> object | None:
        return self.modes.active_backend.get_library()

    def get_track_state(self) -> TrackInfo | None:
        return self.modes.active_backend.get_track_state(self.deck_id)

    def get_playback_state(self) -> DeckState:
        return self.modes.active_backend.get_playback_state(self.deck_id)

    # -- Sprechende Performance-API -------------------------------------

    def play(self) -> None:
        self.modes.active_backend.play(self.deck_id)

    def pause(self) -> None:
        self.modes.active_backend.pause(self.deck_id)

    def cue(self, *, pressed: bool = True) -> None:
        self.modes.active_backend.cue(self.deck_id, pressed=pressed)

    def jog(
        self,
        delta: int,
        velocity: float,
        direction: str,
        touched: bool,
        *,
        ticks_per_rev: int = 800,
    ) -> None:
        self.modes.active_backend.jog(
            self.deck_id,
            delta,
            velocity,
            direction,
            touched,
            ticks_per_rev=ticks_per_rev,
        )

    def set_tempo(self, value: float) -> None:
        self.modes.active_backend.set_tempo(self.deck_id, value)

    def load_track(self, track_id: str) -> None:
        self.modes.active_backend.load_track(self.deck_id, track_id)

    def set_hotcue(self, index: int) -> None:
        self.modes.active_backend.set_hotcue(self.deck_id, index)

    def delete_hotcue(self, index: int) -> None:
        self.modes.active_backend.delete_hotcue(self.deck_id, index)

    def loop_in(self) -> None:
        self.modes.active_backend.loop_in(self.deck_id)

    def loop_out(self) -> None:
        self.modes.active_backend.loop_out(self.deck_id)

    def exit_loop(self) -> None:
        self.modes.active_backend.exit_loop(self.deck_id)

    def beat_jump(self, amount: float) -> None:
        self.modes.active_backend.beat_jump(self.deck_id, amount)

    # -- Backend-Umschaltung --------------------------------------------

    def _bind_active_backend(self) -> None:
        if self._unsubscribe_state is not None:
            self._unsubscribe_state()
        self._unsubscribe_state = self.modes.active_backend.subscribe(
            self.deck_id, self._on_state
        )

    def _on_mode(self, _mode: OperatingMode) -> None:
        self._bind_active_backend()
        self._on_state(self.get_state())

    def _on_state(self, state: DeckState) -> None:
        for listener in list(self._listeners):
            listener(state)

    def close(self) -> None:
        if self._unsubscribe_state is not None:
            self._unsubscribe_state()
            self._unsubscribe_state = None
        self._unsubscribe_mode()
