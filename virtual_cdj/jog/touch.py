"""Auswertung des kapazitiven Jog-Sensors.

Der Sensor liefert einen Rohwert. Daraus wird ein einziger Zustand:

    Touch = 1   Jogwheel wird beruehrt
    Touch = 0   Jogwheel wird nicht beruehrt

Mit zwei Schwellen (Hysterese) springt der Zustand bei kleinen Schwankungen
um den Grenzwert nicht staendig hin und her::

    Touch == 0 und Raw > Touch_ON_Threshold   -> Touch = 1
    Touch == 1 und Raw < Touch_OFF_Threshold  -> Touch = 0

Ein einzelner Grenzwert (``off_threshold`` weggelassen) ergibt das einfache
Verhalten ``Raw > Threshold``.

Die Kennwerte (Minimum, Maximum, Durchschnitt) und der Verlauf werden hier
gefuehrt, damit das Kalibrierungsprogramm sie nur noch anzeigen muss.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

#: Startwert des Grenzwerts, bis er am echten Sensor eingemessen ist.
DEFAULT_ON_THRESHOLD = 700.0
DEFAULT_OFF_THRESHOLD = 650.0

#: Laenge des mitgefuehrten Verlaufs (Punkte im Diagramm).
DEFAULT_HISTORY = 300


@dataclass(frozen=True)
class TouchStats:
    """Kennwerte des Rohsignals seit dem letzten Zuruecksetzen."""

    samples: int = 0
    last: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    average: float | None = None

    @property
    def span(self) -> float:
        """Abstand zwischen Minimum und Maximum. ``0`` ohne Messwerte."""
        if self.minimum is None or self.maximum is None:
            return 0.0
        return self.maximum - self.minimum

    @property
    def has_samples(self) -> bool:
        return self.samples > 0


class TouchSensor:
    """Rohwert -> Touch-Zustand, samt Kennwerten fuer die Kalibrierung."""

    def __init__(
        self,
        on_threshold: float = DEFAULT_ON_THRESHOLD,
        off_threshold: float | None = None,
        *,
        history: int = DEFAULT_HISTORY,
    ) -> None:
        """Ohne ``off_threshold`` gibt es keine Hysterese."""
        self.touched = False
        self.raw = 0.0
        self._history: deque[float] = deque(maxlen=max(1, history))
        self._samples = 0
        self._sum = 0.0
        self._min: float | None = None
        self._max: float | None = None
        self.set_thresholds(on_threshold, off_threshold)

    # ------------------------------------------------------------------
    # Grenzwerte
    # ------------------------------------------------------------------

    def set_thresholds(
        self, on_threshold: float, off_threshold: float | None = None
    ) -> None:
        """Grenzwerte setzen. Ohne ``off_threshold`` gibt es keine Hysterese.

        Raises:
            ValueError: wenn der Ausschaltwert ueber dem Einschaltwert
                liegt - damit waere der Zustand nicht mehr eindeutig.
        """
        on_value = float(on_threshold)
        off_value = on_value if off_threshold is None else float(off_threshold)
        if off_value > on_value:
            raise ValueError(
                "Touch_OFF_Threshold darf nicht ueber Touch_ON_Threshold "
                f"liegen ({off_value} > {on_value})."
            )
        self.on_threshold = on_value
        self.off_threshold = off_value

    @property
    def has_hysteresis(self) -> bool:
        return self.off_threshold < self.on_threshold

    # ------------------------------------------------------------------
    # Auswertung
    # ------------------------------------------------------------------

    def update(self, raw: float) -> bool:
        """Einen Rohwert verarbeiten. Rueckgabe: der Touch-Zustand."""
        value = float(raw)
        self.raw = value
        self._history.append(value)
        self._samples += 1
        self._sum += value
        self._min = value if self._min is None else min(self._min, value)
        self._max = value if self._max is None else max(self._max, value)

        if self.touched:
            if value < self.off_threshold:
                self.touched = False
        elif value > self.on_threshold:
            self.touched = True
        return self.touched

    # ------------------------------------------------------------------
    # Kalibrierung
    # ------------------------------------------------------------------

    @property
    def stats(self) -> TouchStats:
        average = self._sum / self._samples if self._samples else None
        return TouchStats(
            samples=self._samples,
            last=self.raw,
            minimum=self._min,
            maximum=self._max,
            average=average,
        )

    @property
    def history(self) -> tuple[float, ...]:
        """Verlauf des Rohwerts, aeltester Wert zuerst."""
        return tuple(self._history)

    def suggested_thresholds(
        self, *, margin: float = 0.2, min_relative_span: float = 0.1
    ) -> tuple[float, float] | None:
        """Vorschlag aus den gemessenen Werten: ``(ein, aus)``.

        Nimmt die Mitte zwischen Minimum und Maximum und legt die beiden
        Schwellen um ``margin`` der Spanne darum.

        ``None``, solange die Spanne kleiner ist als ``min_relative_span``
        des groessten Messwerts. Dann wurde nur Rauschen gemessen, nicht der
        Unterschied zwischen beruehrt und nicht beruehrt - ein Grenzwert
        mitten im Rauschen waere schlimmer als gar keiner. Es muss also
        waehrend der Messung einmal beruehrt **und** losgelassen werden.
        """
        stats = self.stats
        if stats.minimum is None or stats.maximum is None:
            return None
        span = stats.span
        if span <= 0:
            return None
        scale = max(abs(stats.maximum), abs(stats.minimum))
        if scale > 0 and span < scale * min_relative_span:
            return None
        middle = (stats.minimum + stats.maximum) / 2
        offset = span * margin / 2
        return (middle + offset, middle - offset)

    def reset_stats(self) -> None:
        """Kennwerte und Verlauf verwerfen. Der Zustand bleibt."""
        self._history.clear()
        self._samples = 0
        self._sum = 0.0
        self._min = None
        self._max = None
