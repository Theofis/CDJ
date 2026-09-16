"""Zentrale Komponentenliste des CDJ-Bedienfelds.

Diese Datei ist die einzige Quelle der Wahrheit fuer:

* welche Bedienelemente existieren,
* welche ID sie haben,
* welchen Typ sie haben,
* zu welcher Gruppe sie gehoeren,
* wo sie im virtuellen Bedienfeld liegen,
* ob sie eine LED besitzen.

Die Positionen sind die Pixelkoordinaten des Referenzbildes des geplanten
Bedienfelds (614 x 734). ``x``/``y`` ist jeweils die Mitte des Elements.
Die virtuelle Oberflaeche skaliert und verschiebt diese Koordinaten selbst.

Wichtig: Hier stehen keine Funktionen. Die Zuordnung
"Bedienelement -> CDJ-Funktion" und "Bedienelement -> Hardware-Pin" liegt
bewusst ausserhalb (siehe ``hardware_map.py`` bzw. ``controller.py``).
"""

from __future__ import annotations

from . import ids
from .model import Control, ControlType, Shape, Status

# --------------------------------------------------------------------------
# Gruppen
# --------------------------------------------------------------------------

G_SCREEN = "SCREEN"
G_BROWSE = "BROWSE"
G_MEDIA = "MEDIA"
G_MODIFIER = "MODIFIER"
G_PLAY_MODE = "PLAY_MODE"
G_PADS = "PERFORMANCE_PADS"
G_PAD_MODE = "PAD_MODE"
G_LOOP = "LOOP"
G_MEMORY = "CUE_LOOP_MEMORY"
G_BEAT_JUMP = "BEAT_JUMP"
G_DIRECTION = "DIRECTION"
G_SEARCH = "SEARCH"
G_TRANSPORT = "TRANSPORT"
G_SYNC = "SYNC"
G_TEMPO = "TEMPO"
G_JOG = "JOG"
#: Fuer Elemente, deren Zugehoerigkeit noch voellig offen ist. Aktuell leer -
#: die noch offenen Punkte stehen bei ihrer fachlichen Gruppe und tragen
#: ``Status.UNRESOLVED``.
G_UNRESOLVED = "UNRESOLVED"
#: Fuer eigenstaendige Anzeige-LEDs. Aktuell hat das Bedienfeld keine - die
#: LEDs sitzen alle in Tastern (Feld ``has_led``).
G_INDICATOR = "INDICATOR"

GROUP_ORDER = (
    G_SCREEN,
    G_BROWSE,
    G_MEDIA,
    G_MODIFIER,
    G_PLAY_MODE,
    G_PADS,
    G_PAD_MODE,
    G_LOOP,
    G_MEMORY,
    G_BEAT_JUMP,
    G_DIRECTION,
    G_SEARCH,
    G_TRANSPORT,
    G_SYNC,
    G_TEMPO,
    G_JOG,
    G_UNRESOLVED,
    G_INDICATOR,
)

#: Groesse des Referenzbildes, auf das sich alle Koordinaten beziehen.
PANEL_REFERENCE_SIZE = (614, 734)


def _btn(
    control_id: str,
    label: str,
    group: str,
    x: float,
    y: float,
    *,
    short: str = "",
    label_side: str = "below",
    w: float = 18,
    h: float = 18,
    shape: Shape = Shape.ROUND,
    has_led: bool = False,
    led_count: int = 1,
    status: Status = Status.VIRTUAL,
    note: str = "",
) -> Control:
    return Control(
        id=control_id,
        label=label,
        type=ControlType.DIGITAL_BUTTON,
        group=group,
        short=short,
        label_side=label_side,
        x=x,
        y=y,
        w=w,
        h=h,
        shape=shape,
        has_led=has_led,
        led_count=led_count,
        status=status,
        note=note,
    )


# --------------------------------------------------------------------------
# Komponentenliste
# --------------------------------------------------------------------------

