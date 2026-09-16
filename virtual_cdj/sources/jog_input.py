"""Jog-Eingabe - eine Schnittstelle fuer Maus, Touchscreen und ESP32.

Das Problem, das dieses Modul loest
-----------------------------------
Es gab zwei verschiedene Wege in die Jog-Engine:

* **Hardware**: jeder Quadratur-Schritt landet in einem Positionszaehler
  (``jog/scanner.py``), und der Bildtakt holt sich ueber ``JogReader`` die
  gesamte Bewegung seit dem letzten Mal - **ein** Ereignis je Bild.
* **Virtuell**: jede Mausbewegung erzeugte sofort ein eigenes Ereignis.

Gemessen an einer schnellen Drehung ueber ein Bild: 120 Ereignisse auf dem
virtuellen Weg gegen 1 auf dem Hardware-Weg. Das ist genau die
Warteschlange, die es nicht geben darf - die Deck-Engine arbeitete alte
Teilbewegungen einzeln ab, jede mit ihrer eigenen Geschwindigkeit.

Jetzt gehen beide denselben Weg::

    Maus / Touch  -> VirtualJogInput   \\
                                        +-> JogScanner (Positionszaehler)
    ESP32         -> HardwareJogInput  /            |
                                                    | poll() je Bild
                                                    v
                                              JogMovement
                                          (touched, deltaPosition,
                                           direction, speed)
                                                    |
                                                    v
                                              InputLayer
                                                    |
                                          InputMapper -> Deck

Die Deck-Engine sieht keinen Unterschied mehr. Sie bekommt in beiden
Faellen dasselbe ``JOG_MOVE`` mit derselben Bedeutung.

Warum ein Zaehler und kein Ereignisstrom
----------------------------------------
Ein Zaehler kann nicht veralten. Wer ihn seltener liest, bekommt die
Bewegung vollstaendig, aber in einem Stueck - es geht kein Schritt
verloren, und es staut sich nichts auf. Genau das verlangt die Vorgabe:
"aktuelle Position sauber akkumulieren, keine veralteten Speed-/Touch-
Events nachtraeglich abspielen".
"""

from __future__ import annotations

import time
from collections.abc import Callable

from ..core import ids
from ..core.input_layer import InputLayer
from ..core.model import InputEvent, Source
from ..jog import JogMovement, JogReader, JogScanner


class JogInput:
    """Gemeinsame Basis: Positionszaehler lesen und weitergeben.

    Haelt einen ``JogScanner`` (den Stand) und einen ``JogReader`` (die
    Differenz seit dem letzten Lesen). Beide Unterklassen unterscheiden
    sich nur darin, **wer** den Zaehler fuellt.
    """

    def __init__(
        self,
        scanner: JogScanner | None = None,
        *,
        jog_id: str = ids.JOG_MOVE,
        touch_id: str = ids.JOG_TOUCH,
    ) -> None:
        self.scanner = scanner if scanner is not None else JogScanner()
        self.reader = JogReader()
        self.jog_id = jog_id
        self.touch_id = touch_id

    # ------------------------------------------------------------------

    @property
    def state(self):
        """Der aktuelle Stand - immer der neueste."""
        return self.scanner.state

    def poll(self) -> JogMovement:
        """Bewegung seit dem letzten Aufruf. Ohne Bewegung ein leerer Stand."""
        return self.reader.read(self.scanner.state)

    def sync(self) -> None:
        """Stand uebernehmen, ohne Bewegung zu erzeugen.

        Nach einer Pause - etwa waehrend ein Track laedt - verhindert das
        einen Sprung ueber die gesamte Zwischenzeit.
        """
        self.reader.sync(self.scanner.state)

    def emit(
        self, input_layer: InputLayer, source: Source
    ) -> list[InputEvent]:
        """Die Bewegung in die Input-Schicht geben.

        Hoechstens **zwei** Ereignisse je Aufruf: die Beruehrung, falls sie
        sich geaendert hat, und die gesamte Bewegung. Nie eine Reihe alter
        Einzelschritte.

        Der Zeitstempel wird bewusst **nicht** mitgegeben. Die Input-Schicht
        rechnet ihre geglaettete Geschwindigkeit auf ihrer eigenen Uhr
        (``now_ms``, Epoche), der Zaehler misst auf der monotonen Uhr. Zwei
        Zeitbasen zu mischen waere ein stiller Fehler: eine einzige
        Differenz zwischen ihnen ergibt Milliarden Millisekunden und danach
        dauerhaft eine Geschwindigkeit von null. Da im Bildtakt gelesen
        wird, liegen beide Uhren ohnehin um denselben Betrag auseinander.

        Wer die sensornahe Geschwindigkeit braucht, nimmt ``poll()`` und
        ``JogMovement.speed``.
        """
        movement = self.poll()
        events: list[InputEvent] = []
        if movement.touch_changed:
            # Zuerst die Beruehrung: das Bewegungsereignis fuehrt mit, ob
            # gleichzeitig beruehrt wird.
            event = input_layer.jog_touch(
                self.touch_id, movement.touch, source
            )
            if event is not None:
                events.append(event)
        if movement.steps:
            event = input_layer.jog_move(
                self.jog_id, movement.steps, source
            )
            if event is not None:
                events.append(event)
        return events


class VirtualJogInput(JogInput):
    """Maus, Touchscreen oder Stift auf dem virtuellen Jogwheel.

    Das Widget rechnet die Zeigerbewegung in Jog-Schritte um und legt sie
    hier ab. Es erzeugt **kein** Ereignis - das macht erst ``emit()`` im
    Bildtakt. Damit verhaelt sich die Maus wie ein Geraet, das seinen
    Positionszaehler selbst fuehrt (``P``-Zeile im Hardware-Protokoll).
    """

    def __init__(
        self,
        *,
        jog_id: str = ids.JOG_MOVE,
        touch_id: str = ids.JOG_TOUCH,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(
            JogScanner(time_source=time_source),
            jog_id=jog_id,
            touch_id=touch_id,
        )

    # ------------------------------------------------------------------

    @property
    def position(self) -> int:
        """Aufsummierte Schritte. Laeuft nicht um - beliebig viele
        Umdrehungen sind moeglich."""
        return self.scanner.position

    def rotate(self, steps: int) -> None:
        """Jog-Schritte anhaengen. Erzeugt kein Ereignis."""
        if steps:
            self.scanner.set_position(self.scanner.position + int(steps))

    def set_touch(self, touched: bool) -> None:
        """Beruehrungszustand setzen. Erzeugt kein Ereignis.

        Beruehren heisst **nicht** bewegen: wer die Platte anfasst und
        still haelt, erzeugt ``touch=True`` bei ``deltaPosition=0``.
        """
        self.scanner.set_touch(bool(touched))

    def release(self) -> None:
        """Eingabe sicher beenden.

        Wird gerufen, wenn der Zeiger das Jogwheel verlaesst oder ausserhalb
        losgelassen wird. Danach ist die Beruehrung sicher aus; Richtung und
        Geschwindigkeit ergeben sich von selbst zu 0, weil ohne weitere
        Schritte auch keine Bewegung mehr gemeldet wird.
        """
        self.set_touch(False)


class HardwareJogInput(JogInput):
    """Lichtschranken und kapazitiver Sensor eines echten Jogwheels.

    Gefuellt wird der Zaehler vom Geraeteprotokoll
    (``sources/hardware.py``), entweder aus den Rohpegeln der
    Lichtschranken oder aus einem fertigen Positionsstand des Geraets.
    """


__all__ = ["HardwareJogInput", "JogInput", "VirtualJogInput"]
