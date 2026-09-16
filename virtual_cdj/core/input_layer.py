"""Zentrale Input-Schicht.

Jede Eingabe - egal ob Mausklick im virtuellen Bedienfeld oder Pin-Wechsel an
einem MCP23017 - laeuft durch genau diese Klasse. Sie

* prueft die ID gegen die zentrale Komponentenliste,
* normiert Werte (analog immer 0.0 - 1.0),
* fuehrt den Zustand (inkl. gleichzeitig gedrueckter Tasten),
* entprellt logisch (kein doppeltes PRESS, kein RELEASE ohne PRESS),
* erzeugt ein ``InputEvent`` und verteilt es an alle Abonnenten.

Die Input-Schicht ruft selbst keine CDJ-Funktion auf. Sie kennt den
CDJ-Controller nicht - der Controller abonniert sie.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from . import controls
from .model import (
    Control,
    ControlType,
    Direction,
    EventType,
    InputEvent,
    Source,
    now_ms,
)
from .state import InputState

Listener = Callable[[InputEvent], None]

#: Kleinste Aenderung, die bei analogen Elementen ein Event erzeugt.
#: Verhindert, dass ein zitternder ADC die Ereigniskette flutet.
ANALOG_EPSILON = 0.001

#: Zeitkonstante der Jog-Geschwindigkeitsglaettung in Sekunden.
JOG_VELOCITY_TAU = 0.08


@runtime_checkable
class Normalizer(Protocol):
    """Was die Input-Schicht von einer Kalibrierung braucht."""

    def normalize(self, control_id: str, raw: float) -> float | None:
        """Rohwert auf 0.0 - 1.0 bringen. ``None`` = nicht zustaendig."""


class UnknownControlError(KeyError):
    """Eine ID, die nicht in der zentralen Komponentenliste steht."""


class ControlTypeMismatch(TypeError):
    """Eine Eingabeart passt nicht zum Typ des Bedienelements."""


class InputLayer:
    """Einziger Eingang fuer alle Eingabesignale."""

    def __init__(self, *, strict: bool = True) -> None:
        self.state = InputState()
        self._listeners: list[Listener] = []
        self._strict = strict
        self._event_count = 0
        #: Optionale Kalibrierung. Objekt mit
        #: ``normalize(control_id, raw) -> float | None``; ``None`` heisst
        #: "nicht zustaendig", dann gelten die Vorgaben aus ``controls.py``.
        #: Gesetzt wird sie in ``app.py``, nicht von einer Oberflaeche.
        self.calibration: Normalizer | None = None

    # ------------------------------------------------------------------
    # Abonnenten
    # ------------------------------------------------------------------

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Listener anmelden. Rueckgabe: Funktion zum Abmelden."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    @property
    def event_count(self) -> int:
        return self._event_count

    # ------------------------------------------------------------------
    # Digitale Eingaben
    # ------------------------------------------------------------------

    def press(
        self,
        control_id: str,
        source: Source = Source.VIRTUAL,
        *,
        raw: object = None,
        timestamp: float | None = None,
    ) -> InputEvent | None:
        """Taster gedrueckt. Ein bereits gedrueckter Taster erzeugt nichts."""
        control = self._require(control_id, ControlType.DIGITAL_BUTTON)
        if self.state.is_pressed(control_id):
            return None
        self.state._set_pressed(control_id, True)
        return self._emit(
            control,
            EventType.PRESS,
            source,
            value=1,
            raw=raw,
            timestamp=timestamp,
        )

    def release(
        self,
        control_id: str,
        source: Source = Source.VIRTUAL,
        *,
        raw: object = None,
        timestamp: float | None = None,
    ) -> InputEvent | None:
        """Taster losgelassen. Ohne vorheriges PRESS erzeugt das nichts."""
        control = self._require(control_id, ControlType.DIGITAL_BUTTON)
        if not self.state.is_pressed(control_id):
            return None
        self.state._set_pressed(control_id, False)
        return self._emit(
            control,
            EventType.RELEASE,
            source,
            value=0,
            raw=raw,
            timestamp=timestamp,
        )

    def set_digital(
        self,
        control_id: str,
        pressed: bool,
        source: Source = Source.VIRTUAL,
        **kwargs: object,
    ) -> InputEvent | None:
        """Pegel setzen - praktisch fuer Hardware, die Zustaende pollt."""
        if pressed:
            return self.press(control_id, source, **kwargs)  # type: ignore[arg-type]
        return self.release(control_id, source, **kwargs)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Analoge Eingaben
    # ------------------------------------------------------------------

    def set_analog(
        self,
        control_id: str,
        value: float,
        source: Source = Source.VIRTUAL,
        *,
        raw: object = None,
        force: bool = False,
        timestamp: float | None = None,
    ) -> InputEvent | None:
        """Normierten Wert 0.0 - 1.0 setzen."""
        control = self._require(
            control_id, ControlType.ANALOG_FADER, ControlType.ANALOG_POT
        )
        value = _clamp01(float(value))
        previous = self.state.analog(control_id)
        if not force and abs(value - previous) < ANALOG_EPSILON:
            return None
        self.state._analog[control_id] = value
        self.state._raw[control_id] = raw
        return self._emit(
            control,
            EventType.VALUE,
            source,
            value=value,
            raw=raw,
            timestamp=timestamp,
        )

    def set_analog_raw(
        self,
        control_id: str,
        raw_value: float,
        source: Source = Source.HARDWARE,
        **kwargs: object,
    ) -> InputEvent | None:
        """Hardware-Rohwert setzen (z. B. ESP32-ADC 0 - 4095).

        Der Rohwert wird hier normiert und verlaesst diese Schicht nie als
        Rohwert - die CDJ-Logik sieht ausschliesslich 0.0 - 1.0.

        Ist eine Kalibrierung hinterlegt, rechnet sie. Das ist die einzige
        Stelle dafuer: keine Oberflaeche und keine CDJ-Funktion rechnet
        eingemessene Endanschlaege oder Totzonen selbst nach.
        """
        control = self._require(
            control_id, ControlType.ANALOG_FADER, ControlType.ANALOG_POT
        )
        norm: float | None = None
        if self.calibration is not None:
            norm = self.calibration.normalize(control_id, float(raw_value))
        if norm is None:
            span = control.raw_max - control.raw_min
            norm = (
                0.0 if span == 0
                else (float(raw_value) - control.raw_min) / span
            )
        return self.set_analog(
            control_id, norm, source, raw=raw_value, **kwargs  # type: ignore[arg-type]
        )

    # ------------------------------------------------------------------
    # Encoder
    # ------------------------------------------------------------------

    def rotate(
        self,
        control_id: str,
        delta: int,
        source: Source = Source.VIRTUAL,
        *,
        timestamp: float | None = None,
    ) -> InputEvent | None:
        """Relative Encoder-Bewegung. ``delta`` ist die Anzahl Rastungen."""
        control = self._require(control_id, ControlType.ENCODER)
        delta = int(delta)
        if delta == 0:
            return None
        self.state._encoder_total[control_id] = (
            self.state.encoder_total(control_id) + delta
        )
        return self._emit(
            control,
            EventType.ROTATE,
            source,
            delta=delta,
            direction=Direction.CW if delta > 0 else Direction.CCW,
            timestamp=timestamp,
        )

    # ------------------------------------------------------------------
    # Schalter
    # ------------------------------------------------------------------

    def set_switch(
        self,
        control_id: str,
        position: str,
        source: Source = Source.VIRTUAL,
        *,
        force: bool = False,
        timestamp: float | None = None,
    ) -> InputEvent | None:
        control = self._require(control_id, ControlType.SWITCH)
        if control.positions and position not in control.positions:
            raise ValueError(
                f"{control_id}: Position {position!r} nicht in "
                f"{control.positions}"
            )
        if not force and self.state.switch(control_id) == position:
            return None
        self.state._switch[control_id] = position
        index = (
            control.positions.index(position) if control.positions else 0
        )
        return self._emit(
            control,
            EventType.POSITION,
            source,
            value=index,
            position=position,
            timestamp=timestamp,
        )

    # ------------------------------------------------------------------
    # Jogwheel
    # ------------------------------------------------------------------

    def jog_move(
        self,
        control_id: str,
        delta: int,
        source: Source = Source.VIRTUAL,
        *,
        timestamp: float | None = None,
    ) -> InputEvent | None:
        """Relative Jog-Bewegung in Encoder-Ticks.

        Es wird zusaetzlich eine geglaettete Geschwindigkeit in Ticks/s
        mitgeliefert, damit spaeter schnelle Bewegungen und Backspins
        auswertbar sind, ohne dass jeder Verbraucher selbst mitzaehlt.
        """
        control = self._require(control_id, ControlType.JOG)
        delta = int(delta)
        if delta == 0:
            return None

        ts = now_ms() if timestamp is None else timestamp
        jog = self.state.jog(control_id)

        dt = 0.0 if jog.last_timestamp == 0.0 else (ts - jog.last_timestamp) / 1000.0
        if dt > 0:
            instant = delta / dt
            alpha = min(1.0, dt / JOG_VELOCITY_TAU)
            jog.velocity = jog.velocity + alpha * (instant - jog.velocity)
        elif jog.last_timestamp == 0.0:
            jog.velocity = 0.0

        jog.total += delta
        jog.last_delta = delta
        jog.last_direction = Direction.CW if delta > 0 else Direction.CCW
        jog.last_timestamp = ts

        revs_per_sec = (
            jog.velocity / control.ticks_per_rev if control.ticks_per_rev else 0.0
        )
        return self._emit(
            control,
            EventType.MOVE,
            source,
            delta=delta,
            direction=jog.last_direction,
            timestamp=ts,
            meta={
                "velocity_ticks_per_s": round(jog.velocity, 2),
                "rev_per_s": round(revs_per_sec, 4),
                "total_ticks": jog.total,
                "touched": jog.touched,
                # Aufloesung mitgeben: nur damit laesst sich ``delta`` in
                # Umdrehungen umrechnen, ohne die Komponentenliste zu kennen.
                "ticks_per_rev": control.ticks_per_rev,
            },
        )

    def jog_touch(
        self,
        control_id: str,
        touched: bool,
        source: Source = Source.VIRTUAL,
        **kwargs: object,
    ) -> InputEvent | None:
        """Jog-Beruehrung als eigener digitaler Zustand.

        ``control_id`` ist die ID der Touch-Flaeche (z. B. ``JOG_TOUCH``),
        nicht die des Jogwheels.
        """
        event = self.set_digital(control_id, touched, source, **kwargs)
        # Spiegel im Jog-Zustand, damit JOG_MOVE-Events mitteilen koennen,
        # ob gleichzeitig beruehrt wird.
        for jog_id, jog in self.state._jog.items():
            if jog_id.startswith("JOG"):
                jog.touched = self.state.is_pressed(control_id)
        return event

    # ------------------------------------------------------------------
    # Joystick (Widget vorhanden, aktuell kein Element im Bedienfeld)
    # ------------------------------------------------------------------

    def set_axis(
        self,
        control_id: str,
        axis: str,
        value: float,
        source: Source = Source.VIRTUAL,
        *,
        timestamp: float | None = None,
    ) -> InputEvent | None:
        control = self._require(control_id, ControlType.JOYSTICK)
        value = max(-1.0, min(1.0, float(value)))
        return self._emit(
            control,
            EventType.AXIS,
            source,
            value=value,
            timestamp=timestamp,
            meta={"axis": axis},
        )

    # ------------------------------------------------------------------
    # LED-Ausgang (Gegenrichtung, gleiche Abstraktion)
    # ------------------------------------------------------------------

    def set_led(self, control_id: str, on: bool) -> None:
        """LED-Zustand setzen. Wird von der Oberflaeche gespiegelt."""
        control = controls.get(control_id)
        if not control.has_led:
            raise ValueError(f"{control_id} besitzt keine LED")
        self.state._set_led(control_id, bool(on))

    # ------------------------------------------------------------------
    # Intern
    # ------------------------------------------------------------------

    def _require(self, control_id: str, *expected: ControlType) -> Control:
        try:
            control = controls.CONTROLS[control_id]
        except KeyError:
            raise UnknownControlError(
                f"Unbekannte Control-ID {control_id!r}. Bedienelemente muessen "
                "in virtual_cdj/core/controls.py definiert sein."
            ) from None
        if self._strict and expected and control.type not in expected:
            raise ControlTypeMismatch(
                f"{control_id} ist {control.type.value}, erwartet wurde "
                f"{' oder '.join(t.value for t in expected)}."
            )
        return control

    def _emit(
        self,
        control: Control,
        event_type: EventType,
        source: Source,
        *,
        value: float | None = None,
        delta: int | None = None,
        direction: Direction | None = None,
        position: str | None = None,
        raw: object = None,
        timestamp: float | None = None,
        meta: dict[str, object] | None = None,
    ) -> InputEvent:
        event = InputEvent(
            control_id=control.id,
            control_type=control.type,
            event=event_type,
            source=source,
            timestamp=now_ms() if timestamp is None else timestamp,
            value=value,
            delta=delta,
            direction=direction,
            position=position,
            raw=raw,
            meta=dict(meta or {}),
        )
        self._event_count += 1
        # Zustand ist zu diesem Zeitpunkt bereits aktualisiert, damit ein
        # Listener bei einer Tastenkombination den vollstaendigen Zustand sieht.
        for listener in list(self._listeners):
            listener(event)
        return event


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