CONTROL_LIST: tuple[Control, ...] = (
    # ---------------- Tastenreihe ueber dem Display ------------------------
    # Belegung von links nach rechts vom Erbauer bestaetigt.
    _btn(ids.SOURCE, "Source", G_SCREEN, 205, 88, short="SOURCE",
         w=32, h=13, shape=Shape.RECT),
    _btn(ids.BROWSE, "Browse", G_SCREEN, 245, 88, short="BROWSE",
         w=32, h=13, shape=Shape.RECT),
    _btn(ids.TAG_LIST, "Tag List", G_SCREEN, 285, 88, short="TAG LIST",
         w=32, h=13, shape=Shape.RECT),
    _btn(ids.PLAYLIST, "Playlist", G_SCREEN, 325, 88, short="PLAYLIST",
         w=32, h=13, shape=Shape.RECT),
    _btn(ids.SEARCH, "Search", G_SCREEN, 365, 88, short="SEARCH",
         w=32, h=13, shape=Shape.RECT),
    _btn(ids.MENU, "Menu", G_SCREEN, 405, 88, short="MENU",
         w=32, h=13, shape=Shape.RECT),

    # ---------------- Load-Drehregler (oben rechts) -----------------------
    #
    # Beschriftung "Load" wie am CDJ-3000: gedreht wird ausgewaehlt,
    # gedrueckt wird geladen. Die technischen IDs bleiben
    # ``BROWSE_ROTATE``/``BROWSE_PRESS`` - sie stehen in
    # ``config/hardware_mapping.json`` und in der Kommandokette. Eine
    # Umbenennung der ID waere eine Aenderung an der Hardwarezuordnung,
    # keine an der Beschriftung.
    Control(
        id=ids.BROWSE_ROTATE,
        label="Load (Drehung)",
        type=ControlType.ENCODER,
        group=G_BROWSE,
        short="LOAD",
        x=434, y=190, w=62, h=62,
        detents_per_rev=30,
        note="Drehregler zum Auswaehlen. Am Rand ziehen dreht, ohne zu "
             "druecken.",
    ),
    _btn(ids.BROWSE_PRESS, "Load (Druck)", G_BROWSE, 434, 190, short="",
         w=26, h=26,
         note="Druckfunktion des Load-Drehreglers. Im virtuellen Bedienfeld "
              "nur auf der Nabe, nicht auf dem Rand."),
    _btn(ids.BACK, "Back", G_BROWSE, 398, 145, short="BACK",
         label_side="above", w=30, h=12, shape=Shape.RECT),
    _btn(
        ids.TAG_TRACK_REMOVE, "Tag Track / Remove", G_BROWSE,
        470, 145, short="TAG TRACK\n/ REMOVE", label_side="above",
        w=34, h=12, shape=Shape.RECT,
    ),
    _btn(
        ids.TRACK_FILTER_EDIT, "Track Filter / Edit", G_BROWSE,
        398, 235, short="TRACK FILTER\n/ EDIT",
        w=34, h=12, shape=Shape.RECT,
    ),
    _btn(ids.SHORTCUT, "Shortcut", G_BROWSE, 470, 235, short="SHORT CUT",
         w=34, h=12, shape=Shape.RECT),

    # ---------------- Medien (oben links) ---------------------------------
    _btn(ids.USB_STOP, "USB Stop", G_MEDIA, 140, 181, short="USB STOP",
         w=15, h=15, status=Status.UNRESOLVED,
         note="Runder Taster unter dem Kartenschacht/Port oben links."),

    # ---------------- Slip / Quantize -------------------------------------
    _btn(ids.SLIP, "Slip", G_PLAY_MODE, 128, 238, short="SLIP",
         w=22, h=11, shape=Shape.RECT, has_led=True),
    _btn(ids.QUANTIZE, "Quantize", G_PLAY_MODE, 153, 238, short="QUANT",
         w=22, h=11, shape=Shape.RECT, has_led=True, label_side="right"),

    # ---------------- Shift (links neben den Pads) -------------------------
    _btn(ids.SHIFT, "Shift", G_MODIFIER, 140, 268, short="SHIFT",
         w=14, h=14, shape=Shape.SQUARE,
         note="Modifikator. Muss zusammen mit anderen Tastern gehalten "
              "werden koennen - im virtuellen Bedienfeld per Rechtsklick "
              "rasten."),

    # ---------------- Performance Pads A-H --------------------------------
    _btn(ids.PAD_A, "Pad A", G_PADS, 178, 268, short="A",
         w=28, h=15, shape=Shape.RECT, has_led=True),
    _btn(ids.PAD_B, "Pad B", G_PADS, 213, 268, short="B",
         w=28, h=15, shape=Shape.RECT, has_led=True),
    _btn(ids.PAD_C, "Pad C", G_PADS, 248, 268, short="C",
         w=28, h=15, shape=Shape.RECT, has_led=True),
    _btn(ids.PAD_D, "Pad D", G_PADS, 283, 268, short="D",
         w=28, h=15, shape=Shape.RECT, has_led=True),
    _btn(ids.PAD_E, "Pad E", G_PADS, 318, 268, short="E",
         w=28, h=15, shape=Shape.RECT, has_led=True),
    _btn(ids.PAD_F, "Pad F", G_PADS, 353, 268, short="F",
         w=28, h=15, shape=Shape.RECT, has_led=True),
    _btn(ids.PAD_G, "Pad G", G_PADS, 388, 268, short="G",
         w=28, h=15, shape=Shape.RECT, has_led=True),
    _btn(ids.PAD_H, "Pad H", G_PADS, 421, 268, short="H",
         w=28, h=15, shape=Shape.RECT, has_led=True),

    # ---------------- Loop ------------------------------------------------
    _btn(ids.LOOP_IN, "Loop In", G_LOOP, 130, 313, short="IN",
         w=22, h=22, has_led=True),
    _btn(ids.LOOP_OUT, "Loop Out", G_LOOP, 158, 313, short="OUT",
         w=22, h=22, has_led=True),
    _btn(ids.RELOOP_EXIT, "Reloop / Exit", G_LOOP, 188, 313, short="RELOOP",
         w=14, h=14, has_led=True),

    # ---------------- Pad-Modus: Hotcue / Beat Loop -----------------------
    #
    # Die beiden Modustaster des Bedienfelds. Sie sind eine Erweiterung des
    # Erbauers - am CDJ-3000 gibt es vier Pad-Modi, hier zwei, und genau
    # einer ist immer aktiv (``DeckState.pad_mode``). Die LED zeigt, welcher.
    _btn(ids.HOT_CUE, "Hotcue", G_PAD_MODE, 247, 313, short="HOTCUE",
         w=26, h=13, shape=Shape.RECT, has_led=True,
         note="Pad-Modus: Pads A-H werden Hot Cues."),
    _btn(ids.BEAT_JUMP, "Beat Loop", G_PAD_MODE, 279, 313, short="BEAT LOOP",
         w=26, h=13, shape=Shape.RECT, has_led=True, label_side="above",
         note="Pad-Modus: Pads A-H werden Beatloops (1/4 bis 32 Beats). "
              "Die technische ID bleibt BEAT_JUMP - sie steht in "
              "config/hardware_mapping.json; auf dem Geraet ist der Taster "
              "mit BEAT JUMP beschriftet."),

    # ---------------- Cue/Loop Call, Delete, Memory -----------------------
    _btn(ids.CUE_LOOP_CALL_PREV, "Cue/Loop Call zurueck", G_MEMORY, 338, 313,
         short="CALL-", w=14, h=14),
    _btn(ids.CUE_LOOP_CALL_NEXT, "Cue/Loop Call vor", G_MEMORY, 362, 313,
         short="CALL+", w=14, h=14, label_side="above"),
    _btn(ids.DELETE, "Delete", G_MEMORY, 387, 313, short="DEL",
         w=16, h=16),
    _btn(ids.MEMORY, "Memory", G_MEMORY, 411, 313, short="MEM",
         w=16, h=16, label_side="above"),

    # ---------------- Jog Mode (rechts neben MEMORY) -----------------------
    _btn(ids.JOG_MODE, "Jog Mode", G_JOG, 445, 313, short="JOG MODE",
         w=28, h=15, shape=Shape.RECT, has_led=True, led_count=2),

    # ---------------- Beat Loop (runde Taster) ----------------------------
    _btn(ids.BEAT_LOOP_4, "4 Beat Loop", G_LOOP, 130, 363,
         short="4 BEAT", w=17, h=17, has_led=True),
    _btn(ids.BEAT_LOOP_8, "8 Beat Loop", G_LOOP, 158, 363,
         short="8 BEAT", w=17, h=17, label_side="right", has_led=True),

    # ---------------- Beat Jump (rechteckig ueber DIRECTION) --------------
    _btn(ids.BEAT_JUMP_PREV, "Beat Jump zurueck", G_BEAT_JUMP, 130, 407,
         short="B JUMP -", w=20, h=12, shape=Shape.RECT),
    _btn(ids.BEAT_JUMP_NEXT, "Beat Jump vor", G_BEAT_JUMP, 158, 407,
         short="B JUMP +", w=20, h=12, shape=Shape.RECT,
         label_side="right"),

    # ---------------- Richtungsschalter -----------------------------------
    Control(
        id=ids.DIRECTION,
        label="Direction",
        type=ControlType.SWITCH,
        group=G_DIRECTION,
        short="DIRECTION",
        x=137, y=456, w=32, h=48,
        shape=Shape.RECT,
        has_led=True,
        led_count=3,
        positions=("SLIP_REV", "FWD", "REV"),
        default_position="FWD",
    ),

    # ---------------- Track Search / Search -------------------------------
    _btn(ids.TRACK_SEARCH_PREV, "Track Search zurueck", G_SEARCH,
         129, 505, short="TS-", w=17, h=15, shape=Shape.RECT),
    _btn(ids.TRACK_SEARCH_NEXT, "Track Search vor", G_SEARCH,
         153, 505, short="TS+", w=17, h=15, shape=Shape.RECT,
         label_side="right"),
    _btn(ids.SEARCH_BACK, "Search zurueck", G_SEARCH, 129, 532,
         short="SR-", w=17, h=15, shape=Shape.RECT),
    _btn(ids.SEARCH_FWD, "Search vor", G_SEARCH, 153, 532,
         short="SR+", w=17, h=15, shape=Shape.RECT, label_side="right"),

    # ---------------- Transport -------------------------------------------
    _btn(ids.CUE, "Cue", G_TRANSPORT, 140, 592, w=44, h=44,
         shape=Shape.BIG_ROUND, has_led=True),
    _btn(ids.PLAY, "Play / Pause", G_TRANSPORT, 140, 643, w=44, h=44,
         shape=Shape.BIG_ROUND, has_led=True),

    # ---------------- Sync / Master ---------------------------------------
    _btn(ids.BEAT_SYNC, "Beat Sync", G_SYNC, 436, 390, short="SYNC",
         w=17, h=15, shape=Shape.SQUARE, has_led=True),
    _btn(ids.MASTER, "Master", G_SYNC, 463, 390, short="MSTR",
         w=17, h=15, shape=Shape.SQUARE, has_led=True, label_side="right"),
    _btn(ids.KEY_SYNC, "Key Sync", G_SYNC, 449, 417, short="KEY SYNC",
         w=30, h=13, shape=Shape.RECT, has_led=True),

    # ---------------- Tempo -----------------------------------------------
    _btn(ids.TEMPO_RANGE, "Tempo Range", G_TEMPO, 450, 461, short="RANGE",
         w=14, h=14, has_led=True, label_side="left"),
    _btn(ids.MASTER_TEMPO, "Master Tempo", G_TEMPO, 450, 485,
         short="M.TEMPO", w=14, h=14, has_led=True, label_side="left"),
    Control(
        id=ids.TEMPO_FADER,
        label="Tempo",
        type=ControlType.ANALOG_FADER,
        group=G_TEMPO,
        short="TEMPO",
        label_side="above",
        x=450, y=587, w=30, h=148,
        shape=Shape.RECT,
        raw_min=0,
        raw_max=4095,
        default_value=0.5,
    ),
    _btn(ids.TEMPO_RESET, "Tempo Reset", G_TEMPO, 392, 582, short="T.RESET",
         w=12, h=12, has_led=True, label_side="left"),

    # ---------------- Jogwheel --------------------------------------------
    Control(
        id=ids.JOG_MOVE,
        label="Jogwheel",
        type=ControlType.JOG,
        group=G_JOG,
        short="JOG",
        x=283, y=478, w=210, h=210,
        # 200 Schlitze im Encoder-Ring, vier auswertbare Flanken je Schlitz.
        # Derselbe Wert steht als STEPS_PER_REV in jog/quadrature.py; ein
        # Test haelt beide zusammen.
        ticks_per_rev=800,
    ),
    _btn(ids.JOG_TOUCH, "Jog Touch", G_JOG, 283, 478, w=144, h=144,
         note="Beruehrungsflaeche der Jog-Platte. Eigener digitaler Zustand, "
              "unabhaengig von JOG_MOVE."),

    # ---------------- Vinyl Speed Adjust ----------------------------------
    Control(
        id=ids.VINYL_SPEED_ADJUST,
        label="Vinyl Speed Adjust",
        type=ControlType.ANALOG_POT,
        group=G_JOG,
        short="V.SPEED ADJ",
        label_side="above",
        x=452, y=270, w=14, h=14,
        raw_min=0,
        raw_max=4095,
        default_value=0.5,
    ),
)


