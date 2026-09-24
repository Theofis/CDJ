"""Input-Mapping: Control-ID -> Deck-Kommando.

Diese Schicht ist die einzige Stelle, an der Bedienelemente und CDJ-Absichten
aufeinandertreffen. Sie kennt keine GPIOs und keine Deck-Engine-Interna.

Damit gilt fuer jede Signalquelle derselbe Weg:

    ESP32 / MIDI / Tastatur / Maus / Netzwerk
        -> InputLayer   (bereits vorhanden)
        -> InputMapper  (dieses Modul)
        -> DeckCommand
        -> Deck
"""

from __future__ import annotations

from collections.abc import Callable

from ..core import ids
from ..core.model import LONG_PRESS_S, EventType, InputEvent
from ..jog import STEPS_PER_REV
from .commands import CommandType, DeckCommand, Views, command
from .library import PLAYLIST_CATEGORY, TAG_LIST_CATEGORY
from .state import Direction, PadMode

CommandSink = Callable[[DeckCommand], None]


#: Taster, die nur beim Druecken ein Kommando erzeugen (Flankenausloeser).
_ON_PRESS: dict[str, tuple[CommandType, dict[str, object]]] = {
    ids.PLAY: (CommandType.PLAY_PAUSE, {}),
    ids.LOOP_IN: (CommandType.LOOP_IN, {}),
    ids.LOOP_OUT: (CommandType.LOOP_OUT, {}),
    ids.RELOOP_EXIT: (CommandType.RELOOP_EXIT, {}),
    # Doppelbeschriftung am Geraet: ohne laufenden Loop 4 bzw. 8 Beats,
    # mit laufendem Loop 1/2X bzw. 2X.
    ids.BEAT_LOOP_4: (CommandType.BEAT_LOOP, {"beats": 4.0, "scale": 0.5}),
    ids.BEAT_LOOP_8: (CommandType.BEAT_LOOP, {"beats": 8.0, "scale": 2.0}),
    ids.MEMORY: (CommandType.MEMORY, {}),
    ids.BEAT_SYNC: (CommandType.SYNC_TOGGLE, {}),
    ids.MASTER: (CommandType.MASTER_SET, {}),
    ids.KEY_SYNC: (CommandType.KEY_SYNC, {}),
    ids.QUANTIZE: (CommandType.QUANTIZE_TOGGLE, {}),
    ids.SLIP: (CommandType.SLIP_TOGGLE, {}),
    ids.TEMPO_RANGE: (CommandType.TEMPO_RANGE_CYCLE, {}),
    ids.MASTER_TEMPO: (CommandType.MASTER_TEMPO_TOGGLE, {}),
    ids.TEMPO_RESET: (CommandType.TEMPO_RESET_TOGGLE, {}),
    ids.JOG_MODE: (CommandType.JOG_MODE_TOGGLE, {}),
    ids.USB_STOP: (CommandType.USB_STOP, {}),
    ids.BEAT_JUMP_PREV: (CommandType.BEAT_JUMP, {"direction": -1}),
    ids.BEAT_JUMP_NEXT: (CommandType.BEAT_JUMP, {"direction": +1}),
    ids.CUE_LOOP_CALL_PREV: (CommandType.CUE_LOOP_CALL, {"direction": -1}),
    ids.CUE_LOOP_CALL_NEXT: (CommandType.CUE_LOOP_CALL, {"direction": +1}),
    # Die beiden Modustaster in der Loop-Reihe schalten die Pad-Belegung
    # um. Mehr tun sie nicht: welche Funktion ein Pad dann ausloest,
    # entscheidet allein ``Deck._cmd_pad`` anhand von ``pad_mode``. Die
    # Pads selbst senden unveraendert nur ``PAD`` mit ihrem Index.
    ids.HOT_CUE: (CommandType.PAD_MODE, {"mode": PadMode.HOT_CUE}),
    ids.BEAT_JUMP: (CommandType.PAD_MODE, {"mode": PadMode.BEAT_LOOP}),
    # Bildschirmtasten oberhalb des Displays (Handbuch S. 15).
    #
    # SOURCE zeigt die Quellenauswahl (S. 18/36), BROWSE die Trackliste
    # (S. 19). MENU zeigt am Geraet auf dem Wellenform-Bildschirm die zuletzt
    # gespielten Tracks (S. 37) - das ist hier die HISTORY-Kategorie des
    # Browsers. SEARCH oeffnet vorerst ebenfalls den Durchsuchen-Bildschirm,
    # weil der Suchbildschirm mit Tastatur (S. 37) noch fehlt.
    ids.SOURCE: (CommandType.VIEW, {"view": Views.SOURCE}),
    # ``path: ()`` heisst "die Bibliothek selbst": zurueck in den
    # BROWSE-Einstieg, an die Stelle, an der man dort zuletzt war.
    ids.BROWSE: (CommandType.VIEW, {"view": Views.BROWSE, "path": ()}),
    ids.SEARCH: (CommandType.VIEW, {"view": Views.BROWSE, "path": ()}),
    ids.MENU: (CommandType.MENU, {}),
    # PLAYLIST und TAG LIST fuehren in dieselbe Durchsuchen-Ansicht, nur in
    # eine andere Kategorie - wie am Geraet. Kein eigener Bildschirm, keine
    # zweite Trackliste.
    ids.PLAYLIST: (
        CommandType.VIEW,
        {"view": Views.BROWSE, "path": (PLAYLIST_CATEGORY,)},
    ),
    ids.TAG_LIST: (
        CommandType.VIEW,
        {"view": Views.BROWSE, "path": (TAG_LIST_CATEGORY,)},
    ),
    ids.TAG_TRACK_REMOVE: (CommandType.TAG_TRACK_TOGGLE, {}),
}

