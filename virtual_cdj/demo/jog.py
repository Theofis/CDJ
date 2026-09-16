"""Synthetisches Jog-Signal.

Erzeugt genau das, was die beiden Lichtschranken und der kapazitive Sensor
liefern wuerden - **keine** fertigen Positionen. Damit laesst sich das
Scan-Programm ohne Hardware betreiben und pruefen: es muss aus diesen Pegeln
dieselbe Bewegung ableiten, die hier hineingedreht wurde.

Erkennbar bleibt es daran, dass es hier steht und nicht in ``jog/``.
"""

from __future__ import annotations

import math
import random

from ..jog.quadrature import SLOTS_PER_REV

#: Rohwerte des kapazitiven Sensors in Ruhe und bei Beruehrung. Grob an
#: einem ESP32-Touchpin abgeschaut; die echten Werte kommen aus der
#: Kalibrierung.
IDLE_RAW = 520.0
TOUCHED_RAW = 880.0
NOISE = 12.0


class SimulatedJog:
    """Ein gedachtes Jogwheel: Winkel rein, Sensorpegel raus.

    Der Winkel wird in Umdrehungen gefuehrt. Aus ihm entstehen die beiden
    um eine Viertelperiode versetzten Rechtecksignale des Encoder-Rings.
    """

    def __init__(
        self,
        *,
        slots_per_rev: int = SLOTS_PER_REV,
        seed: int | None = 7,
    ) -> None:
        self.slots_per_rev = slots_per_rev
        self.revolutions = 0.0
        self.touched = False
        self._random = random.Random(seed)

    # ------------------------------------------------------------------

    def turn(self, revolutions: float) -> None:
        """Weiterdrehen. Negativ ist die Gegenrichtung."""
        self.revolutions += revolutions

    def turn_degrees(self, degrees: float) -> None:
        self.turn(degrees / 360.0)

    def touch(self, touched: bool) -> None:
        self.touched = bool(touched)

    # ------------------------------------------------------------------

    @property
    def sensors(self) -> tuple[bool, bool]:
        """Pegel der beiden Lichtschranken beim aktuellen Winkel.

        Beide Rechtecke haben die Periode eines Schlitzes, B ist um eine
        Viertelperiode versetzt - genau das ergibt die Quadratur. Die
        Richtung des Versatzes ist so gewaehlt, dass Weiterdrehen im
        positiven Sinn auch ``+1`` ergibt.
        """
        phase = self.revolutions * self.slots_per_rev
        return (
            _square(phase),
            _square(phase + 0.25),
        )

    @property
    def touch_raw(self) -> float:
        """Rohwert des kapazitiven Sensors samt etwas Rauschen."""
        base = TOUCHED_RAW if self.touched else IDLE_RAW
        return base + self._random.uniform(-NOISE, NOISE)

    def sample(self) -> tuple[bool, bool, float]:
        """Eine Abtastung: ``(A, B, Touch-Rohwert)``."""
        a, b = self.sensors
        return (a, b, self.touch_raw)

    def burst(
        self, revolutions: float, *, samples_per_step: int = 3
    ) -> list[tuple[bool, bool, float]]:
        """Weiterdrehen und dabei abtasten.

        Die Sensorseite tastet viel schneller ab, als eine Oberflaeche
        zeichnet - sonst waeren die Zustandswechsel nicht mehr einzeln
        sichtbar und es gaebe ungueltige Spruenge. ``burst`` liefert die
        Abtastungen, die waehrend dieser Drehung angefallen waeren.
        """
        steps = abs(revolutions) * self.slots_per_rev * 4
        count = max(1, math.ceil(steps * samples_per_step))
        samples = []
        for _ in range(count):
            self.turn(revolutions / count)
            samples.append(self.sample())
        return samples


def _square(phase: float) -> bool:
    """Rechtecksignal: erste Haelfte jeder Periode ist ``True``."""
    return math.floor(phase * 2) % 2 == 0
