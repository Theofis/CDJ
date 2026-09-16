"""Datenmodell der Input-Schicht.

Dieses Modul enthaelt ausschliesslich Typen. Es kennt weder die GUI noch die
Hardware noch irgendeine CDJ-Funktion.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class ControlType(str, Enum):
    """Physikalischer Typ eines Bedienelements."""

    DIGITAL_BUTTON = "DIGITAL_BUTTON"
    ANALOG_FADER = "ANALOG_FADER"
    ANALOG_POT = "ANALOG_POT"
    ENCODER = "ENCODER"
    SWITCH = "SWITCH"
    JOG = "JOG"
    JOYSTICK = "JOYSTICK"
    LED = "LED"  # reines Ausgangselement, kein Input


#: Typen, die als Eingang behandelt werden (alles ausser LED).
INPUT_TYPES = frozenset(t for t in ControlType if t is not ControlType.LED)


class EventType(str, Enum):
    """Art des erzeugten Ereignisses."""

    PRESS = "PRESS"
    RELEASE = "RELEASE"
    VALUE = "VALUE"  # analog, normiert 0.0 - 1.0
    ROTATE = "ROTATE"  # Encoder, relativ
    MOVE = "MOVE"  # Jogwheel, relativ
    POSITION = "POSITION"  # Schalter
    AXIS = "AXIS"  # Joystick


class Direction(str, Enum):
    CW = "CW"
    CCW = "CCW"
    NONE = "NONE"


class Source(str, Enum):
    """Woher kam das Signal? Die CDJ-Logik darf das ignorieren."""

    VIRTUAL = "VIRTUAL"
    HARDWARE = "HARDWARE"
    SCRIPT = "SCRIPT"  # Tests / Automatisierung


class Shape(str, Enum):
    """Nur ein Darstellungshinweis fuer die virtuelle Oberflaeche."""

    ROUND = "ROUND"
    RECT = "RECT"
    SQUARE = "SQUARE"
    BIG_ROUND = "BIG_ROUND"


class Status(str, Enum):
    """Reifegrad eines Eintrags in der Komponentenliste."""

    VIRTUAL = "VIRTUAL"  # im Bild erkannt, Beschriftung gesichert
    UNRESOLVED = "UNRESOLVED"  # im Bild erkannt, Beschriftung/Funktion unklar


# --------------------------------------------------------------------------
# Bedienelement-Definition
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Control:
    """Ein einzelnes Bedienelement der zentralen Komponentenliste."""

    id: str
    label: str
    type: ControlType
    group: str

    #: Kurzform fuer die Beschriftung im virtuellen Bedienfeld. Leer =
    #: es wird die ID angezeigt.
    short: str = ""
    #: Seite, auf der die Beschriftung gezeichnet wird: below/above/left/right.
    label_side: str = "below"

    # Position/Groesse im Panel-Koordinatensystem (entspricht den Pixeln des
    # Referenzbildes; x/y ist die Mitte des Elements).
    x: float = 0.0
    y: float = 0.0
    w: float = 20.0
    h: float = 20.0
    shape: Shape = Shape.ROUND

    has_led: bool = False
    #: Anzahl physischer LED-Punkte dieses Elements. Mehrere LEDs verwenden
    #: denselben logischen Ausgang, bis die echte Hardware getrennte
    #: Rueckmeldekanäle bereitstellt.
    led_count: int = 1
    status: Status = Status.VIRTUAL
    note: str = ""

    # Analoge Elemente: Rohwertbereich der spaeteren Hardware. Wird nur zum
    # Normieren benutzt, die CDJ-Logik sieht ausschliesslich 0.0 - 1.0.
    raw_min: int = 0
    raw_max: int = 4095

    # Analoge Elemente: Startwert (normiert).
    default_value: float = 0.0

    # Encoder: Rastungen pro Umdrehung in der virtuellen Darstellung.
    detents_per_rev: int = 30

    # Jog: Ticks pro Umdrehung (echte optische Encoder liefern deutlich mehr,
    # der Wert ist hier bewusst konfigurierbar).
    ticks_per_rev: int = 360

    # Schalter: moegliche Positionen (Reihenfolge = Reihenfolge am Geraet).
    positions: tuple[str, ...] = ()
    default_position: str = ""

    @property
    def is_input(self) -> bool:
        return self.type in INPUT_TYPES


# --------------------------------------------------------------------------
# Event
# --------------------------------------------------------------------------


def now_ms() -> float:
    """Zeitstempel in Millisekunden (Unix-Epoche, monoton genug fuer Debug)."""
    return time.time() * 1000.0


@dataclass(frozen=True)
class InputEvent:
    """Ein normiertes Eingabeereignis.

    Alle Verbraucher (CDJ-Controller, Debug-Ansicht, Input-Monitor) sehen
    ausschliesslich dieses Objekt. Es ist unabhaengig davon, ob ein Mausklick
    oder ein MCP23017-Pin die Quelle war.
    """

    control_id: str
    control_type: ControlType
    event: EventType
    source: Source
    timestamp: float = field(default_factory=now_ms)

    #: Normierter Wert: 0/1 bei digital, 0.0-1.0 bei analog, sonst None.
    value: float | None = None
    #: Relative Bewegung bei ENCODER / JOG.
    delta: int | None = None
    direction: Direction | None = None
    #: Schalterposition bei SWITCH.
    position: str | None = None
    #: Rohwert der Hardware, falls vorhanden (nur zur Diagnose).
    raw: Any | None = None
    #: Zusatzinformationen, z. B. Jog-Geschwindigkeit.
    meta: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        """Kurzform fuer den Input-Monitor."""
        if self.event in (EventType.PRESS, EventType.RELEASE):
            return self.event.value
        if self.event is EventType.VALUE:
            return f"{self.value:.3f}"
        if self.event in (EventType.ROTATE, EventType.MOVE):
            return f"{self.delta:+d}"
        if self.event is EventType.POSITION:
            return str(self.position)
        if self.event is EventType.AXIS:
            return f"{self.value:.3f}"
        return self.event.value

    def to_dict(self) -> dict[str, Any]:
        d = {
            "control_id": self.control_id,
            "control_type": self.control_type.value,
            "event": self.event.value,
            "source": self.source.value,
            "timestamp": self.timestamp,
        }
        for key in ("value", "delta", "position", "raw"):
            val = getattr(self, key)
            if val is not None:
                d[key] = val
        if self.direction is not None:
            d["direction"] = self.direction.value
        if self.meta:
            d["meta"] = dict(self.meta)
        return d
