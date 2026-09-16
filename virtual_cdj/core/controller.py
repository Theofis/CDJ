"""CDJ-Controller - in diesem Entwicklungsschritt absichtlich ohne Funktion.

Der Controller ist der einzige Verbraucher der Input-Schicht, der spaeter
CDJ-Funktionen ausloest. Aktuell tut er nur zwei Dinge:

1. Er nimmt Ereignisse an und protokolliert sie.
2. Er fuehrt eine Tabelle "Control-ID -> Funktion", die vollstaendig leer ist.

Damit ist der Signalweg
``Virtual CDJ -> Input Layer -> CDJ Controller -> CDJ Functions``
schon vorhanden, ohne dass irgendeine Funktion implementiert ist.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from . import controls
from .input_layer import InputLayer
from .model import InputEvent

#: Funktionszuordnung. Bewusst leer - CDJ-Funktionen kommen im naechsten
#: Entwicklungsschritt. ``None`` bedeutet "nicht zugeordnet".
FunctionHandler = Callable[[InputEvent], None]


class CdjController:
    """Platzhalter fuer die spaetere CDJ-Logik."""

    def __init__(self, input_layer: InputLayer, *, history: int = 512) -> None:
        self.input_layer = input_layer
        self.functions: dict[str, FunctionHandler | None] = {
            control_id: None for control_id in controls.INPUT_CONTROLS
        }
        self.history: deque[InputEvent] = deque(maxlen=history)
        self.unhandled_count = 0
        self._unsubscribe = input_layer.subscribe(self.handle_event)

    # ------------------------------------------------------------------

    def handle_event(self, event: InputEvent) -> None:
        self.history.append(event)
        handler = self.functions.get(event.control_id)
        if handler is None:
            self.unhandled_count += 1
            return
        handler(event)

    # ------------------------------------------------------------------

    def bind(self, control_id: str, handler: FunctionHandler) -> None:
        """Funktion zuordnen. Wird erst im naechsten Schritt benutzt."""
        controls.get(control_id)  # validiert die ID
        self.functions[control_id] = handler

    def unbind(self, control_id: str) -> None:
        controls.get(control_id)
        self.functions[control_id] = None

    def assigned_functions(self) -> tuple[str, ...]:
        return tuple(
            cid for cid, handler in self.functions.items() if handler is not None
        )

    def close(self) -> None:
        self._unsubscribe()

    # ------------------------------------------------------------------
    # Zustandsabfragen, die spaetere Funktionen brauchen werden
    # ------------------------------------------------------------------

    def is_pressed(self, control_id: str) -> bool:
        return self.input_layer.state.is_pressed(control_id)

    def chord(self) -> tuple[str, ...]:
        """Aktuell gleichzeitig gedrueckte Bedienelemente."""
        return self.input_layer.state.pressed_ids()
