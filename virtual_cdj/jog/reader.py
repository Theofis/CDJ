"""DJ-seitige Auswertung des Jogwheels.

Das DJ-Programm arbeitet **keine** alten Jog-Ereignisse ab. Es liest den
neuesten Positionsstand und rechnet die Differenz zum zuletzt verarbeiteten
Stand::

    Current_Jog_Position = ReadJogPosition()
    Delta_Position       = Current_Jog_Position - Last_Jog_Position
    Playback_Position   += Delta_Position / 800 Beat
    Last_Jog_Position    = Current_Jog_Position

Dreht sich das Jogwheel schneller, als die Oberflaeche verarbeitet, wird
beim naechsten Zugriff die gesamte Bewegung in einem Schritt abgerechnet.
Es kann sich keine Warteschlange veralteter Bewegungsbefehle aufbauen, und
es geht trotzdem kein Schritt verloren - gezaehlt hat sie das
Scan-Programm laengst.
"""

from __future__ import annotations

from dataclasses import dataclass

from .quadrature import STEPS_PER_BEAT, STEPS_PER_REV
from .scanner import JogState

#: Kuerzeste Zeitspanne, aus der eine Geschwindigkeit berechnet wird.
#: Darunter ist der Quotient Rauschen - siehe ``JogMovement.speed``.
MIN_SPEED_INTERVAL_S = 0.001


@dataclass(frozen=True)
class JogMovement:
    """Bewegung seit dem letzten Lesen - der Jog-Eingabezustand.

    Das ist die Schnittstelle, die **jede** Jog-Quelle liefert: das
    virtuelle Jogwheel im Bedienfeld genauso wie spaeter der ESP32. Die
    vier Werte der Vorgabe stehen hier:

        touched         -> ``touch``
        deltaPosition   -> ``steps``
        direction       -> ``direction``   (-1 / 0 / +1)
        speed           -> ``speed``       (Schritte je Sekunde)

    Alles davon ist **relativ zum letzten Lesen**. Es gibt keinen
    Ereignisstrom und keine Warteschlange: wer langsamer liest, bekommt
    beim naechsten Mal die gesamte Bewegung in einem Stueck.
    """

    #: Positionsschritte, mit Vorzeichen (``deltaPosition``).
    steps: int = 0
    #: Umdrehungen. Eine Umdrehung entspricht einem Beat.
    revolutions: float = 0.0
    #: Beruehrungszustand **jetzt** (``touched``).
    touch: bool = False
    #: Ob sich der Beruehrungszustand seit dem letzten Lesen geaendert hat.
    touch_changed: bool = False
    #: Dauer seit dem letzten Lesen in Sekunden. ``0.0``, solange keine
    #: Zeit bekannt ist - dann gibt es auch keine Geschwindigkeit.
    seconds: float = 0.0
    #: Zeitpunkt dieser Aufnahme (monotone Uhr, Sekunden).
    timestamp: float = 0.0

    @property
    def beats(self) -> float:
        """Bewegung in Beats - dasselbe wie ``revolutions``."""
        return self.revolutions

    @property
    def moved(self) -> bool:
        return self.steps != 0

    @property
    def direction(self) -> int:
        """``+1`` vorwaerts, ``-1`` rueckwaerts, ``0`` keine Bewegung.

        Aus **dieser** Bewegung, nicht aus einer Historie: steht das Rad
        still, ist die Richtung 0 und nicht die zuletzt gedrehte.
        """
        if self.steps > 0:
            return 1
        if self.steps < 0:
            return -1
        return 0

    @property
    def speed(self) -> float:
        """Drehgeschwindigkeit in Schritten je Sekunde, mit Vorzeichen.

        ``0.0``, wenn die Zeitspanne fehlt **oder** zu kurz ist, um etwas
        auszusagen: zwei Aufnahmen wenige Mikrosekunden auseinander ergeben
        rechnerisch Millionen Schritte je Sekunde, physikalisch aber nichts.
        Lieber keine Geschwindigkeit als eine erfundene. Im Betrieb wird im
        Bildtakt gelesen (16-40 ms), also weit oberhalb der Schwelle.
        """
        if self.seconds < MIN_SPEED_INTERVAL_S:
            return 0.0
        return self.steps / self.seconds

    @property
    def revolutions_per_second(self) -> float:
        """Dieselbe Geschwindigkeit in Umdrehungen je Sekunde."""
        return self.speed / STEPS_PER_REV

    def __bool__(self) -> bool:
        return self.moved or self.touch_changed


class JogReader:
    """Wandelt aufeinanderfolgende ``JogState`` in Bewegung um.

    Ein Leser je Deck. Er haelt nur den zuletzt verarbeiteten Stand - das
    ist die ganze Zustandshaltung der DJ-Seite.
    """

    def __init__(self, *, steps_per_beat: int = STEPS_PER_BEAT) -> None:
        if steps_per_beat <= 0:
            raise ValueError("steps_per_beat muss positiv sein")
        self.steps_per_beat = int(steps_per_beat)
        #: ``None``, solange noch kein Stand gelesen wurde.
        self.last_position: int | None = None
        self.last_timestamp: float = 0.0
        self.touch = False

    def read(self, state: JogState) -> JogMovement:
        """Neuesten Stand verarbeiten und die Bewegung dazu liefern.

        Der allererste Aufruf uebernimmt den Stand nur: ohne Vorgaenger
        waere die Differenz zum Nullpunkt eine erfundene Bewegung.
        """
        touch_changed = state.touch != self.touch
        self.touch = state.touch

        if self.last_position is None:
            self.last_position = state.position
            self.last_timestamp = state.timestamp
            return JogMovement(
                steps=0,
                revolutions=0.0,
                touch=state.touch,
                touch_changed=touch_changed,
                timestamp=state.timestamp,
            )

        steps = state.position - self.last_position
        seconds = max(0.0, state.timestamp - self.last_timestamp)
        self.last_position = state.position
        self.last_timestamp = state.timestamp
        return JogMovement(
            steps=steps,
            revolutions=steps / self.steps_per_beat,
            touch=state.touch,
            touch_changed=touch_changed,
            seconds=seconds,
            timestamp=state.timestamp,
        )

    def sync(self, state: JogState) -> None:
        """Stand uebernehmen, ohne Bewegung zu erzeugen.

        Nach einer Pause im Verarbeiten - etwa waehrend ein Track laedt -
        verhindert das einen Sprung ueber die gesamte Zwischenzeit.
        """
        self.last_position = state.position
        self.last_timestamp = state.timestamp
        self.touch = state.touch

    def reset(self) -> None:
        self.last_position = None
        self.last_timestamp = 0.0
        self.touch = False


def steps_to_beats(
    steps: int, *, steps_per_beat: int = STEPS_PER_BEAT
) -> float:
    """Positionsschritte in Beats umrechnen."""
    return steps / steps_per_beat


def steps_to_revolutions(steps: int) -> float:
    return steps / STEPS_PER_REV
