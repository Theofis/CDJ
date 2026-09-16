"""Hardware-Quelle - vorbereitet, noch ohne Transport.

Hier wird spaeter der ESP32 angebunden (serielle Schnittstelle, WebSocket
oder UDP). Der Transport fehlt bewusst noch; was schon existiert, ist der
Dekoder, der eine Zeile eines Geraeteprotokolls in Aufrufe der zentralen
Input-Schicht umsetzt.

Dadurch entsteht genau der gewuenschte Weg:

    ESP32 / MCP23017 / ADC / Encoder
        -> HardwareSource (dieses Modul)
        -> derselbe InputLayer
        -> derselbe CdjController

Protokoll (eine Zeile pro Ereignis, Felder durch Leerzeichen getrennt):

    D <CONTROL_ID> <0|1>        digitaler Pegel
    A <CONTROL_ID> <rohwert>    analoger Rohwert, z. B. ADC 0..4095
    E <CONTROL_ID> <delta>      Encoder, relativ
    J <CONTROL_ID> <delta>      Jogwheel, relativ
    T <CONTROL_ID> <0|1>        Jog-Beruehrung
    S <CONTROL_ID> <POSITION>   Schalterposition

Rohdaten des Jogwheels - sie gehen in das Scan-Programm (``jog/``) und
erzeugen **kein** Ereignis (siehe ``poll_jog``):

    Q <CONTROL_ID> <AB>         Pegel beider Lichtschranken, z. B. ``10``
    C <CONTROL_ID> <rohwert>    kapazitiver Sensor, Rohwert
    P <CONTROL_ID> <zaehler>    fertiger Positionszaehler des Geraets

Beispiel:

    D PLAY 1
    A TEMPO_FADER 2048
    J JOG_MOVE -3
    Q JOG_MOVE 10
    C JOG_TOUCH 812
"""

from __future__ import annotations

from ..core import ids
from ..core.hardware_map import HardwareMapping, load as load_mapping
from ..core.input_layer import InputLayer
from ..core.model import InputEvent, Source
from ..jog import JogReader, JogScanner, JogState
from .base import InputSource
from .jog_input import HardwareJogInput


class ProtocolError(ValueError):
    pass


class HardwareSource(InputSource):
    """Uebersetzt Geraetemeldungen in Aufrufe der Input-Schicht."""

    source = Source.HARDWARE

    def __init__(
        self,
        input_layer: InputLayer,
        mapping: HardwareMapping | None = None,
        *,
        jog: JogScanner | None = None,
        jog_id: str = ids.JOG_MOVE,
        jog_touch_id: str = ids.JOG_TOUCH,
    ) -> None:
        super().__init__(input_layer)
        self.mapping = mapping if mapping is not None else load_mapping()
        self._running = False
        #: Jog-Eingabe des Geraets. Dieselbe Schnittstelle wie
        #: ``VirtualSource.jog`` - der Unterschied ist nur, wer den
        #: Positionszaehler fuellt.
        self.jog_input = HardwareJogInput(
            jog if jog is not None else JogScanner(),
            jog_id=jog_id,
            touch_id=jog_touch_id,
        )
        self.jog_id = jog_id
        self.jog_touch_id = jog_touch_id

    @property
    def jog(self) -> JogScanner:
        """Scan-Programm des Jogwheels. Es zaehlt jeden Quadratur-Schritt
        mit; in die Input-Schicht geht erst, was ``poll_jog`` abholt."""
        return self.jog_input.scanner

    @property
    def jog_reader(self) -> JogReader:
        return self.jog_input.reader

    # -- Transport (noch nicht implementiert) ------------------------------

    def start(self) -> None:
        """Platzhalter. Hier wird spaeter der Port geoeffnet."""
        self._running = True

    def stop(self) -> None:
        self._running = False

    # -- Dekoder -----------------------------------------------------------

    def feed_line(self, line: str) -> InputEvent | None:
        """Eine Protokollzeile verarbeiten.

        Rueckgabe: das erzeugte Ereignis, oder ``None`` bei Leer- und
        Kommentarzeilen und bei den Jog-Rohdaten ``Q``, ``C`` und ``P``.
        Die gehen in das Scan-Programm; in die Input-Schicht kommen sie
        gebuendelt ueber ``poll_jog``.
        """
        line = line.strip()
        if not line or line.startswith("#"):
            return None

        parts = line.split()
        if len(parts) != 3:
            raise ProtocolError(f"Erwartet 3 Felder, erhalten: {line!r}")

        kind, control_id, raw = parts
        kind = kind.upper()
        layer = self.input_layer

        if kind in ("Q", "C", "P"):
            self._feed_jog(kind, control_id, raw)
            return None
        if kind == "D":
            return layer.set_digital(
                control_id, _to_bool(raw), self.source, raw=raw
            )
        if kind == "A":
            return layer.set_analog_raw(
                control_id, float(raw), self.source
            )
        if kind == "E":
            return layer.rotate(control_id, int(raw), self.source)
        if kind == "J":
            return layer.jog_move(control_id, int(raw), self.source)
        if kind == "T":
            return layer.jog_touch(control_id, _to_bool(raw), self.source)
        if kind == "S":
            return layer.set_switch(control_id, raw, self.source)

        raise ProtocolError(f"Unbekannter Nachrichtentyp {kind!r} in {line!r}")

    def feed(self, text: str) -> list[InputEvent]:
        """Mehrere Zeilen verarbeiten. Rueckgabe: erzeugte Ereignisse."""
        events: list[InputEvent] = []
        for line in text.splitlines():
            event = self.feed_line(line)
            if event is not None:
                events.append(event)
        return events

    # -- Jogwheel ----------------------------------------------------------

    def _feed_jog(self, kind: str, control_id: str, raw: str) -> JogState:
        """Rohdaten des Jogwheels in das Scan-Programm geben."""
        if kind == "C":
            if control_id != self.jog_touch_id:
                raise ProtocolError(
                    f"C erwartet {self.jog_touch_id}, erhalten {control_id!r}"
                )
            return self.jog.scan_touch(float(raw))

        if control_id != self.jog_id:
            raise ProtocolError(
                f"{kind} erwartet {self.jog_id}, erhalten {control_id!r}"
            )
        if kind == "P":
            return self.jog.set_position(int(raw))
        # Q: beide Lichtschranken als zwei Ziffern, A zuerst.
        if len(raw) != 2 or any(c not in "01" for c in raw):
            raise ProtocolError(
                f"Q erwartet zwei Pegel wie '10', erhalten {raw!r}"
            )
        return self.jog.scan(raw[0] == "1", raw[1] == "1")

    def poll_jog(self) -> list[InputEvent]:
        """Aktuellen Jog-Stand abholen und als Ereignisse weitergeben.

        Wird vom Bildtakt aufgerufen. Es entsteht **ein** Bewegungsereignis
        mit der gesamten Bewegung seit dem letzten Aufruf - niemals eine
        Reihe alter Einzelschritte. Dreht sich das Jogwheel schneller, als
        hier abgeholt wird, ist die Bewegung trotzdem vollstaendig, weil das
        Scan-Programm jeden Schritt gezaehlt hat.
        """
        return self.jog_input.emit(self.input_layer, self.source)


def _to_bool(token: str) -> bool:
    if token in ("1", "true", "TRUE", "on", "ON"):
        return True
    if token in ("0", "false", "FALSE", "off", "OFF"):
        return False
    raise ProtocolError(f"Kein Pegel: {token!r}")
