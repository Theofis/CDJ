"""Internes Bibliotheksmodell - unabhaengig von Rekordbox.

Das ist die einzige Datenform, die die DJ-Software zu sehen bekommt. Kein
Feld hier traegt ein Rekordbox-Detail: keine Offsets, keine Millisekunden,
keine Tabellennummern. Positionen sind Sekunden, Farben sind Hex-Text,
Slots sind ``A`` bis ``H``.

    Rekordbox-USB -> Reader -> Parser -> dieses Modell -> DJ-Software
    DJ-Software   -> dieses Modell -> Writer -> Rekordbox-USB

Damit laesst sich spaeter eine zweite Quelle (Serato, Netzwerk-CDJ)
anschliessen, ohne dass die Oberflaeche etwas davon merkt.

Aenderungen entstehen nie am Datentraeger, sondern immer zuerst hier. Alle
Typen sind unveraenderlich; ``set_hot_cue()`` und ``delete_hot_cue()``
liefern einen **neuen** Track. Das ist der Stil des uebrigen Projekts
(``DeckState``, ``LoopState``) und macht es unmoeglich, versehentlich eine
halb geaenderte Bibliothek zu schreiben.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

#: Beschriftung der acht Hotcue-Slots. Entspricht ``PAD_LABELS`` im
#: Deck-Modell - dieselbe Reihenfolge, damit Pad 0 und Slot "A" dasselbe
#: meinen.
HOT_CUE_SLOTS: tuple[str, ...] = ("A", "B", "C", "D", "E", "F", "G", "H")


def slot_name(index: int) -> str:
    """``0`` -> ``"A"``. Ausserhalb 0-7 gibt es keinen Namen."""
    if 0 <= index < len(HOT_CUE_SLOTS):
        return HOT_CUE_SLOTS[index]
    return "?"


def slot_index(slot: str | int) -> int:
    """``"A"`` oder ``0`` -> ``0``.

    Raises:
        ValueError: wenn der Slot nicht A-H bzw. 0-7 ist. Ein falscher Slot
            ist ein Programmierfehler, kein Datenfehler - deshalb hart.
    """
    if isinstance(slot, int):
        if 0 <= slot < len(HOT_CUE_SLOTS):
            return slot
        raise ValueError(f"Hotcue-Slot ausserhalb 0-7: {slot}")
    name = slot.strip().upper()
    if name in HOT_CUE_SLOTS:
        return HOT_CUE_SLOTS.index(name)
    raise ValueError(f"Unbekannter Hotcue-Slot: {slot!r}")


class CueType(str, Enum):
    """Ob ein Cue-Punkt eine Position oder ein Loop ist."""

    CUE = "CUE"
    LOOP = "LOOP"


# --------------------------------------------------------------------------
# Beatgrid
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BeatGridPoint:
    """Ein Beat aus dem Beatgrid."""

    #: Position im Takt, 1 = Downbeat.
    beat_number: int
    #: Zeit in Sekunden bei normaler Geschwindigkeit.
    position_s: float
    #: Tempo an dieser Stelle. Rekordbox speichert je Beat ein eigenes
    #: Tempo, damit auch Tracks mit wechselndem Tempo stimmen.
    bpm: float


@dataclass(frozen=True)
class LibraryBeatGrid:
    """Alle Beats eines Tracks."""

    points: tuple[BeatGridPoint, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.points)

    def __len__(self) -> int:
        return len(self.points)

    @property
    def first_beat_s(self) -> float:
        return self.points[0].position_s if self.points else 0.0

    @property
    def bpm(self) -> float:
        """Tempo des ersten Beats. ``0.0``, wenn es kein Beatgrid gibt."""
        return self.points[0].bpm if self.points else 0.0

    @property
    def is_constant_tempo(self) -> bool:
        """Ob alle Beats dasselbe Tempo tragen."""
        if not self.points:
            return False
        first = self.points[0].bpm
        return all(point.bpm == first for point in self.points)

    @property
    def downbeat_index(self) -> int:
        """Index des ersten Beats mit ``beat_number == 1``."""
        for index, point in enumerate(self.points):
            if point.beat_number == 1:
                return index
        return 0


# --------------------------------------------------------------------------
# Cue-Punkte
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HotCue:
    """Ein Hotcue-Slot.

    Es gibt immer acht davon. Ein leerer Slot ist nicht ``None``, sondern
    ein Slot mit ``active = False`` - so kann die Oberflaeche acht Pads
    zeichnen, ohne zu wissen, welche belegt sind.
    """

    #: 0-7, entspricht A-H.
    index: int
    active: bool = False
    position_s: float = 0.0
    type: CueType = CueType.CUE
    #: Nur bei ``type == LOOP``: Ende des Loops in Sekunden.
    loop_end_s: float | None = None
    #: Hex-Farbe wie ``"#ff8c00"``. Leer, wenn die Datei keine Farbe
    #: enthaelt - es wird keine erfunden.
    color: str = ""
    #: Rohwert der Rekordbox-Farbtabelle. Wird beim Schreiben unveraendert
    #: zurueckgegeben, damit die Farbe in rekordbox gleich bleibt.
    color_code: int = 0
    #: Name/Kommentar des Cues, wie ihn rekordbox anzeigt.
    name: str = ""

    @property
    def slot(self) -> str:
        return slot_name(self.index)

    @property
    def length_s(self) -> float:
        """Loop-Laenge in Sekunden, sonst ``0.0``."""
        if self.type is not CueType.LOOP or self.loop_end_s is None:
            return 0.0
        return max(0.0, self.loop_end_s - self.position_s)


@dataclass(frozen=True)
class MemoryCue:
    """Ein gespeicherter Cue- oder Loop-Punkt ohne Pad."""

    position_s: float
    type: CueType = CueType.CUE
    loop_end_s: float | None = None
    color: str = ""
    color_code: int = 0
    name: str = ""

    @property
    def length_s(self) -> float:
        if self.type is not CueType.LOOP or self.loop_end_s is None:
            return 0.0
        return max(0.0, self.loop_end_s - self.position_s)


# --------------------------------------------------------------------------
# Waveform
# --------------------------------------------------------------------------


class WaveformKind(str, Enum):
    """Welche Waveform-Variante eine Spaltenreihe ist.

    Rekordbox legt mehrere nebeneinander ab: grobe Uebersichten fuer den
    Balken ueber dem Track und feine Reihen zum Mitlaufen.
    """

    #: Uebersicht ueber den ganzen Track, monochrom (PWAV).
    PREVIEW = "PREVIEW"
    #: Sehr kleine Uebersicht aelterer Geraete (PWV2).
    PREVIEW_TINY = "PREVIEW_TINY"
    #: Uebersicht ueber den ganzen Track, farbig (PWV4).
    PREVIEW_COLOR = "PREVIEW_COLOR"
    #: Mitlaufende Waveform, monochrom (PWV3).
    DETAIL = "DETAIL"
    #: Mitlaufende Waveform, farbig (PWV5).
    DETAIL_COLOR = "DETAIL_COLOR"


@dataclass(frozen=True)
class WaveformColumn:
    """Eine Spalte einer Waveform.

    ``height`` ist auf 0.0-1.0 normiert. ``red``/``green``/``blue`` sind
    ebenfalls 0.0-1.0 und bei monochromen Waveforms alle gleich
    (Helligkeitsstufe statt Farbe).
    """

    height: float
    red: float = 0.0
    green: float = 0.0
    blue: float = 0.0

    @property
    def is_monochrome(self) -> bool:
        return self.red == self.green == self.blue


@dataclass(frozen=True)
class Waveform:
    """Eine Waveform-Reihe eines Tracks."""

    kind: WaveformKind
    columns: tuple[WaveformColumn, ...] = ()
    #: Spalten je Sekunde. Bei den Uebersichten ergibt sich das erst aus
    #: der Tracklaenge; ist die unbekannt, steht hier ``0.0`` statt einer
    #: geratenen Zahl.
    columns_per_second: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.columns)

    def __len__(self) -> int:
        return len(self.columns)

    @property
    def is_color(self) -> bool:
        return self.kind in (
            WaveformKind.PREVIEW_COLOR, WaveformKind.DETAIL_COLOR
        )


# --------------------------------------------------------------------------
# Track
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LibraryTrack:
    """Ein Track der Bibliothek samt Performance-Daten.

    Die Felder sind bewusst flach: alles, was aus mehreren Dateien
    zusammengetragen wurde (Datenbankzeile, ``.DAT``, ``.EXT``), steht hier
    an einer Stelle.
    """

    #: Eindeutig innerhalb eines Datentraegers.
    id: int
    file_path: str = ""
    file_name: str = ""

    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    label: str = ""
    remixer: str = ""
    composer: str = ""
    original_artist: str = ""
    comment: str = ""

    bpm: float = 0.0
    key: str = ""
    rating: int = 0
    #: Farbkennzeichnung des Tracks, Hex oder Name - leer, wenn keine.
    color: str = ""
    color_id: int = 0

    duration_s: float = 0.0
    year: int = 0
    track_number: int = 0
    disc_number: int = 0
    play_count: int = 0
    bitrate: int = 0
    sample_rate: int = 0
    sample_depth: int = 0
    file_size: int = 0
    file_type: str = ""

    date_added: str = ""
    release_date: str = ""
    analyze_date: str = ""
    mix_name: str = ""
    isrc: str = ""
    artwork_path: str = ""
    #: Pfad der ``ANLZ*``-Datei laut Datenbank, so wie er dort steht.
    analyze_path: str = ""
    #: Ob rekordbox die Hotcues beim Laden automatisch setzen soll.
    autoload_hot_cues: bool = False

    beat_grid: LibraryBeatGrid = field(default_factory=LibraryBeatGrid)
    #: Immer acht Slots, A-H. Leere Slots haben ``active = False``.
    hot_cues: tuple[HotCue, ...] = ()
    memory_cues: tuple[MemoryCue, ...] = ()
    loops: tuple[MemoryCue, ...] = ()
    waveforms: tuple[Waveform, ...] = ()

    #: Welche Dateien zu diesem Track gelesen wurden (Diagnose, Abschnitt
    #: "Logging"). Nicht fuer die Oberflaeche gedacht.
    analysis_files: tuple[str, ...] = ()
    #: Klartext-Hinweise: fehlende Analyse, unlesbare Felder. Leer heisst
    #: "nichts zu berichten".
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.hot_cues:
            object.__setattr__(
                self,
                "hot_cues",
                tuple(HotCue(index=i) for i in range(len(HOT_CUE_SLOTS))),
            )

    # ------------------------------------------------------------------
    # Lesen
    # ------------------------------------------------------------------

    def hot_cue(self, slot: str | int) -> HotCue:
        """Ein Slot, auch wenn er leer ist."""
        return self.hot_cues[slot_index(slot)]

    @property
    def active_hot_cues(self) -> tuple[HotCue, ...]:
        return tuple(cue for cue in self.hot_cues if cue.active)

    @property
    def has_performance_data(self) -> bool:
        """Ob ueberhaupt Analysedaten vorliegen."""
        return bool(
            self.beat_grid
            or self.active_hot_cues
            or self.memory_cues
            or self.loops
            or self.waveforms
        )

    def waveform(self, kind: WaveformKind) -> Waveform | None:
        for waveform in self.waveforms:
            if waveform.kind is kind:
                return waveform
        return None

    @property
    def display_title(self) -> str:
        """Titel, sonst Dateiname - nie ein erfundener Platzhalter."""
        return self.title or self.file_name

    # ------------------------------------------------------------------
    # Aendern (nur hier im Modell, nie auf dem Datentraeger)
    # ------------------------------------------------------------------

    def set_hot_cue(
        self,
        slot: str | int,
        position_s: float,
        *,
        type: CueType = CueType.CUE,
        loop_end_s: float | None = None,
        color: str = "",
        color_code: int = 0,
        name: str = "",
    ) -> LibraryTrack:
        """Hotcue setzen und den geaenderten Track zurueckgeben.

        Ein bereits belegter Slot wird ueberschrieben - das entspricht dem
        Geraet, an dem ein Pad-Druck im Set-Modus den alten Punkt ersetzt.
        """
        if position_s < 0:
            raise ValueError("Hotcue-Position darf nicht negativ sein")
        if type is CueType.LOOP:
            if loop_end_s is None:
                raise ValueError("Loop ohne Endpunkt")
            if loop_end_s <= position_s:
                raise ValueError("Loop-Ende liegt nicht hinter dem Anfang")
        index = slot_index(slot)
        cue = HotCue(
            index=index,
            active=True,
            position_s=float(position_s),
            type=type,
            loop_end_s=None if type is CueType.CUE else float(loop_end_s),
            color=color,
            color_code=color_code,
            name=name,
        )
        cues = list(self.hot_cues)
        cues[index] = cue
        return replace(self, hot_cues=tuple(cues))

    def delete_hot_cue(self, slot: str | int) -> LibraryTrack:
        """Hotcue loeschen. Ein leerer Slot bleibt einfach leer."""
        index = slot_index(slot)
        cues = list(self.hot_cues)
        cues[index] = HotCue(index=index)
        return replace(self, hot_cues=tuple(cues))

    def set_rating(self, rating: int) -> LibraryTrack:
        if not 0 <= rating <= 5:
            raise ValueError(f"Bewertung ausserhalb 0-5: {rating}")
        return replace(self, rating=rating)

    def set_memory_cues(
        self, cues: tuple[MemoryCue, ...]
    ) -> LibraryTrack:
        """Memory-Cues ersetzen; Loops bleiben unberuehrt."""
        return replace(self, memory_cues=cues)

    def set_loops(self, loops: tuple[MemoryCue, ...]) -> LibraryTrack:
        return replace(self, loops=loops)


# --------------------------------------------------------------------------
# Playlists
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlaylistNode:
    """Ein Knoten im Playlist-Baum: Ordner oder Playlist.

    Rekordbox erlaubt beliebig tiefe Ordner. Der Baum wird deshalb als
    Baum abgebildet und nicht in eine einzelne Ebene gepresst.
    """

    id: int
    name: str
    is_folder: bool = False
    sort_order: int = 0
    parent_id: int = 0
    children: tuple[PlaylistNode, ...] = ()
    #: Track-IDs in der Reihenfolge der Playlist. Bei Ordnern leer.
    track_ids: tuple[int, ...] = ()

    @property
    def track_count(self) -> int:
        if not self.is_folder:
            return len(self.track_ids)
        return sum(child.track_count for child in self.children)

    def walk(self):
        """Diesen Knoten und alle darunter der Reihe nach liefern."""
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, playlist_id: int) -> PlaylistNode | None:
        for node in self.walk():
            if node.id == playlist_id:
                return node
        return None


# --------------------------------------------------------------------------
# Bibliothek
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceLibrary:
    """Alles, was von einem Datentraeger gelesen wurde."""

    #: Wurzelpfad des Datentraegers, wie er beim Lesen erkannt wurde.
    root_path: str = ""
    #: Kennung des Datentraegers (Seriennummer), falls ermittelbar.
    volume_id: str = ""
    label: str = ""
    tracks: tuple[LibraryTrack, ...] = ()
    #: Playlist-Baum, oberste Ebene.
    playlists: tuple[PlaylistNode, ...] = ()
    #: Verlaufslisten des Players, getrennt vom Playlist-Baum.
    history: tuple[PlaylistNode, ...] = ()
    #: Klartext-Hinweise aus dem Lesevorgang.
    warnings: tuple[str, ...] = ()
    #: Ob geschrieben werden darf. Bei jedem Zweifel ``True`` - lieber
    #: nur lesen als einen Stick beschaedigen.
    read_only: bool = True
    #: Grund, falls nur gelesen werden darf.
    read_only_reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "_by_id", {track.id: track for track in self.tracks}
        )

    def track(self, track_id: int) -> LibraryTrack | None:
        return getattr(self, "_by_id", {}).get(track_id)

    @property
    def track_count(self) -> int:
        return len(self.tracks)

    @property
    def playlist_count(self) -> int:
        """Alle Playlists, auch tief verschachtelte - ohne die Ordner."""
        return sum(
            1
            for root in self.playlists
            for node in root.walk()
            if not node.is_folder
        )

    def replace_track(self, track: LibraryTrack) -> DeviceLibrary:
        """Einen geaenderten Track uebernehmen."""
        tracks = tuple(
            track if existing.id == track.id else existing
            for existing in self.tracks
        )
        return replace(self, tracks=tracks)

    def playlist(self, playlist_id: int) -> PlaylistNode | None:
        for root in self.playlists:
            found = root.find(playlist_id)
            if found is not None:
                return found
        return None
