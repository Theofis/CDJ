"""Eingabeverteilung nach Anwendungsmodus.

Zwischen Input-Schicht und DJ-Logik steht genau diese Stelle:

    InputLayer -> InputRouter -> InputMapper -> DeckCommand -> Deck
                       |
                       +-> Beobachter (Test- und Kalibrierseite)

Damit gilt:

    Application_Mode == PERFORMANCE   Eingaben loesen DJ-Funktionen aus
    Application_Mode == TEST          Eingaben gehen nur in die Testanzeige
    Application_Mode == CALIBRATION   Eingaben gehen nur in die Kalibrierung
    Application_Mode == MENU/SETTINGS Eingaben loesen keine DJ-Funktion aus

Ein Hotcue-Taster im Testmodus laesst also die Anzeige reagieren, aber den
Track stehen.

Der Zustand der Hardware wird in **jedem** Modus fortgeschrieben - das macht
die Input-Schicht selbst, und ``HardwareState`` liest ihn. Der Router
entscheidet nur, wer die Ereignisse zu sehen bekommt.
"""

from __future__ import annotations

from collections.abc import Callable

from ..core.model import InputEvent
from .modes import ApplicationMode, ModeController

EventSink = Callable[[InputEvent], None]


class InputRouter:
    """Verteilt Eingabeereignisse abhaengig vom Anwendungsmodus."""

    def __init__(
        self,
        modes: ModeController,
        *,
        performance_sink: EventSink | None = None,
    ) -> None:
        self.modes = modes
        #: Empfaenger fuer den Performance-Modus - in der Regel
        #: ``InputMapper.handle_event``.
        self.performance_sink = performance_sink
        #: Beobachter je Modus. Sie bekommen Ereignisse **nur** in ihrem
        #: Modus zu sehen.
        self._observers: dict[ApplicationMode, list[EventSink]] = {}
        #: Beobachter, die jedes Ereignis sehen, egal in welchem Modus
        #: (Diagnose, Aufzeichnung).
        self._always: list[EventSink] = []
        #: Ereignisse, die im aktuellen Modus verworfen wurden - Diagnose.
        self.blocked = 0

    # ------------------------------------------------------------------

    def observe(
        self, mode: ApplicationMode, sink: EventSink
    ) -> Callable[[], None]:
        """Beobachter fuer einen Modus anmelden."""
        sinks = self._observers.setdefault(mode, [])
        sinks.append(sink)

        def remove() -> None:
            if sink in sinks:
                sinks.remove(sink)

        return remove

    def observe_always(self, sink: EventSink) -> Callable[[], None]:
        self._always.append(sink)

        def remove() -> None:
            if sink in self._always:
                self._always.remove(sink)

        return remove

    # ------------------------------------------------------------------

    def handle_event(self, event: InputEvent) -> bool:
        """Ein Ereignis verteilen. Rueckgabe: ob es DJ-Funktionen erreichte.

        An die Input-Schicht anzumelden::

            input_layer.subscribe(router.handle_event)
        """
        for sink in list(self._always):
            sink(event)

        mode = self.modes.mode
        for sink in list(self._observers.get(mode, ())):
            sink(event)

        if not mode.controls_deck:
            self.blocked += 1
            return False
        if self.performance_sink is None:
            return False
        self.performance_sink(event)
        return True

    __call__ = handle_event
