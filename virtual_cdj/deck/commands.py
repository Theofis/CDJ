"""Deck-Kommandos - die Stufe zwischen Eingabe und Deck-Engine.

Ein Kommando beschreibt eine Absicht ("spiele/pausiere", "springe auf Hotcue
3"), nicht ein Eingabeereignis. Fuer Diagnose und anpassbare Konsolentexte
kann es die technische Control-ID tragen; die Deck-Logik wertet sie nicht aus.

Damit gilt die geforderte Kette:

    Hardware Input -> Input Mapping -> Deck Command -> Deck Engine
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CommandType(str, Enum):
    # Transport
    PLAY_PAUSE = "PLAY_PAUSE"
    CUE = "CUE"  # gedrueckt/losgelassen ueber ``pressed``
    SEEK = "SEEK"  # absolute Position, ``position_s``
    SEARCH = "SEARCH"  # Schnellvor-/ruecklauf, ``direction`` + ``pressed``
    TRACK_SEARCH = "TRACK_SEARCH"  # ``direction``
    DIRECTION = "DIRECTION"  # ``position``: FWD / REV / SLIP_REV

    # Performance Pads
    PAD = "PAD"  # ``index`` 0..7, ``pressed``
    PAD_MODE = "PAD_MODE"  # ``mode``: HOT_CUE / BEAT_JUMP
    #: CALL/DELETE als **Hotcue**-Funktion: gehalten der Modifikator zum
    #: Loeschen eines Hotcues, kurz gedrueckt der Aufrufmodus. ``pressed``.
    #:
    #: Ausdruecklich **nicht** das Loeschen gemerkter Punkte - das ist
    #: ``MEMORY_DELETE``. Am Geraet sitzen beide auf derselben Taste; hier
    #: sind sie zwei Kommandos, damit nichts stillschweigend dasselbe
    #: bedeutet (siehe ``Deck._cmd_delete``).
    DELETE = "DELETE"
    MEMORY = "MEMORY"  # Cue-Punkt oder laufenden Loop merken
    #: Den ueber CUE/LOOP CALL angewaehlten Memory Cue / Memory Loop
    #: loeschen. Eigenes Kommando, eigener Handler; erreichbar ueber den
    #: Tastenverlauf **und** unabhaengig davon ueber SHIFT + DELETE.
    MEMORY_DELETE = "MEMORY_DELETE"
    CUE_LOOP_CALL = "CUE_LOOP_CALL"  # ``direction``: gemerkte Punkte
    #: Manueller Hotcue-Aufrufmodus ein/aus - am Geraet der kurze Druck
    #: auf CALL/DELETE. Bleibt als Kommando fuer den direkten Weg.
    HOT_CUE_CALL_MODE = "HOT_CUE_CALL_MODE"
    #: AUTO CUE ein/aus - langer Druck auf die Zeitmodus-Taste.
    AUTO_CUE = "AUTO_CUE"

    # Loop
    LOOP_IN = "LOOP_IN"
    LOOP_OUT = "LOOP_OUT"
    RELOOP_EXIT = "RELOOP_EXIT"
    BEAT_LOOP = "BEAT_LOOP"  # ``beats``
    LOOP_HALVE = "LOOP_HALVE"
    LOOP_DOUBLE = "LOOP_DOUBLE"

    # Beat Jump
    BEAT_JUMP = "BEAT_JUMP"  # ``direction``, optional ``beats``
    #: Sprungweite fuer BEAT JUMP. Mit ``beats`` wird ein Wert gesetzt,
    #: ohne ``beats`` auf den naechsten der Reihe weitergeschaltet
    #: (1/2 -> 1 -> ... -> 64 -> 1/2). Am Geraet sitzt der Wert in
    #: UTILITY/SHORTCUT; an den Tastern erreicht ihn CALL/DELETE +
    #: BEAT JUMP, das ``Deck._cmd_beat_jump`` selbst erkennt.
    BEAT_JUMP_BEATS = "BEAT_JUMP_BEATS"  # optional ``beats``, ``direction``

    # Tempo
    TEMPO_SET = "TEMPO_SET"  # ``value`` 0.0-1.0 vom Fader
    TEMPO_RANGE_CYCLE = "TEMPO_RANGE_CYCLE"
    MASTER_TEMPO_TOGGLE = "MASTER_TEMPO_TOGGLE"
    TEMPO_RESET_TOGGLE = "TEMPO_RESET_TOGGLE"

    # Key Shift (Zustand vorhanden, DSP fehlt - siehe docs)
    KEY_SHIFT = "KEY_SHIFT"  # ``semitones`` relativ
    KEY_SHIFT_RESET = "KEY_SHIFT_RESET"

    # Sync / Master
    SYNC_TOGGLE = "SYNC_TOGGLE"
    MASTER_SET = "MASTER_SET"
    KEY_SYNC = "KEY_SYNC"
    QUANTIZE_TOGGLE = "QUANTIZE_TOGGLE"
    #: Rasterweite der Quantisierung. Mit ``beats`` wird ein Wert gesetzt,
    #: ohne ``beats`` auf den naechsten der Reihe weitergeschaltet
    #: (1/8 -> 1/4 -> 1/2 -> 1). Am Geraet sitzt das in UTILITY/SHORTCUT;
    #: hier zusaetzlich auf SHIFT + QUANTIZE.
    QUANTIZE_BEATS = "QUANTIZE_BEATS"  # optional ``beats``
    SLIP_TOGGLE = "SLIP_TOGGLE"

    # Jogwheel
    JOG_MOVE = "JOG_MOVE"  # ``delta`` in Ticks, ``ticks_per_rev``
    JOG_TOUCH = "JOG_TOUCH"  # ``pressed``
    JOG_MODE_TOGGLE = "JOG_MODE_TOGGLE"
    VINYL_SPEED_ADJUST = "VINYL_SPEED_ADJUST"  # ``value`` 0.0-1.0

    # Beatgrid (Handbuch S. 72) - echte Aenderung am Raster des Tracks
    BEATGRID_SHIFT = "BEATGRID_SHIFT"  # ``delta_s`` oder ``beats``
    BEATGRID_RESET = "BEATGRID_RESET"

    # Bildschirm / Browser
    BROWSE_ROTATE = "BROWSE_ROTATE"  # ``delta``
    BROWSE_PRESS = "BROWSE_PRESS"  # ``pressed``
    VIEW = "VIEW"  # ``view``: WAVEFORM / BROWSE / SOURCE, optional ``path``
    BACK = "BACK"  # ``held`` = laenger gehalten: oberste Ebene (S. 25)
    LOAD = "LOAD"  # ``track_id``

    # Anwendungsrahmen. Wird in ``CdjApplication.dispatch`` behandelt und
    # erreicht kein Deck-Backend.
    OPEN_SETTINGS = "OPEN_SETTINGS"  # SHIFT + MENU

    # Reine Anzeigebefehle. Der Bildschirm behandelt sie selbst und schickt
    # sie nicht an die Deck-Engine - sie aendern keinen Deck-Zustand.
    PANEL = "PANEL"  # ``panel``: BEAT_LOOP / KEY_SHIFT / BEAT_JUMP / ...
    TIME_MODE = "TIME_MODE"  # Restzeit <-> verstrichene Zeit
    WAVEFORM_ZOOM = "WAVEFORM_ZOOM"  # ``delta``: negativ = naeher heran
    WAVEFORM_MODE = "WAVEFORM_MODE"  # ``mode``: RGB / 3BAND / BLUE
    HEADER_TOGGLE = "HEADER_TOGGLE"  # Wellenform <-> Phasenmesser
    NEEDLE_LOCK = "NEEDLE_LOCK"  # Overview-Touch sperren/freigeben

    # Navigation in SOURCE- und Durchsuchen-Bildschirm (S. 18-20, 24-25).
    NAV_SELECT = "NAV_SELECT"  # ``index``, ``confirm``
    NAV_ENTER = "NAV_ENTER"  # markierten Eintrag bestaetigen
    NAV_TOP = "NAV_TOP"  # oberste Hierarchieebene (BACK halten)
    BROWSE_SORT = "BROWSE_SORT"  # ``column``
    BROWSE_TOGGLE = "BROWSE_TOGGLE"  # ``option``: PREVIEW / INFO / FONT
    JUMP_MODE = "JUMP_MODE"  # Sprungmodus ein/aus (Drehregler halten)
    ROTARY_MODE = "ROTARY_MODE"  # Zoom <-> Rastereinstellung

    # Tag List (Handbuch S. 40-41). Reine Anzeige-/Auswahlbefehle: sie
    # aendern keinen Deck-Zustand und erreichen kein Backend.
    TAG_TRACK_TOGGLE = "TAG_TRACK_TOGGLE"  # markierten/geladenen Track
    TAG_MENU_ACTION = "TAG_MENU_ACTION"  # ``action``: REMOVE_ALL / CREATE

    #: MENU-Taste. Was sie tut, haengt an der Ansicht: in der Tag List
    #: oeffnet sie das Tag-List-Menue (S. 41), sonst den Verlauf (S. 37).
    #: Deshalb ein eigenes Kommando statt eines festen ``VIEW`` - die
    #: Zuordnungsschicht kennt die Ansicht nicht.
    MENU = "MENU"

    # Medien
    USB_STOP = "USB_STOP"


class Views(str, Enum):
    """Ansichten der Bildschirmoberflaeche.

    Genau die drei, die es am Geraet gibt und die hier auch wirklich etwas
    anzeigen: Wellenform (S. 21), Durchsuchen (S. 19) und Quellenauswahl
    (S. 18). Fuer UTILITY, SHORTCUT und den Suchbildschirm gibt es bewusst
    keinen Eintrag, solange sie nicht umgesetzt sind.

    **PLAYLIST und TAG LIST sind keine eigenen Ansichten.** Am Geraet sind
    es Kategorien desselben Durchsuchen-Bildschirms, und genau so sind sie
    hier umgesetzt: die Tasten springen ueber ``path`` in die jeweilige
    Kategorie von ``Views.BROWSE``. Eine eigene Ansicht haette eine zweite
    Trackliste, eine zweite Sortierung und eine zweite Ladefunktion
    bedeutet - vier Bibliothekssysteme statt einem.
    """

    WAVEFORM = "WAVEFORM"
    BROWSE = "BROWSE"
    SOURCE = "SOURCE"


@dataclass(frozen=True)
class DeckCommand:
    """Ein Kommando an ein bestimmtes Deck."""

    type: CommandType
    deck_id: int
    #: Zusatzparameter, je nach ``type``: index, pressed, direction, value,
    #: beats, mode, view, position, delta, track_id.
    params: dict[str, Any] = field(default_factory=dict)
    #: Nur zur Diagnose - woher kam die Absicht.
    origin: str = ""
    #: Technisches Bedienelement, das das Kommando ausgeloest hat. Leer bei
    #: Touch-, Tastatur- oder direkt erzeugten Kommandos.
    control_id: str = ""

    def get(self, name: str, default: Any = None) -> Any:
        return self.params.get(name, default)

    @property
    def pressed(self) -> bool:
        return bool(self.params.get("pressed", True))

    def __str__(self) -> str:
        if self.params:
            args = " ".join(f"{k}={v}" for k, v in self.params.items())
            return f"{self.type.value}[{self.deck_id}] {args}"
        return f"{self.type.value}[{self.deck_id}]"


def command(
    command_type: CommandType,
    deck_id: int,
    origin: str = "",
    control_id: str = "",
    **params: Any,
) -> DeckCommand:
    """Kurzform zum Erzeugen eines Kommandos."""
    return DeckCommand(
        type=command_type,
        deck_id=deck_id,
        params=dict(params),
        origin=origin,
        control_id=control_id,
    )
