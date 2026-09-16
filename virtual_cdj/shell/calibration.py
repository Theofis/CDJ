"""Kalibrierwerte: laden, aendern, speichern.

Was hier steht, sind die Zahlen, mit denen aus einem Rohwert ein brauchbarer
Wert wird - Grenzwerte des Touch-Sensors, Endanschlaege und Mittelstellung
von Fadern und Potis, Drehrichtung des Jogwheels.

Gespeichert wird in ``config/calibration.json``. Nach einem Neustart gelten
dieselben Werte wieder.

Wichtig: Die Kalibrierung wird **nicht** in einer GUI-Seite angewandt. Die
Input-Schicht fragt sie beim Normieren, die Jog-Sensorik beim Auswerten. Die
Kalibrierseite aendert nur die Zahlen hier.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..core import controls
from ..core.model import ControlType
from ..jog.touch import DEFAULT_OFF_THRESHOLD, DEFAULT_ON_THRESHOLD

DEFAULT_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "calibration.json"
)

CALIBRATION_VERSION = 1

_NOTE = (
    "Eingemessene Werte der Hardware. Erzeugt und geaendert im "
    "Kalibrierungsbereich der Oberflaeche (Menue -> Kalibrierung). "
    "Fehlt ein Eintrag, gelten die Vorgaben aus virtual_cdj/core/controls.py."
)


@dataclass(frozen=True)
class AnalogCalibration:
    """Endanschlaege und Totzone eines Faders oder Potis."""

    raw_min: float
    raw_max: float
    #: Totzone an den Enden, als Anteil des Wegs (0.0 - 0.4).
    deadzone: float = 0.0
    #: Rohwert der Mittelstellung. ``None`` = Mitte zwischen den Enden.
    #: Sinnvoll bei Potis mit Mittelrastung, etwa dem Tempo-Fader.
    center: float | None = None
    invert: bool = False

    @property
    def span(self) -> float:
        return self.raw_max - self.raw_min

    @property
    def is_usable(self) -> bool:
        """Ob sich damit ueberhaupt normieren laesst."""
        return self.span > 0

    def normalize(self, raw: float) -> float:
        """Rohwert auf 0.0 - 1.0 bringen.

        Die Totzone sorgt dafuer, dass ein Fader die Enden wirklich
        erreicht, auch wenn der ADC kurz davor stehen bleibt. Eine
        eingemessene Mittelstellung wird auf genau 0.5 gelegt, damit ein
        Tempo-Fader in der Rastung auch 0.0 % zeigt.
        """
        if not self.is_usable:
            return 0.0
        value = (float(raw) - self.raw_min) / self.span
        value = _clamp01(value)

        if self.center is not None and self.raw_min < self.center < self.raw_max:
            middle = (self.center - self.raw_min) / self.span
            value = _split_scale(value, middle)

        margin = max(0.0, min(0.4, self.deadzone))
        if margin > 0:
            usable = 1.0 - 2 * margin
            if usable <= 0:
                return 0.0
            value = _clamp01((value - margin) / usable)

        return 1.0 - value if self.invert else value

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "raw_min": self.raw_min,
            "raw_max": self.raw_max,
            "deadzone": self.deadzone,
            "invert": self.invert,
        }
        if self.center is not None:
            data["center"] = self.center
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> AnalogCalibration | None:
        try:
            return cls(
                raw_min=float(data["raw_min"]),
                raw_max=float(data["raw_max"]),
                deadzone=float(data.get("deadzone", 0.0)),
                center=(
                    float(data["center"]) if data.get("center") is not None
                    else None
                ),
                invert=bool(data.get("invert", False)),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @classmethod
    def from_control(cls, control_id: str) -> AnalogCalibration | None:
        """Vorgabe aus der Komponentenliste."""
        control = controls.CONTROLS.get(control_id)
        if control is None:
            return None
        return cls(raw_min=float(control.raw_min), raw_max=float(control.raw_max))


@dataclass(frozen=True)
class TouchCalibration:
    """Grenzwerte des kapazitiven Jog-Sensors."""

    on_threshold: float = DEFAULT_ON_THRESHOLD
    off_threshold: float = DEFAULT_OFF_THRESHOLD

    def to_json(self) -> dict[str, Any]:
        return {
            "on_threshold": self.on_threshold,
            "off_threshold": self.off_threshold,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TouchCalibration:
        return cls(
            on_threshold=float(
                data.get("on_threshold", DEFAULT_ON_THRESHOLD)
            ),
            off_threshold=float(
                data.get("off_threshold", DEFAULT_OFF_THRESHOLD)
            ),
        )


@dataclass(frozen=True)
class JogCalibration:
    """Einbaubedingte Eigenschaften des Jogwheels."""

    invert: bool = False

    def to_json(self) -> dict[str, Any]:
        return {"invert": self.invert}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> JogCalibration:
        return cls(invert=bool(data.get("invert", False)))


class CalibrationStore:
    """Alle Kalibrierwerte an einer Stelle."""

    def __init__(
        self,
        *,
        analog: dict[str, AnalogCalibration] | None = None,
        touch: TouchCalibration | None = None,
        jog: JogCalibration | None = None,
        path: Path | str = DEFAULT_PATH,
    ) -> None:
        self._analog: dict[str, AnalogCalibration] = dict(analog or {})
        self.touch = touch if touch is not None else TouchCalibration()
        self.jog = jog if jog is not None else JogCalibration()
        self.path = Path(path)
        #: Steigt bei jeder Aenderung - die Oberflaeche sieht daran, dass
        #: gespeicherte Werte von den angezeigten abweichen.
        self.revision = 0
        self._saved_revision = 0
        #: Fehlertext, falls die Datei nicht gelesen werden konnte.
        self.load_error = ""

    # ------------------------------------------------------------------
    # Analoge Elemente
    # ------------------------------------------------------------------

    def analog(self, control_id: str) -> AnalogCalibration | None:
        """Eingemessene Werte, sonst die Vorgabe der Komponentenliste."""
        entry = self._analog.get(control_id)
        if entry is not None:
            return entry
        return AnalogCalibration.from_control(control_id)

    def is_measured(self, control_id: str) -> bool:
        """Ob dieser Eingang wirklich eingemessen wurde."""
        return control_id in self._analog

    def set_analog(
        self, control_id: str, calibration: AnalogCalibration
    ) -> None:
        if not calibration.is_usable:
            raise ValueError(
                f"{control_id}: raw_max muss ueber raw_min liegen "
                f"({calibration.raw_max} <= {calibration.raw_min})"
            )
        self._analog[control_id] = calibration
        self._touched()

    def update_analog(self, control_id: str, **changes: Any) -> AnalogCalibration:
        """Einzelne Felder aendern, den Rest behalten."""
        current = self.analog(control_id)
        if current is None:
            raise KeyError(f"kein analoges Element: {control_id}")
        updated = replace(current, **changes)
        self.set_analog(control_id, updated)
        return updated

    def clear_analog(self, control_id: str) -> None:
        """Messung verwerfen - es gilt wieder die Vorgabe."""
        if self._analog.pop(control_id, None) is not None:
            self._touched()

    def measured_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._analog))

    # ------------------------------------------------------------------
    # Sensoren
    # ------------------------------------------------------------------

    def set_touch(self, calibration: TouchCalibration) -> None:
        if calibration.off_threshold > calibration.on_threshold:
            raise ValueError(
                "Touch_OFF_Threshold darf nicht ueber Touch_ON_Threshold "
                "liegen"
            )
        self.touch = calibration
        self._touched()

    def set_jog(self, calibration: JogCalibration) -> None:
        self.jog = calibration
        self._touched()

    # ------------------------------------------------------------------
    # Normierung - hier fragt die Input-Schicht
    # ------------------------------------------------------------------

    def normalize(self, control_id: str, raw: float) -> float | None:
        """Rohwert normieren. ``None``, wenn nichts hinterlegt ist.

        ``None`` heisst ausdruecklich "nicht zustaendig": die Input-Schicht
        rechnet dann wie bisher mit den Vorgaben der Komponentenliste.
        """
        entry = self._analog.get(control_id)
        if entry is None or not entry.is_usable:
            return None
        return entry.normalize(raw)

    # ------------------------------------------------------------------
    # Datei
    # ------------------------------------------------------------------

    @property
    def has_unsaved_changes(self) -> bool:
        return self.revision != self._saved_revision

    def _touched(self) -> None:
        self.revision += 1

    def to_json(self) -> dict[str, Any]:
        return {
            "version": CALIBRATION_VERSION,
            "note": _NOTE,
            "touch": self.touch.to_json(),
            "jog": self.jog.to_json(),
            "analog": {
                control_id: entry.to_json()
                for control_id, entry in sorted(self._analog.items())
            },
        }

    def save(self, path: Path | str | None = None) -> Path:
        target = Path(path) if path is not None else self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_json(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self.path = target
        self._saved_revision = self.revision
        return target

    @classmethod
    def load(cls, path: Path | str = DEFAULT_PATH) -> CalibrationStore:
        """Werte laden. Fehlt oder bricht die Datei, gelten die Vorgaben.

        Eine kaputte Datei darf den Start nicht verhindern - sie wird
        uebergangen, und ``load_error`` sagt, was los war.
        """
        target = Path(path)
        store = cls(path=target)
        if not target.exists():
            return store
        try:
            doc = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            store.load_error = str(error)
            return store
        if not isinstance(doc, dict):
            store.load_error = "unerwarteter Inhalt"
            return store

        touch = doc.get("touch")
        if isinstance(touch, dict):
            store.touch = TouchCalibration.from_json(touch)
        jog = doc.get("jog")
        if isinstance(jog, dict):
            store.jog = JogCalibration.from_json(jog)
        analog = doc.get("analog")
        if isinstance(analog, dict):
            for control_id, value in analog.items():
                if not isinstance(value, dict):
                    continue
                entry = AnalogCalibration.from_json(value)
                if entry is not None and entry.is_usable:
                    store._analog[control_id] = entry
        store._saved_revision = store.revision
        return store


def analog_control_ids() -> tuple[str, ...]:
    """Alle Fader und Potis der Komponentenliste."""
    return tuple(
        control.id
        for control in controls.CONTROL_LIST
        if control.type in (ControlType.ANALOG_FADER, ControlType.ANALOG_POT)
    )


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def _split_scale(value: float, middle: float) -> float:
    """``middle`` auf 0.5 legen, beide Haelften linear dehnen."""
    if middle <= 0.0 or middle >= 1.0:
        return value
    if value <= middle:
        return value / middle * 0.5
    return 0.5 + (value - middle) / (1.0 - middle) * 0.5
