"""Virtuelle Quelle - die Bedienoberflaeche.

Die Widgets rufen ausschliesslich diese Klasse auf, niemals den Controller
und niemals eine CDJ-Funktion. Damit ist der Mausklick technisch
gleichwertig zum spaeteren Hardware-Taster.
"""

from __future__ import annotations

from ..core.input_layer import InputLayer
from ..core.model import InputEvent, Source
from .base import InputSource
from .jog_input import VirtualJogInput


class VirtualSource(InputSource):
    source = Source.VIRTUAL

    def __init__(self, input_layer: InputLayer) -> None:
        super().__init__(input_layer)
        self._running = False
        #: Jog-Eingabe von Maus, Touchscreen oder Stift. Sie sammelt die
        #: Bewegung in einem Positionszaehler; in die Input-Schicht geht
        #: erst, was ``poll_jog()`` im Bildtakt abholt - genau wie beim
        #: echten Jogwheel.
        self.jog = VirtualJogInput()

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    # -- Von den Widgets benutzte Methoden ---------------------------------

    def press(self, control_id: str) -> InputEvent | None:
        return self.input_layer.press(control_id, self.source)

    def release(self, control_id: str) -> InputEvent | None:
        return self.input_layer.release(control_id, self.source)

    def set_analog(self, control_id: str, value: float) -> InputEvent | None:
        return self.input_layer.set_analog(control_id, value, self.source)

    def rotate(self, control_id: str, delta: int) -> InputEvent | None:
        return self.input_layer.rotate(control_id, delta, self.source)

    def set_switch(self, control_id: str, position: str) -> InputEvent | None:
        return self.input_layer.set_switch(control_id, position, self.source)

    # -- Jogwheel ----------------------------------------------------------
    #
    # Zwei Ebenen, beide ueber denselben Positionszaehler:
    #
    #   jog_rotate() / jog_set_touch()  sammeln nur - fuer die Oberflaeche,
    #                                   die sehr viele Zeigerereignisse
    #                                   liefert
    #   poll_jog()                      gibt die gesammelte Bewegung im
    #                                   Bildtakt weiter
    #   jog_move() / jog_touch()        sammeln **und** geben sofort weiter -
    #                                   fuer Tests und Skripte

    def jog_rotate(self, steps: int) -> None:
        """Jog-Schritte anhaengen, ohne ein Ereignis zu erzeugen."""
        self.jog.rotate(steps)

    def jog_set_touch(self, touched: bool) -> None:
        """Beruehrung setzen, ohne ein Ereignis zu erzeugen."""
        self.jog.set_touch(touched)

    def poll_jog(self) -> list[InputEvent]:
        """Gesammelte Jog-Bewegung weitergeben. Aus dem Bildtakt aufrufen.

        Hoechstens zwei Ereignisse je Aufruf - dieselbe Zusicherung wie
        ``HardwareSource.poll_jog()``.
        """
        return self.jog.emit(self.input_layer, self.source)

    def jog_move(self, control_id: str, delta: int) -> InputEvent | None:
        """Bewegung sofort melden - fuer Tests und Skripte.

        Geht durch denselben Zaehler wie die Oberflaeche, damit es keine
        zweite Zaehlung gibt.
        """
        if control_id != self.jog.jog_id:
            return self.input_layer.jog_move(control_id, delta, self.source)
        self.jog.rotate(delta)
        events = self.poll_jog()
        for event in reversed(events):
            if event.control_id == control_id:
                return event
        return None

    def jog_touch(self, control_id: str, touched: bool) -> InputEvent | None:
        """Beruehrung sofort melden - fuer Tests und Skripte."""
        if control_id != self.jog.touch_id:
            return self.input_layer.jog_touch(control_id, touched, self.source)
        self.jog.set_touch(touched)
        events = self.poll_jog()
        for event in events:
            if event.control_id == control_id:
                return event
        return None

    def set_axis(self, control_id: str, axis: str, value: float) -> InputEvent | None:
        return self.input_layer.set_axis(control_id, axis, value, self.source)
