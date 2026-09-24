"""Anzeigezustand des CDJ-Bildschirms.

Wichtig: Diese Schicht **aggregiert nur**. Sie enthaelt keine zweite
Transport-, Audio- oder Tempologik. Position, BPM, Beat, Loop und Cues werden
aus ``DeckState`` gelesen, niemals neu berechnet.

Eigen ist ihr ausschliesslich, was reine Anzeige ist: Zeitmodus, Zoomstufe,
Wellenformfarbe, geoeffnetes Panel. Und abgeleitete Werte, die aus
``DeckState`` + Beatgrid folgen, etwa der Beat Countdown.

``MasterDeckView`` ist absichtlich flach und serialisierbar: genau die Felder,
die spaeter ueber ein PRO-DJ-LINK-artiges Netz kommen wuerden. Der Bildschirm
weiss dadurch nicht, ob der Master auf demselben Rechner laeuft.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import Enum

from .library import (
    ORIGINAL_ORDER,
    PLAYLIST_CATEGORY,
    TAG_LIST_CATEGORY,
    BrowseColumn,
)
from .state import BeatGrid, DeckState, PlayState, WaveformSet


class TimeMode(str, Enum):
    """Zeitanzeige: verstrichen oder restlich."""

    REMAIN = "REMAIN"
    ELAPSED = "ELAPSED"

    def toggled(self) -> TimeMode:
        return TimeMode.ELAPSED if self is TimeMode.REMAIN else TimeMode.REMAIN


class WaveformMode(str, Enum):
    """Wellenformfarbe, wie ``WAVEFORM COLOR`` am Geraet."""

    RGB = "RGB"
    THREE_BAND = "3BAND"
    BLUE = "BLUE"

    def next_mode(self) -> WaveformMode:
        order = list(WaveformMode)
        return order[(order.index(self) + 1) % len(order)]


class TouchPanel(str, Enum):
    """Welches Touch-Panel offen ist."""

    NONE = "NONE"
    BEAT_LOOP = "BEAT_LOOP"
    KEY_SHIFT = "KEY_SHIFT"
    BEAT_JUMP = "BEAT_JUMP"
    TRACK_INFO = "TRACK_INFO"


class WaveformHeader(str, Enum):
    """Kopfzeile ueber der Wellenform: Wellenform oder Phasenmesser."""

    WAVEFORM = "WAVEFORM"
    PHASE_METER = "PHASE_METER"

    def toggled(self) -> WaveformHeader:
        return (
            WaveformHeader.PHASE_METER
            if self is WaveformHeader.WAVEFORM
            else WaveformHeader.WAVEFORM
        )


class RotaryMode(str, Enum):
    """Was der Drehregler auf dem Wellenform-Bildschirm tut.

    Handbuch S. 22, Element 11: Halten des Drehreglers wechselt zwischen
    Zoom- und Rastereinstellungsmodus.
    """

    ZOOM = "ZOOM"
    GRID = "GRID"

    def toggled(self) -> RotaryMode:
        return RotaryMode.GRID if self is RotaryMode.ZOOM else RotaryMode.ZOOM


class JumpMode(str, Enum):
    """Sprungmodus im Durchsuchen-Bildschirm (Handbuch S. 38)."""

    OFF = "OFF"
    ALPHABET = "ALPHABET"
    PAGE = "PAGE"


class BrowseContext(str, Enum):
    """Womit der Durchsuchen-Bildschirm gerade beschaeftigt ist.

    Nicht drei Bildschirme, sondern drei Einstiege in dieselbe Bibliothek.
    Der Kontext dient nur dazu, je Einstieg Markierung, Pfad und Sortierung
    zu **merken**: wer von BROWSE nach PLAYLIST und zurueck wechselt, steht
    wieder auf demselben Track (Abschnitt 17 der Aufgabe).
    """

    LIBRARY = "LIBRARY"
    PLAYLIST = "PLAYLIST"
    TAG_LIST = "TAG_LIST"

    @classmethod
    def of(cls, path: Sequence[str]) -> BrowseContext:
        """Kontext aus dem Pfad ableiten - eine Quelle der Wahrheit."""
        if path:
            if path[0] == PLAYLIST_CATEGORY:
                return cls.PLAYLIST
            if path[0] == TAG_LIST_CATEGORY:
                return cls.TAG_LIST
        return cls.LIBRARY


class TagMenuAction(str, Enum):
    """Eintraege des Tag-List-Menues (Handbuch S. 41)."""

    REMOVE_ALL = "REMOVE ALL TRACKS"
    CREATE_PLAYLIST = "CREATE PLAYLIST"


@dataclass(frozen=True)
class BrowseView:
    """Navigationszustand des Durchsuchen-Bildschirms.

    Reine Anzeige: Pfad, Markierung, Sortierung und die drei Schalter der
    Kopfzeile (S. 19, Elemente 3-7). Kein Trackinhalt - der kommt aus der
    ``MediaLibrary``.
    """

    path: tuple[str, ...] = ()
    selected: int = 0
    #: Erste Beruehrung markiert, zweite bestaetigt (S. 25).
    confirming: bool = False
    sort_column: BrowseColumn = ORIGINAL_ORDER
    ascending: bool = True
    preview: bool = True
    info: bool = False
    large_font: bool = False
    jump: JumpMode = JumpMode.OFF

    @property
    def at_top(self) -> bool:
        return not self.path

    @property
    def context(self) -> BrowseContext:
        return BrowseContext.of(self.path)

    @property
    def is_original_order(self) -> bool:
        """Ob die gespeicherte Reihenfolge gilt - der Ausgangszustand."""
        return self.sort_column.is_original_order and self.ascending

    def moved(self, delta: int, count: int) -> BrowseView:
        if count <= 0:
            return replace(self, selected=0, confirming=False)
        index = max(0, min(self.selected + delta, count - 1))
        return replace(self, selected=index, confirming=False)

    def at_index(self, index: int, *, confirming: bool = True) -> BrowseView:
        return replace(self, selected=max(0, index), confirming=confirming)

    def entered(self, node_key: str) -> BrowseView:
        """Eine Ebene tiefer gehen.

        Die neue Ebene beginnt in ihrer **gespeicherten** Reihenfolge. Eine
        frisch geoeffnete Playlist zeigt damit die Reihenfolge des Sticks
        und nicht die Sortierung, die zuletzt auf einer anderen Liste
        eingestellt war (Abschnitt 11 der Aufgabe). Sortiert wird erst,
        wenn der Benutzer eine Spalte antippt.
        """
        return replace(
            self,
            path=self.path + (node_key,),
            selected=0,
            confirming=False,
            jump=JumpMode.OFF,
            sort_column=ORIGINAL_ORDER,
            ascending=True,
        )

    def with_original_order(self) -> BrowseView:
        """Zurueck zur gespeicherten Reihenfolge (``ORIGINAL ORDER``)."""
        return replace(
            self, sort_column=ORIGINAL_ORDER, ascending=True, confirming=False
        )

    def up(self) -> BrowseView:
        return replace(
            self,
            path=self.path[:-1],
            selected=0,
            confirming=False,
            jump=JumpMode.OFF,
        )

    def to_top(self) -> BrowseView:
        return replace(
            self, path=(), selected=0, confirming=False, jump=JumpMode.OFF
        )

    def with_sort(self, column: BrowseColumn) -> BrowseView:
        """Titelzeile beruehren: gleiche Spalte dreht die Richtung (S. 20)."""
        if column is self.sort_column:
            return replace(self, ascending=not self.ascending, confirming=False)
        return replace(self, sort_column=column, ascending=True, confirming=False)

    def with_jump(self, mode: JumpMode) -> BrowseView:
        return replace(self, jump=mode)

    def toggled_preview(self) -> BrowseView:
        return replace(self, preview=not self.preview)

    def toggled_info(self) -> BrowseView:
        return replace(self, info=not self.info)

    def toggled_font(self) -> BrowseView:
        return replace(self, large_font=not self.large_font)


#: Zoomstufen **musikalisch** in Beats. Damit bleibt die Darstellung
#: unabhaengig vom Tempo gleich: 16 Beats sind 16 Beats, ob bei 124 oder
#: 156 BPM. Am Geraet wird mit dem Drehregler gezoomt.
ZOOM_BEATS: tuple[int, ...] = (2, 4, 8, 16, 32, 64)

#: Ruecklauf in Sekunden fuer Tracks ohne gueltiges Beatgrid - dann gibt es
#: keine Beats, auf die man sich beziehen koennte.
ZOOM_WINDOWS_S: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0, 32.0, 64.0)

#: Standard: 8 Beats, also zwei Takte.
DEFAULT_ZOOM_INDEX = 2

#: Beat-Laengen im Beat-Loop-Panel (Handbuch S. 58).
BEAT_LOOP_VALUES: tuple[float, ...] = (
    0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0,
)

#: Beat-Laengen im Beat-Jump-Panel (Handbuch S. 66, plus 32 wie gewuenscht).
BEAT_JUMP_VALUES: tuple[float, ...] = (
    0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0,
)

#: Grenze fuer Key Shift in Halbtoenen.
KEY_SHIFT_LIMIT = 12


@dataclass(frozen=True)
class BeatCountdown:
    """Abstand zum naechsten Cue in Takten und Beats.

    Am Geraet wird das als ``Takte.Beats`` gezeigt, z. B. ``2.3``.
    """

    bars: int = 0
    beats: int = 0
    #: Zielposition in Sekunden, ``None`` wenn kein Cue mehr folgt.
    target_s: float | None = None
    #: Beschriftung des Ziels, z. B. ``C`` oder ``MEM``.
    label: str = ""

    @property
    def is_valid(self) -> bool:
        return self.target_s is not None

    @property
    def total_beats(self) -> int:
        return self.bars * 4 + self.beats

    def text(self, beats_per_bar: int = 4) -> str:
        if not self.is_valid:
            return "--.-"
        return f"{self.bars}.{self.beats + 1}"


@dataclass(frozen=True)
class MasterDeckView:
    """Schlanke Sicht auf das Master-Deck.

    Flach und ohne Verweis auf ein lokales ``Deck``, damit derselbe Typ
    spaeter aus dem Netzwerk befuellt werden kann.
    """

    #: ``local`` oder die normalisierte externe Quelle (z. B. ``prolink``).
    #: Die Herkunft ist Teil der Identität: ein externer Player 1 ist nicht
    #: dasselbe Objekt wie ein lokales Deck 1.
    source_type: str = "local"
    player_id: int = 0
    track_id: str = ""
    title: str = ""
    artist: str = ""
    bpm: float = 0.0
    key: str = ""
    position_s: float = 0.0
    duration_s: float = 0.0
    bar: int = 0
    beat: int = 0
    beat_phase: float = 0.0
    is_master: bool = False
    is_playing: bool = False
    #: Beatgrid-Parameter, damit die Master-Wellenform ihr Raster zeichnen kann.
    beat_grid: BeatGrid | None = None
    #: Wellenform-Vorschau. Lokal die volle Analyse, ueber Netz eine
    #: reduzierte Stufe.
    waveform: WaveformSet | None = None

    @property
    def has_track(self) -> bool:
        return bool(self.track_id)

    @property
    def has_waveform(self) -> bool:
        return self.waveform is not None and self.waveform.is_consistent()

    @classmethod
    def from_deck_state(cls, state: DeckState) -> MasterDeckView:
        """Aus einem lokalen ``DeckState`` erzeugen.

        Der einzige Ort, an dem lokaler Zustand in die Master-Sicht
        uebersetzt wird. Eine Netzwerkquelle wuerde dieselben Felder
        direkt fuellen.
        """
        track = state.track
        return cls(
            source_type="local",
            player_id=state.deck_id,
            track_id=track.track_id if track else "",
            title=track.display_title if track else "",
            artist=track.artist if track else "",
            bpm=state.current_bpm,
            key=state.key,
            position_s=state.position_s,
            duration_s=state.duration_s,
            bar=state.bar,
            beat=state.beat,
            beat_phase=state.beat_phase,
            is_master=state.is_master,
            is_playing=state.play_state
            in (PlayState.PLAYING, PlayState.CUEING),
            beat_grid=track.beat_grid if track else None,
            waveform=track.waveform if track else None,
        )


#: Verwandte Tonarten im Camelot-System: gleiche Zahl, Nachbarzahlen,
#: Wechsel zwischen A und B. Grundlage fuer die gruene Tonartanzeige.
def related_keys(camelot: str) -> frozenset[str]:
    """Zum Camelot-Code passende Tonarten."""
    if not camelot or len(camelot) < 2:
        return frozenset()
    number_text, letter = camelot[:-1], camelot[-1].upper()
    if not number_text.isdigit() or letter not in ("A", "B"):
        return frozenset()
    number = int(number_text)
    if not 1 <= number <= 12:
        return frozenset()

    def wrap(value: int) -> int:
        return (value - 1) % 12 + 1

    other = "B" if letter == "A" else "A"
    return frozenset({
        f"{number}{letter}",
        f"{wrap(number - 1)}{letter}",
        f"{wrap(number + 1)}{letter}",
        f"{number}{other}",
    })


@dataclass(frozen=True)
class CdjDisplayState:
    """Was der Bildschirm anzeigt.

    Enthaelt eine Referenz auf den lokalen ``DeckState``, eine
    ``MasterDeckView`` und reine Anzeigezustaende.
    """

    deck: DeckState
    master: MasterDeckView | None = None

    time_mode: TimeMode = TimeMode.REMAIN
    waveform_mode: WaveformMode = WaveformMode.RGB
    zoom_index: int = DEFAULT_ZOOM_INDEX
    panel: TouchPanel = TouchPanel.NONE
    header: WaveformHeader = WaveformHeader.WAVEFORM
    #: Needle Lock: waehrend der Wiedergabe keinen Sprung per Overview-Touch.
    needle_lock: bool = True

    # -- Navigation (SOURCE, BROWSE, Drehregler) --------------------------
    #: Bestaetigte Quelle des Durchsuchen-Bildschirms (S. 18).
    source_id: str = ""
    #: Markierung im SOURCE-Bildschirm - erst die zweite Bestaetigung waehlt.
    source_index: int = 0
    source_confirming: bool = False
    browse: BrowseView = field(default_factory=BrowseView)
    #: Zustand je Einstieg (BROWSE / PLAYLIST / TAG LIST), damit beim
    #: Hin- und Herwechseln Markierung, Pfad und Sortierung erhalten
    #: bleiben (Abschnitt 17). Der aktive Kontext steht in ``browse``.
    browse_memory: tuple[tuple[BrowseContext, BrowseView], ...] = ()
    #: Zoom- oder Rastereinstellungsmodus des Drehreglers (S. 22, Element 11).
    rotary: RotaryMode = RotaryMode.ZOOM
    #: Offenes Tag-List-Menue (S. 41). ``None`` heisst geschlossen; sonst
    #: der markierte Eintrag.
    tag_menu: TagMenuAction | None = None
    #: Kurze Rueckmeldung ueber der Liste, z. B. "Tag List voll". Leer
    #: heisst "nichts zu melden".
    notice: str = ""

    # ------------------------------------------------------------------
    # Durchgriff auf den Deck-Zustand - kein eigener Wert
    # ------------------------------------------------------------------

    @property
    def deck_id(self) -> int:
        return self.deck.deck_id

    @property
    def has_track(self) -> bool:
        return self.deck.has_track

    @property
    def zoom_step(self) -> int:
        return max(0, min(self.zoom_index, len(ZOOM_BEATS) - 1))

    @property
    def zoom_beats(self) -> int:
        """Sichtbare Beats der aktuellen Zoomstufe."""
        return ZOOM_BEATS[self.zoom_step]

    @property
    def is_beat_zoom(self) -> bool:
        """Ob der Zoom an Beats haengt - dafuer braucht es ein Tempo."""
        return self.deck.has_beat_grid and self.deck.current_bpm > 0

    @property
    def window_s(self) -> float:
        """Sichtbares Zeitfenster der laufenden Wellenform.

        Mit Beatgrid folgt es der Zoomstufe in Beats und damit dem
        laufenden Tempo; ohne Beatgrid bleibt es ein Zeitfenster.
        """
        step = self.zoom_step
        if self.is_beat_zoom:
            return ZOOM_BEATS[step] * 60.0 / self.deck.current_bpm
        return ZOOM_WINDOWS_S[step]

    @property
    def zoom_label(self) -> str:
        if self.is_beat_zoom:
            return f"{self.zoom_beats} BEATS"
        return f"{self.window_s:g} s"

    @property
    def is_master(self) -> bool:
        return self.deck.is_master

    @property
    def master_is_other_deck(self) -> bool:
        """Ob ein **anderes** Deck Master ist."""
        master = self.master
        if master is None or not master.is_master:
            return False
        return (
            master.source_type != "local"
            or master.player_id != self.deck.deck_id
        )

    # ------------------------------------------------------------------
    # Abgeleitete Anzeigewerte
    # ------------------------------------------------------------------

    @property
    def time_value_s(self) -> float:
        deck = self.deck
        if self.time_mode is TimeMode.REMAIN:
            return deck.remaining_s
        return deck.position_s

    @property
    def time_prefix(self) -> str:
        return "-" if self.time_mode is TimeMode.REMAIN else ""

    @property
    def key_is_related_to_master(self) -> bool:
        """Ob die eigene Tonart zur Tonart des Masters passt.

        Am Geraet wird die Tonart dann gruen dargestellt.
        """
        master = self.master
        if master is None or not master.key or not self.deck.key:
            return False
        if master.player_id == self.deck.deck_id:
            return False
        return self.deck.key in related_keys(master.key)

    def beat_countdown(self) -> BeatCountdown:
        """Takte und Beats bis zum naechsten Cue-Marker.

        Beruecksichtigt Hot Cues **und** Memory Cues. Das Handbuch nennt nur
        gespeicherte Cues; auf diesem Geraet sind Hot Cues die praktisch
        genutzten Marker, deshalb beide.
        """
        deck = self.deck
        track = deck.track
        if track is None or track.beat_grid is None:
            return BeatCountdown()
        grid = track.beat_grid
        if not grid.is_valid:
            return BeatCountdown()

        position = deck.position_s
        candidates: list[tuple[float, str]] = [
            (cue.position_s, cue.label)
            for cue in track.hot_cues
            if cue.position_s > position + 1e-6
        ]
        candidates.extend(
            (memory.position_s, "MEM")
            for memory in track.memory_cues
            if memory.position_s > position + 1e-6
        )
        if not candidates:
            return BeatCountdown()

        target_s, label = min(candidates, key=lambda item: item[0])

        current_beat = grid.beat_number_at(position)
        target_beat = grid.beat_number_at(target_s)
        if target_beat < 0:
            return BeatCountdown(target_s=target_s, label=label)
        # Liegt die Position noch vor dem ersten Beat des Rasters, wird von
        # Beat 0 aus gezaehlt - sonst waere der Countdown am Trackanfang 0.
        current_beat = max(0, current_beat)

        distance = max(0, target_beat - current_beat)
        per_bar = max(1, grid.beats_per_bar)
        return BeatCountdown(
            bars=distance // per_bar,
            beats=distance % per_bar,
            target_s=target_s,
            label=label,
        )

    # ------------------------------------------------------------------
    # Aenderungen - immer neue Objekte
    # ------------------------------------------------------------------

    def with_deck(self, deck: DeckState) -> CdjDisplayState:
        return replace(self, deck=deck)

    def with_master(self, master: MasterDeckView | None) -> CdjDisplayState:
        return replace(self, master=master)

    def with_time_mode_toggled(self) -> CdjDisplayState:
        return replace(self, time_mode=self.time_mode.toggled())

    def with_panel(self, panel: TouchPanel) -> CdjDisplayState:
        """Panel oeffnen; dasselbe Panel erneut schliesst es."""
        target = TouchPanel.NONE if panel is self.panel else panel
        return replace(self, panel=target)

    def with_zoom(self, delta: int) -> CdjDisplayState:
        index = max(
            0, min(self.zoom_index + delta, len(ZOOM_BEATS) - 1)
        )
        return replace(self, zoom_index=index)

    def with_waveform_mode(self, mode: WaveformMode) -> CdjDisplayState:
        return replace(self, waveform_mode=mode)

    def with_header_toggled(self) -> CdjDisplayState:
        return replace(self, header=self.header.toggled())

    # -- Navigation -------------------------------------------------------

    def with_browse(self, browse: BrowseView) -> CdjDisplayState:
        return replace(self, browse=browse)

    # -- Zustand je Einstieg ---------------------------------------------

    def remembered(self, context: BrowseContext) -> BrowseView | None:
        for stored, view in self.browse_memory:
            if stored is context:
                return view
        return None

    def with_context(self, context: BrowseContext) -> CdjDisplayState:
        """In einen Einstieg wechseln und seinen letzten Stand wiederholen.

        Der Stand des bisherigen Einstiegs wird dabei gemerkt. Wer aus
        BROWSE mit markiertem Track 45 in die PLAYLIST wechselt und wieder
        zurueck, steht wieder auf Track 45.
        """
        current = self.browse.context
        if current is context:
            return self
        memory = tuple(
            (stored, view)
            for stored, view in self.browse_memory
            if stored is not current
        ) + ((current, self.browse),)
        target = self.remembered(context)
        if target is None:
            root = {
                BrowseContext.PLAYLIST: (PLAYLIST_CATEGORY,),
                BrowseContext.TAG_LIST: (TAG_LIST_CATEGORY,),
            }.get(context, ())
            # Die Schalter der Kopfzeile (PREVIEW/INFO/Schrift) sind eine
            # Vorliebe des Benutzers und gelten ueberall - sie werden
            # uebernommen statt zurueckgesetzt.
            target = replace(
                BrowseView(
                    preview=self.browse.preview,
                    info=self.browse.info,
                    large_font=self.browse.large_font,
                ),
                path=root,
            )
        return replace(self, browse=target, browse_memory=memory)

    # -- Tag List ---------------------------------------------------------

    def with_tag_menu(
        self, action: TagMenuAction | None
    ) -> CdjDisplayState:
        return replace(self, tag_menu=action)

    def with_tag_menu_toggled(self) -> CdjDisplayState:
        """MENU in der Tag List oeffnet und schliesst das Menue (S. 41)."""
        if self.tag_menu is not None:
            return replace(self, tag_menu=None)
        return replace(self, tag_menu=TagMenuAction.REMOVE_ALL)

    def with_tag_menu_moved(self, delta: int) -> CdjDisplayState:
        if self.tag_menu is None or delta == 0:
            return self
        order = list(TagMenuAction)
        index = order.index(self.tag_menu)
        return replace(
            self, tag_menu=order[max(0, min(index + delta, len(order) - 1))]
        )

    def with_notice(self, text: str) -> CdjDisplayState:
        return replace(self, notice=text)

    def with_source(
        self,
        source_id: str,
        *,
        reset_path: bool = True,
    ) -> CdjDisplayState:
        """Quelle bestaetigen. Der Browser beginnt dann oben (S. 18/19)."""
        if source_id == self.source_id and not reset_path:
            return self
        return replace(
            self,
            source_id=source_id,
            browse=self.browse.to_top() if reset_path else self.browse,
        )

    def with_source_index(
        self, index: int, *, confirming: bool = False
    ) -> CdjDisplayState:
        return replace(
            self,
            source_index=max(0, index),
            source_confirming=confirming,
        )

    def with_rotary_toggled(self) -> CdjDisplayState:
        return replace(self, rotary=self.rotary.toggled())
