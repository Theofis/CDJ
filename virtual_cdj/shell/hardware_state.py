"""Zentraler Hardware-Zustand.

Die eine Stelle, an der eine Oberflaeche nachsieht, was die Hardware gerade
macht:

    Hardware -> Sensor / Input Engine -> HardwareState -> Anwendung -> GUI

``HardwareState`` rechnet nichts aus und wertet keine Sensoren aus. Es
buendelt, was die Input-Schicht (``core/input_layer.py``) und das
Jog-Scan-Programm (``jog/``) ohnehin schon fuehren, und liefert es in einer
Form, die eine Seite direkt anzeigen kann.

Mehrere Seiten koennen gleichzeitig lesen - jede bekommt denselben Stand.
Beim Jogwheel bekommt jeder Leser seinen **eigenen** Differenzzaehler, damit
sich Anzeige und DJ-Programm nicht gegenseitig die Bewegung wegnehmen.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..core import controls, ids
from ..core.input_layer import InputLayer
from ..core.model import ControlType, Source
from ..jog import STEPS_PER_REV, JogReader, JogScanner, JogState
from .calibration import CalibrationStore

#: Nach dieser Zeit ohne Ereignis gilt eine Quelle als still.
IDLE_AFTER_S = 2.0


@dataclass(frozen=True)
class AnalogReading:
    """Ein analoger Eingang, roh und normiert."""

    control_id: str
    value: float = 0.0
    raw: float | None = None
    calibrated: bool = False

    @property
    def percent(self) -> float:
        return self.value * 100.0


@dataclass(frozen=True)
class JogReading:
    """Alles, was das Jogwheel gerade meldet."""

    touch: bool = False
    touch_raw: float = 0.0
    position: int = 0
    #: Bewegung seit dem letzten Lesen **dieses** Lesers.
    delta: int = 0
    direction: str = "-"
    sensor_a: bool = False
    sensor_b: bool = False
    quadrature: int = 0
    errors: int = 0
    steps: int = 0

    @property
    def revolutions(self) -> float:
        return self.position / STEPS_PER_REV

    @property
    def beats(self) -> float:
        """Bewegung des letzten Lesens in Beats (eine Umdrehung = ein Beat)."""
        return self.delta / STEPS_PER_REV

    @property
    def state_text(self) -> str:
        """Quadraturzustand als zwei Ziffern, A zuerst."""
        return f"{int(self.sensor_a)}{int(self.sensor_b)}"


@dataclass(frozen=True)
class LinkStatus:
    """Kommunikationsstatus der Eingabequellen."""

    source: str = "-"
    events: int = 0
    seconds_since_event: float = 0.0
    hardware_connected: bool = False
    jog_errors: int = 0

    @property
    def is_idle(self) -> bool:
        return self.seconds_since_event > IDLE_AFTER_S

    @property
    def text(self) -> str:
        if not self.events:
            return "keine Eingabe empfangen"
        state = "still" if self.is_idle else "aktiv"
        return f"{self.source} - {state} ({self.events} Ereignisse)"


class HardwareState:
    """Sicht auf alle Ein- und Ausgaenge. Die GUI liest nur hier."""

    def __init__(
        self,
        input_layer: InputLayer,
        *,
        jog: JogScanner | None = None,
        calibration: CalibrationStore | None = None,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        self.input_layer = input_layer
        self.jog_scanner = jog
        self.calibration = calibration
        self._time = time_source
        self._reader = JogReader()
        self._last_jog = JogReading()
        #: Zuletzt gesehene Quelle und Zeitpunkt - fuer den Verbindungsstatus.
        self._last_source: Source | None = None
        self._last_event_at = 0.0
        self._events = 0
        self._hardware_connected = False
        input_layer.subscribe(self._note_event)

    # ------------------------------------------------------------------
    # Ereignisse nur mitzaehlen - der Zustand liegt in der Input-Schicht
    # ------------------------------------------------------------------

    def _note_event(self, event) -> None:
        self._last_source = event.source
        self._last_event_at = self._time()
        self._events += 1
        if event.source is Source.HARDWARE:
            self._hardware_connected = True

    # ------------------------------------------------------------------
    # Taster
    # ------------------------------------------------------------------

    @property
    def state(self):
        """Der rohe Zustandsspeicher der Input-Schicht."""
        return self.input_layer.state

    def button(self, control_id: str) -> bool:
        return self.state.is_pressed(control_id)

    def buttons(self) -> dict[str, bool]:
        """Alle Taster mit ihrem Zustand ``Pressed = 0 / 1``."""
        return {
            control.id: self.state.is_pressed(control.id)
            for control in controls.CONTROL_LIST
            if control.type is ControlType.DIGITAL_BUTTON
        }

    def pressed_ids(self) -> tuple[str, ...]:
        return self.state.pressed_ids()

    # ------------------------------------------------------------------
    # Analoge Eingaenge
    # ------------------------------------------------------------------

    def analog(self, control_id: str) -> AnalogReading:
        calibrated = (
            self.calibration is not None
            and self.calibration.is_measured(control_id)
        )
        return AnalogReading(
            control_id=control_id,
            value=self.state.analog(control_id),
            raw=self.state.raw(control_id),
            calibrated=calibrated,
        )

    def analogs(self) -> dict[str, AnalogReading]:
        return {
            control.id: self.analog(control.id)
            for control in controls.CONTROL_LIST
            if control.type in (
                ControlType.ANALOG_FADER, ControlType.ANALOG_POT
            )
        }

    # ------------------------------------------------------------------
    # Schalter, Encoder, LEDs
    # ------------------------------------------------------------------

    def switches(self) -> dict[str, str]:
        return {
            control.id: self.state.switch(control.id)
            for control in controls.CONTROL_LIST
            if control.type is ControlType.SWITCH
        }

    def encoders(self) -> dict[str, int]:
        return {
            control.id: self.state.encoder_total(control.id)
            for control in controls.CONTROL_LIST
            if control.type is ControlType.ENCODER
        }

    def leds(self) -> dict[str, bool]:
        return self.state.led_states()

    def set_led(self, control_id: str, on: bool) -> None:
        """LED schalten - der Weg der Oberflaeche zur Hardware."""
        self.input_layer.set_led(control_id, on)

    def toggle_led(self, control_id: str) -> bool:
        value = not self.state.led(control_id)
        self.set_led(control_id, value)
        return value

    def all_leds(self, on: bool) -> None:
        for control in controls.CONTROL_LIST:
            if control.has_led:
                self.set_led(control.id, on)

    # ------------------------------------------------------------------
    # Jogwheel
    # ------------------------------------------------------------------

    def read_jog(self) -> JogReading:
        """Jog-Zustand holen und die Bewegung seit dem letzten Lesen bilden.

        Wie im DJ-Programm: es wird immer der **neueste** Zaehlerstand
        gelesen, nie eine Warteschlange abgearbeitet.
        """
        scanner = self.jog_scanner
        if scanner is None:
            self._last_jog = self._jog_from_input_layer()
            return self._last_jog

        state: JogState = scanner.state
        movement = self._reader.read(state)
        quadrature = scanner.decoder.state or 0
        direction = "-"
        if movement.steps > 0:
            direction = "CW"
        elif movement.steps < 0:
            direction = "CCW"
        self._last_jog = JogReading(
            touch=state.touch,
            touch_raw=state.touch_raw,
            position=state.position,
            delta=movement.steps,
            direction=direction,
            sensor_a=bool(quadrature & 0b10),
            sensor_b=bool(quadrature & 0b01),
            quadrature=quadrature,
            errors=state.errors,
            steps=state.steps,
        )
        return self._last_jog

    def _jog_from_input_layer(self) -> JogReading:
        """Ersatzweise aus der Input-Schicht, wenn kein Scanner haengt.

        Am virtuellen Bedienfeld gibt es keine Lichtschranken; die Bewegung
        kommt dort als fertiges Tick-Delta. Angezeigt wird dann, was
        wirklich vorliegt - Sensorpegel bleiben leer.
        """
        jog = self.state.jog(ids.JOG_MOVE)
        position = jog.total
        delta = position - (self._reader.last_position or 0)
        self._reader.last_position = position
        direction = "-"
        if delta > 0:
            direction = "CW"
        elif delta < 0:
            direction = "CCW"
        return JogReading(
            touch=self.state.is_pressed(ids.JOG_TOUCH),
            position=position,
            delta=delta,
            direction=direction,
            steps=abs(position),
        )

    @property
    def jog(self) -> JogReading:
        """Zuletzt gelesener Jog-Zustand, ohne neu zu lesen."""
        return self._last_jog

    # ------------------------------------------------------------------
    # Verbindung
    # ------------------------------------------------------------------

    def link(self) -> LinkStatus:
        since = (
            self._time() - self._last_event_at if self._events else 0.0
        )
        errors = (
            self.jog_scanner.errors if self.jog_scanner is not None else 0
        )
        return LinkStatus(
            source=self._last_source.value if self._last_source else "-",
            events=self._events,
            seconds_since_event=since,
            hardware_connected=self._hardware_connected,
            jog_errors=errors,
        )
