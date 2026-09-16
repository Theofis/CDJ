"""Quadraturauswertung der beiden Jog-Lichtschranken.

Das Jogwheel hat zwei versetzt angeordnete Lichtschranken. Aus ihren beiden
Pegeln ergeben sich vier Zustaende; jeder gueltige Wechsel ist genau ein
Positionsschritt.

    Zustand   A   B
    --------------------
       0b10   1   0
       0b00   0   0
       0b01   0   1
       0b11   1   1

Vorwaerts (``+1``)::

    10 -> 00 -> 01 -> 11 -> 10

Rueckwaerts (``-1``)::

    10 -> 11 -> 01 -> 00 -> 10

Ob ``+1`` im Uhrzeigersinn liegt, haengt an der Einbaurichtung der Sensoren.
Umdrehen laesst sich das mit ``QuadratureDecoder(invert=True)``, ohne die
Verkabelung zu tauschen.

Dieses Modul kennt weder Deck noch Oberflaeche. Es zaehlt Schritte.
"""

from __future__ import annotations

#: Schlitze im Encoder-Ring.
SLOTS_PER_REV = 200

#: Auswertbare Flanken je Schlitz - zwei Sensoren, je zwei Flanken.
EDGES_PER_SLOT = 4

#: Positionsschritte je vollstaendiger Umdrehung: 200 x 4.
STEPS_PER_REV = SLOTS_PER_REV * EDGES_PER_SLOT

#: Eine Umdrehung entspricht einem Beat - ein Schritt ist also 1/800 Beat.
#: Die musikalische Bedeutung selbst liegt im Deck, nicht hier.
STEPS_PER_BEAT = STEPS_PER_REV


def state_of(a: bool, b: bool) -> int:
    """Beide Pegel zu einem Zustand 0..3 zusammenfassen.

    Bit 1 ist Lichtschranke A, Bit 0 ist Lichtschranke B.
    """
    return (int(bool(a)) << 1) | int(bool(b))


_FORWARD = ((0b10, 0b00), (0b00, 0b01), (0b01, 0b11), (0b11, 0b10))
_BACKWARD = ((0b10, 0b11), (0b11, 0b01), (0b01, 0b00), (0b00, 0b10))

#: (vorheriger Zustand, neuer Zustand) -> Schritt. Alles, was nicht in
#: dieser Tabelle steht und kein Stillstand ist, ist ein ungueltiger Sprung.
TRANSITIONS: dict[tuple[int, int], int] = {
    **{pair: +1 for pair in _FORWARD},
    **{pair: -1 for pair in _BACKWARD},
}


def step_for(previous: int, current: int) -> int:
    """Schritt eines Zustandswechsels: ``+1``, ``-1`` oder ``0``.

    ``0`` heisst Stillstand **oder** ungueltiger Sprung - beides erzeugt
    keine Bewegung. Ob ein Sprung ungueltig war, sagt ``is_valid``.
    """
    if previous == current:
        return 0
    return TRANSITIONS.get((previous, current), 0)


def is_valid(previous: int, current: int) -> bool:
    """Ob dieser Zustandswechsel ueberhaupt auftreten darf.

    Ungueltig ist der Sprung ueber die Diagonale (``00 <-> 11`` und
    ``01 <-> 10``): dort haetten sich beide Sensoren gleichzeitig geaendert.
    Bei sauberer Abtastung kommt das nicht vor.
    """
    return previous == current or (previous, current) in TRANSITIONS


class QuadratureDecoder:
    """Zaehlt Positionsschritte aus den Pegeln der beiden Lichtschranken.

    Jeder gueltige Wechsel veraendert ``position`` sofort. Es wird nichts
    zwischengespeichert - wer den Stand braucht, liest ``position``.
    """

    def __init__(
        self,
        *,
        position: int = 0,
        invert: bool = False,
    ) -> None:
        #: Aktueller Positionszaehler (``Jog_Position_Count``).
        self.position = int(position)
        #: Zuletzt gesehener Sensorzustand, ``None`` vor der ersten Abtastung.
        self.state: int | None = None
        #: Gueltige Schritte insgesamt - fuer die Diagnose.
        self.steps = 0
        #: Ungueltige Zustandswechsel. Sollte 0 bleiben; ein steigender Wert
        #: heisst schlechte Abtastung, Prellen oder ein defekter Sensor.
        self.errors = 0
        self.invert = bool(invert)

    def update(self, a: bool, b: bool) -> int:
        """Eine Abtastung verarbeiten. Rueckgabe: ``+1``, ``-1`` oder ``0``.

        Die erste Abtastung setzt nur den Startzustand: ohne vorherigen
        Zustand laesst sich keine Richtung bestimmen.
        """
        current = state_of(a, b)
        previous = self.state
        self.state = current
        if previous is None or previous == current:
            return 0

        step = TRANSITIONS.get((previous, current), 0)
        if step == 0:
            self.errors += 1
            return 0
        if self.invert:
            step = -step
        self.position += step
        self.steps += 1
        return step

    def reset(self, position: int = 0) -> None:
        """Zaehler und Statistik zuruecksetzen, Zustand neu einlesen."""
        self.position = int(position)
        self.state = None
        self.steps = 0
        self.errors = 0
