"""Zustandsspeicher der Input-Schicht.

Haelt fuer jedes Bedienelement den aktuellen Zustand. Wichtig fuer
Tastenkombinationen: es wird eine Menge gleichzeitig gedrueckter Elemente
gefuehrt, kein Toggle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import controls
from .model import ControlType, Direction


@dataclass
class JogState:
    """Laufender Zustand eines Jogwheels."""

    touched: bool = False
    #: Summe aller Ticks seit Programmstart (nur zur Diagnose).
    total: int = 0
    last_delta: int = 0
    last_direction: Direction = Direction.NONE
    last_timestamp: float = 0.0
    #: Ticks pro Sekunde, geglaettet. Positiv = CW.
    velocity: float = 0.0


class InputState:
    """Aktueller Zustand aller Bedienelemente."""

    def __init__(self) -> None:
        self._pressed: set[str] = set()
        self._analog: dict[str, float] = {}
        self._raw: dict[str, float | int | None] = {}
        self._encoder_total: dict[str, int] = {}
        self._switch: dict[str, str] = {}
        self._jog: dict[str, JogState] = {}
        self._led: dict[str, bool] = {}
        self.reset()

    # -- Initialisierung ---------------------------------------------------

    def reset(self) -> None:
        self._pressed.clear()
        self._analog.clear()
        self._raw.clear()
        self._encoder_total.clear()
        self._switch.clear()
        self._jog.clear()
        self._led.clear()

        for control in controls.CONTROL_LIST:
            if control.type in (ControlType.ANALOG_FADER, ControlType.ANALOG_POT):
                self._analog[control.id] = control.default_value
            elif control.type is ControlType.ENCODER:
                self._encoder_total[control.id] = 0
            elif control.type is ControlType.SWITCH:
                self._switch[control.id] = (
                    control.default_position or (control.positions[0] if control.positions else "")
                )
            elif control.type is ControlType.JOG:
                self._jog[control.id] = JogState()
            if control.has_led:
                self._led[control.id] = False

    # -- Digital -----------------------------------------------------------

    def is_pressed(self, control_id: str) -> bool:
        return control_id in self._pressed

    def pressed_ids(self) -> tuple[str, ...]:
        """Alle aktuell gedrueckten Elemente, stabil sortiert."""
        return tuple(sorted(self._pressed))

    def _set_pressed(self, control_id: str, pressed: bool) -> None:
        if pressed:
            self._pressed.add(control_id)
        else:
            self._pressed.discard(control_id)

    # -- Analog ------------------------------------------------------------

    def analog(self, control_id: str) -> float:
        return self._analog.get(control_id, 0.0)

    def raw(self, control_id: str) -> float | int | None:
        return self._raw.get(control_id)

    # -- Encoder -----------------------------------------------------------

    def encoder_total(self, control_id: str) -> int:
        return self._encoder_total.get(control_id, 0)

    # -- Schalter ----------------------------------------------------------

    def switch(self, control_id: str) -> str:
        return self._switch.get(control_id, "")

    # -- Jog ---------------------------------------------------------------

    def jog(self, control_id: str) -> JogState:
        return self._jog.setdefault(control_id, JogState())

    # -- LED (Ausgang) -----------------------------------------------------

    def led(self, control_id: str) -> bool:
        return self._led.get(control_id, False)

    def _set_led(self, control_id: str, on: bool) -> None:
        self._led[control_id] = on

    def led_states(self) -> dict[str, bool]:
        return dict(self._led)
