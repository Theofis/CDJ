"""Sensor-/Scan-Programm des Jogwheels.

Dieses Modul liest die Hardware aus und haelt daraus zwei Werte bereit:

    Touch = 0 / 1
    Jog_Position_Count

Mehr braucht das DJ-Programm nicht zu wissen. Wie die Lichtschranken
ausgewertet und wie der kapazitive Sensor entschieden wird, bleibt hier.

Keine Warteschlange
-------------------
Jeder gueltige Quadratur-Schritt wird **sofort** in den Positionszaehler
eingerechnet - es geht also kein Schritt verloren. Zum DJ-Programm hin gibt
es aber keine Ereignisliste, sondern nur den aktuellen Stand. Dreht sich das
Jogwheel schneller, als die Oberflaeche verarbeitet, liest diese beim
naechsten Mal einfach den neuesten Zaehlerstand und rechnet die gesamte
Bewegung in einem Schritt ab (siehe ``reader.JogReader``).

Der Scan darf in einem eigenen Faden laufen: ``scan`` und ``state``
arbeiten unter derselben Sperre, ein gelesener ``JogState`` ist deshalb
immer in sich stimmig.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .quadrature import STEPS_PER_BEAT, STEPS_PER_REV, QuadratureDecoder
from .touch import DEFAULT_OFF_THRESHOLD, DEFAULT_ON_THRESHOLD, TouchSensor


@dataclass(frozen=True)
class JogState:
    """Der aktuelle Stand des Jogwheels - kein Ereignis.

    Ein ``JogState`` ist eine Momentaufnahme. Zwei aufeinanderfolgende
    Aufnahmen ergeben ueber die Differenz der Position die Bewegung
    dazwischen, egal wie viele Schritte in der Zwischenzeit angefallen sind.
    """

    #: Beruehrungszustand.
    touch: bool = False
    #: Positionszaehler. Zaehlt in beide Richtungen und laeuft nicht um.
    position: int = 0
    #: Letzter Rohwert des kapazitiven Sensors (nur zur Anzeige).
    touch_raw: float = 0.0
    #: Ungueltige Zustandswechsel seit dem Start - sollte 0 bleiben.
    errors: int = 0
    #: Gueltige Schritte insgesamt.
    steps: int = 0
    #: Zeitpunkt der Aufnahme in Sekunden (monotone Uhr).
    timestamp: float = 0.0

    def steps_since(self, position: int) -> int:
        return self.position - position

    def revolutions_since(self, position: int) -> float:
        return self.steps_since(position) / STEPS_PER_REV

    def beats_since(self, position: int) -> float:
        return self.steps_since(position) / STEPS_PER_BEAT


class JogScanner:
    """Liest Lichtschranken und Touch-Sensor und fuehrt den Positionsstand.

    Drei Wege hinein, je nachdem, wie viel das Geraet selbst erledigt:

    * ``scan(a, b, touch_raw=...)`` - Rohpegel beider Lichtschranken und
      Rohwert des Touch-Sensors. Die Auswertung passiert hier.
    * ``scan_touch(raw)`` - nur der kapazitive Sensor.
    * ``set_position(count)`` - fuer Geraete, die selbst quadraturzaehlen
      und nur den fertigen Stand melden.
    """

    def __init__(
        self,
        *,
        touch_on: float = DEFAULT_ON_THRESHOLD,
        touch_off: float | None = DEFAULT_OFF_THRESHOLD,
        position: int = 0,
        invert: bool = False,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        self.touch = TouchSensor(touch_on, touch_off)
        self.decoder = QuadratureDecoder(position=position, invert=invert)
        self._time = time_source
        self._lock = threading.Lock()
        self._timestamp = 0.0

    # ------------------------------------------------------------------
    # Abtastung
    # ------------------------------------------------------------------

    def scan(
        self, a: bool, b: bool, *, touch_raw: float | None = None
    ) -> JogState:
        """Eine Abtastung beider Lichtschranken verarbeiten.

        ``touch_raw`` ist freiwillig: wird es weggelassen, bleibt der
        Touch-Zustand unveraendert. So koennen beide Sensoren mit
        unterschiedlichen Raten abgetastet werden.
        """
        with self._lock:
            self.decoder.update(a, b)
            if touch_raw is not None:
                self.touch.update(touch_raw)
            return self._snapshot()

    def scan_touch(self, raw: float) -> JogState:
        """Nur den kapazitiven Sensor abtasten."""
        with self._lock:
            self.touch.update(raw)
            return self._snapshot()

    def set_position(self, count: int) -> JogState:
        """Positionsstand eines Geraets uebernehmen, das selbst zaehlt."""
        with self._lock:
            self.decoder.position = int(count)
            return self._snapshot()

    def set_touch(self, touched: bool) -> JogState:
        """Fertigen Touch-Zustand uebernehmen (Geraet entscheidet selbst)."""
        with self._lock:
            self.touch.touched = bool(touched)
            return self._snapshot()

    # ------------------------------------------------------------------
    # Ausgang
    # ------------------------------------------------------------------

    @property
    def state(self) -> JogState:
        """Der aktuelle Stand. Immer der neueste, nie ein alter."""
        with self._lock:
            return self._snapshot()

    @property
    def position(self) -> int:
        return self.decoder.position

    @property
    def errors(self) -> int:
        return self.decoder.errors

    def reset(self, position: int = 0) -> JogState:
        with self._lock:
            self.decoder.reset(position)
            self.touch.reset_stats()
            return self._snapshot()

    def _snapshot(self) -> JogState:
        """Nur unter der Sperre aufrufen."""
        self._timestamp = self._time()
        return JogState(
            touch=self.touch.touched,
            position=self.decoder.position,
            touch_raw=self.touch.raw,
            errors=self.decoder.errors,
            steps=self.decoder.steps,
            timestamp=self._timestamp,
        )