#: Taster, deren Halten und Loslassen beide relevant sind.
_MOMENTARY: dict[str, tuple[CommandType, dict[str, object]]] = {
    ids.CUE: (CommandType.CUE, {}),
    ids.DELETE: (CommandType.DELETE, {}),
    ids.SEARCH_BACK: (CommandType.SEARCH, {"direction": -1}),
    ids.SEARCH_FWD: (CommandType.SEARCH, {"direction": +1}),
    ids.JOG_TOUCH: (CommandType.JOG_TOUCH, {}),
    # TRACK SEARCH meldet auch das Loslassen: gehalten blaettert das
    # Jogwheel schnell durch die Liste (Abschnitt 7). Der eigentliche
    # Sprung passiert weiterhin beim Druecken.
    ids.TRACK_SEARCH_PREV: (CommandType.TRACK_SEARCH, {"direction": -1}),
    ids.TRACK_SEARCH_NEXT: (CommandType.TRACK_SEARCH, {"direction": +1}),
    # Drehgeber und BACK melden Druecken **und** Loslassen: nur so kann der
    # Bildschirm kurzes von langem Druecken unterscheiden (S. 22, 25, 38).
    ids.BROWSE_PRESS: (CommandType.BROWSE_PRESS, {}),
    ids.BACK: (CommandType.BACK, {}),
}

#: Performance Pads -> Index 0..7.
_PAD_INDEX: dict[str, int] = {pad: i for i, pad in enumerate(ids.PADS)}

#: Bedienelemente, die bewusst kein Kommando erzeugen.
#:
#: ``SHIFT`` ist ein Modifikator - sein Zustand wird gelesen, nicht gesendet.
UNMAPPED: frozenset[str] = frozenset({
    ids.SHIFT,
    # Physisch bestaetigt; die zugehoerigen Bildschirmfunktionen folgen in
    # einem spaeteren Schritt. Sie erzeugen weiterhin normale InputEvents.
    ids.TRACK_FILTER_EDIT,
    ids.SHORTCUT,
})