# --------------------------------------------------------------------------
# Nachschlage-Hilfen
# --------------------------------------------------------------------------

CONTROLS: dict[str, Control] = {c.id: c for c in CONTROL_LIST}

INPUT_CONTROLS: dict[str, Control] = {
    c.id: c for c in CONTROL_LIST if c.is_input
}

OUTPUT_CONTROLS: dict[str, Control] = {
    c.id: c for c in CONTROL_LIST if not c.is_input
}

#: Alle Elemente, die eine LED besitzen (Ein- und Ausgaenge).
LED_CONTROLS: tuple[str, ...] = tuple(
    c.id for c in CONTROL_LIST if c.has_led
)


def get(control_id: str) -> Control:
    """Bedienelement per ID holen.

    Raises:
        KeyError: wenn die ID nicht in der Komponentenliste steht.
    """
    try:
        return CONTROLS[control_id]
    except KeyError:
        raise KeyError(
            f"Unbekannte Control-ID {control_id!r}. "
            "Bedienelemente muessen in controls.py definiert werden."
        ) from None


def by_group(group: str) -> tuple[Control, ...]:
    return tuple(c for c in CONTROL_LIST if c.group == group)


def by_type(control_type: ControlType) -> tuple[Control, ...]:
    return tuple(c for c in CONTROL_LIST if c.type is control_type)


def unresolved() -> tuple[Control, ...]:
    """Alle Elemente, deren Beschriftung/Funktion noch nicht geklaert ist."""
    return tuple(c for c in CONTROL_LIST if c.status is Status.UNRESOLVED)


def _check_unique() -> None:
    if len(CONTROLS) != len(CONTROL_LIST):
        seen: set[str] = set()
        dupes = sorted(
            {c.id for c in CONTROL_LIST if c.id in seen or seen.add(c.id)}
        )
        raise AssertionError(f"Doppelte Control-IDs: {dupes}")


_check_unique()