class InputMapper:
    """Uebersetzt Eingabeereignisse eines Bedienfelds in Deck-Kommandos."""

    def __init__(self, deck_id: int, sink: CommandSink) -> None:
        self.deck_id = deck_id
        self.sink = sink
        self.shift = False
        #: Zeitstempel des laufenden TIME-MODE-Drucks in Millisekunden.
        #: ``None`` heisst: die Taste ist nicht gedrueckt.
        self._time_mode_pressed_ms: float | None = None
        #: IDs, fuer die kein Mapping existiert - fuer die Diagnose.
        self.unhandled: set[str] = set()

    # ------------------------------------------------------------------

    def handle_event(self, event: InputEvent) -> DeckCommand | None:
        """Ein Eingabeereignis verarbeiten. Rueckgabe: erzeugtes Kommando."""
        control_id = event.control_id

        if control_id == ids.SHIFT:
            self.shift = event.event is EventType.PRESS
            return None

        if control_id == ids.TIME_MODE:
            # Eigener Weg, weil das Druecken hier absichtlich noch kein
            # Kommando ergibt - sonst landete die Taste in ``unhandled``.
            cmd = self._time_mode(event)
            if cmd is not None:
                self.sink(cmd)
            return cmd

        if control_id in UNMAPPED:
            return None

        cmd = self._translate(event)
        if cmd is None:
            if control_id not in _PAD_INDEX and event.event in (
                EventType.PRESS, EventType.RELEASE
            ):
                self.unhandled.add(control_id)
            return None

        self.sink(cmd)
        return cmd

    def reset_modifiers(self) -> None:
        """Gehaltene Mapping-Modifikatoren nach einem Seitenwechsel loesen."""
        self.shift = False
        # Wird die Release-Flanke durch den Seitenwechsel verschluckt, darf
        # kein halber Druck stehen bleiben, der beim naechsten Loslassen
        # als sehr langes Halten gilt.
        self._time_mode_pressed_ms = None

    def _time_mode(self, event: InputEvent) -> DeckCommand | None:
        """TIME MODE / AUTO CUE - eine Taste, zwei Haltedauern.

        kurz gedrueckt -> ``TIME_MODE``  (Zeitanzeige, Bildschirmbefehl)
        lang gedrueckt -> ``AUTO_CUE``   (Deckzustand)

        Entschieden wird beim **Loslassen**, aus der Differenz der
        Ereigniszeitstempel. Daraus folgt zweierlei, und beides ist so
        gewollt:

        * Es entsteht genau **ein** Kommando je Druck. Ein langer Druck
          kann deshalb nicht zusaetzlich die Zeitanzeige umschalten.
        * Es laeuft kein Timer und kein Nebenlaeufer. Die Zeitstempel sind
          ohnehin an jedem Ereignis dran (``InputEvent.timestamp``, in
          Millisekunden); mehr braucht es nicht, und ein Test kann sie
          einsetzen, statt zu warten.
        """
        if event.event is EventType.PRESS:
            self._time_mode_pressed_ms = event.timestamp
            return None
        if event.event is not EventType.RELEASE:
            return None
        started = self._time_mode_pressed_ms
        self._time_mode_pressed_ms = None
        if started is None:
            # Loslassen ohne Druecken - z. B. nach einem Seitenwechsel.
            return None
        held_s = max(0.0, (event.timestamp - started) / 1000.0)
        command_type = (
            CommandType.AUTO_CUE if held_s >= LONG_PRESS_S
            else CommandType.TIME_MODE
        )
        return command(
            command_type,
            self.deck_id,
            event.source.value,
            control_id=event.control_id,
            held=held_s >= LONG_PRESS_S,
        )

    # ------------------------------------------------------------------

    def _translate(self, event: InputEvent) -> DeckCommand | None:
        control_id = event.control_id
        origin = event.source.value

        def mapped(command_type: CommandType, **params: object) -> DeckCommand:
            return command(
                command_type,
                self.deck_id,
                origin,
                control_id=control_id,
                **params,
            )

        # SHIFT + MENU oeffnet die persoenlichen Einstellungen. Das ist eine
        # Shell-Absicht, keine Deck- oder Backendentscheidung.
        if (
            control_id == ids.MENU
            and event.event is EventType.PRESS
            and self.shift
        ):
            return mapped(CommandType.OPEN_SETTINGS, shift=True)

        # SHIFT + CALL/DELETE loescht den ueber CUE/LOOP CALL angewaehlten
        # Memory Cue bzw. Memory Loop - unabhaengig davon, wie die Taste
        # sonst gehalten oder kurz gedrueckt wird.
        #
        # Warum es diesen zweiten Weg gibt: am Geraet tragen Hotcue-Loeschen
        # und Memory-Loeschen dieselbe Taste, unterschieden nur durch den
        # Zusammenhang. Hier sind es zwei Kommandos, und dieses hier ist der
        # eindeutige Weg zum Memory-Loeschen - ohne Abhaengigkeit davon, was
        # vorher passiert ist. Derselbe Gedanke wie bei SHIFT + QUANTIZE
        # unten: ein zentral gefuehrter Vorgang braucht eine Tuer, die nicht
        # vom Zufall abhaengt.
        if (
            control_id == ids.DELETE
            and event.event is EventType.PRESS
            and self.shift
        ):
            return mapped(CommandType.MEMORY_DELETE, shift=True)

        # SHIFT + QUANTIZE schaltet die Rasterweite weiter (1/8, 1/4, 1/2,
        # 1 Beat). Am CDJ-3000 sitzt dieser Wert in UTILITY/SHORTCUT; die
        # Seiten dafuer gibt es hier noch nicht, der Wert ist aber schon
        # zentral gefuehrt und soll auch ohne Bildschirm erreichbar sein.
        if (
            control_id == ids.QUANTIZE
            and event.event is EventType.PRESS
            and self.shift
        ):
            return mapped(CommandType.QUANTIZE_BEATS, shift=True)

        # -- Pads ---------------------------------------------------------
        index = _PAD_INDEX.get(control_id)
        if index is not None and event.event in (
            EventType.PRESS, EventType.RELEASE
        ):
            return mapped(
                CommandType.PAD,
                index=index,
                pressed=event.event is EventType.PRESS,
                shift=self.shift,
            )

        # -- Momentan-Taster ----------------------------------------------
        entry = _MOMENTARY.get(control_id)
        if entry is not None and event.event in (
            EventType.PRESS, EventType.RELEASE
        ):
            command_type, params = entry
            return mapped(
                command_type,
                pressed=event.event is EventType.PRESS,
                shift=self.shift,
                **params,
            )

        # -- Flankenausloeser ---------------------------------------------
        entry = _ON_PRESS.get(control_id)
        if entry is not None and event.event is EventType.PRESS:
            command_type, params = entry
            return mapped(command_type, shift=self.shift, **params)
        if entry is not None:
            return None

        # -- Analog --------------------------------------------------------
        if event.event is EventType.VALUE:
            if control_id == ids.TEMPO_FADER:
                return mapped(CommandType.TEMPO_SET, value=event.value)
            if control_id == ids.VINYL_SPEED_ADJUST:
                return mapped(
                    CommandType.VINYL_SPEED_ADJUST, value=event.value
                )
            return None

        # -- Encoder -------------------------------------------------------
        if event.event is EventType.ROTATE and control_id == ids.BROWSE_ROTATE:
            return mapped(
                CommandType.BROWSE_ROTATE,
                delta=event.delta, shift=self.shift,
            )

        # -- Jogwheel ------------------------------------------------------
        if event.event is EventType.MOVE and control_id == ids.JOG_MOVE:
            return mapped(
                CommandType.JOG_MOVE,
                delta=event.delta,
                velocity=event.meta.get("velocity_ticks_per_s", 0.0),
                direction=(
                    event.direction.value if event.direction is not None
                    else "NONE"
                ),
                touched=event.meta.get("touched", False),
                ticks_per_rev=event.meta.get("ticks_per_rev", STEPS_PER_REV),
            )

        # -- Schalter ------------------------------------------------------
        if event.event is EventType.POSITION and control_id == ids.DIRECTION:
            try:
                position = Direction(event.position)
            except ValueError:
                return None
            return mapped(CommandType.DIRECTION, position=position)

        return None
