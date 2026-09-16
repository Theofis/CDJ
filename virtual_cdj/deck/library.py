"""Quellen und Browse-Hierarchie - das Modell hinter SOURCE und BROWSE.

Handbuch S. 18 (Quellenauswahl), S. 19-20 (Durchsuchen-Bildschirm) und
S. 24-25 (Bedienung mit Drehregler und Touch).

Diese Schicht kennt keine Oberflaeche, kein Audio und keine Dateiformate.
Sie beantwortet genau zwei Fragen:

* Welche Quellen gibt es? -> ``MediaLibrary.sources()``
* Was steht auf einer Ebene? -> ``MediaLibrary.node(source_id, path)``

Damit kann die Oberflaeche navigieren, ohne zu wissen, ob die Tracks von
einer Datei, aus dem Demo-Paket oder spaeter aus einem Netzwerk kommen.

Es werden **keine** Quellen erfunden. Gibt es nichts, ist die Liste leer und
der Bildschirm sagt das.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import PureWindowsPath

from .state import TrackInfo


class SourceKind(str, Enum):
    """Geraetetyp einer Quelle - bestimmt Symbol und Farbe (S. 18)."""

    SD = "SD"
    USB = "USB"
    LINK = "LINK"
    DEMO = "DEMO"
    FILE = "FILE"


class EntryKind(str, Enum):
    """Art eines Listeneintrags."""

    CATEGORY = "CATEGORY"
    FOLDER = "FOLDER"
    TRACK = "TRACK"


class BrowseColumn(str, Enum):
    """Sortierbare Spalten der Trackliste (Titelzeile, S. 20).

    ``NUMBER`` ist die **Originalreihenfolge**: die Reihenfolge der Quelle,
    innerhalb einer Playlist die von rekordbox vorgegebene, im Verlauf die
    Abspielreihenfolge. Aufsteigend nach ``NUMBER`` zu sortieren heisst
    also "nicht sortieren" - deshalb ist es auch der Ausgangszustand und
    das Ziel von "ORIGINAL ORDER".
    """

    NUMBER = "#"
    TITLE = "TRACK"
    ARTIST = "ARTIST"
    BPM = "BPM"
    KEY = "KEY"
    RATING = "RATING"
    TIME = "TIME"
    COLOR = "COLOR"

    @property
    def is_alphabetical(self) -> bool:
        """Nur alphabetisch sortierte Listen erlauben Alphabet-Sprung (S. 38)."""
        return self in (BrowseColumn.TITLE, BrowseColumn.ARTIST)

    @property
    def is_original_order(self) -> bool:
        return self is BrowseColumn.NUMBER


#: Ausgangssortierung jeder Liste: die gespeicherte Reihenfolge.
ORIGINAL_ORDER = BrowseColumn.NUMBER


@dataclass(frozen=True)
class SourceInfo:
    """Eine Quelle, wie sie der SOURCE-Bildschirm zeigt.

    ``songs``, ``playlists``, ``date``, ``total_mb`` und ``available_mb``
    entsprechen der Geraeteinformation auf S. 18. Felder, zu denen es keinen
    echten Wert gibt, bleiben leer bzw. ``0`` - die Anzeige schreibt dann
    einen Gedankenstrich statt einer Erfindung.
    """

    source_id: str
    name: str
    kind: SourceKind = SourceKind.FILE
    player_number: int = 0
    songs: int = 0
    playlists: int = 0
    date: str = ""
    total_mb: float = 0.0
    available_mb: float = 0.0
    #: Mit rekordbox-Bibliothek: Kategorien. Ohne: Ordnerhierarchie (S. 19).
    has_library: bool = False
    #: Ehrlicher Hinweis, z. B. "synthetische Analysedaten".
    note: str = ""

    # -- Zustand eines echten Datentraegers ----------------------------
    #
    # Ein USB-Stick ist nicht einfach "da oder nicht": er kann erkannt,
    # aber noch nicht gelesen sein, ein nicht unterstuetztes Format tragen
    # oder eine beschaedigte Datenbank haben. Diese drei Felder machen das
    # im SOURCE-Bildschirm sichtbar, statt einen Fehlzustand als leere
    # Bibliothek auszugeben. Quellen ohne Datentraeger lassen sie leer.

    #: Zustand in Klartext, z. B. "bereit" oder "wird gelesen".
    status: str = ""
    #: Erkanntes Bibliotheksformat in Klartext.
    library_format: str = ""
    #: Dateisystem des Datentraegers, z. B. ``"FAT32"``.
    filesystem: str = ""
    #: Fehler oder Warnung in einem Satz. Leer heisst "nichts zu melden".
    warning: str = ""


@dataclass(frozen=True)
class BrowseEntry:
    """Eine Zeile einer Ebene."""

    kind: EntryKind
    label: str
    #: Schluessel der Unterebene (bei CATEGORY und FOLDER).
    node_key: str = ""
    track: TrackInfo | None = None
    #: Anzahl der Eintraege unterhalb - bei CATEGORY und FOLDER.
    count: int = 0
    #: Position in der Trackliste der Quelle, 1-basiert (Spalte "#").
    number: int = 0
    #: Wurde gespielt und steht im Verlauf (S. 42: gruen).
    played: bool = False
    #: Steht in der Tag List dieses Datentraegers - am Geraet ein Haken.
    tagged: bool = False

    @property
    def is_track(self) -> bool:
        return self.kind is EntryKind.TRACK and self.track is not None

    @property
    def initial(self) -> str:
        """Erstes Zeichen fuer den Alphabet-Sprung (S. 38)."""
        for char in self.label:
            if char.isalnum():
                return char.upper()
        return "#"


@dataclass(frozen=True)
class BrowseNode:
    """Eine Ebene der Hierarchie."""

    source_id: str
    path: tuple[str, ...] = ()
    title: str = ""
    entries: tuple[BrowseEntry, ...] = ()
    #: Art der Eintraege dieser Ebene.
    level_kind: EntryKind = EntryKind.CATEGORY
    #: Spalte, nach der sortiert wurde, und Richtung.
    sort_column: BrowseColumn = BrowseColumn.NUMBER
    ascending: bool = True

    @property
    def is_track_list(self) -> bool:
        return self.level_kind is EntryKind.TRACK

    @property
    def is_root(self) -> bool:
        return not self.path

    @property
    def is_empty(self) -> bool:
        return not self.entries

    def entry(self, index: int) -> BrowseEntry | None:
        if not self.entries:
            return None
        return self.entries[max(0, min(index, len(self.entries) - 1))]

    def breadcrumb(self, source_name: str = "") -> str:
        """Pfadanzeige der Kopfzeile (S. 19, Element 4)."""
        parts = [part for part in self.path if part]
        if not parts:
            return source_name or self.title
        return "  /  ".join(parts)


# --------------------------------------------------------------------------
# Kategorien
# --------------------------------------------------------------------------

def _artist(track: TrackInfo) -> str:
    return track.artist


def _album(track: TrackInfo) -> str:
    return track.album


def _genre(track: TrackInfo) -> str:
    return track.genre


def _bpm(track: TrackInfo) -> str:
    if track.original_bpm <= 0:
        return ""
    return f"{track.original_bpm:.0f}"


def _key(track: TrackInfo) -> str:
    return track.key


def _folder(track: TrackInfo) -> str:
    if not track.file_path:
        return ""
    # ``PureWindowsPath`` trennt an ``/`` und ``\`` - unabhaengig vom System.
    parent = PureWindowsPath(track.file_path).parent
    return parent.name or str(parent)


#: Gruppierende Kategorien: Name -> Schluesselfunktion.
GROUP_CATEGORIES: dict[str, Callable[[TrackInfo], str]] = {
    "ARTIST": _artist,
    "ALBUM": _album,
    "GENRE": _genre,
    "BPM": _bpm,
    "KEY": _key,
    "FOLDER": _folder,
}

#: Name der Playlist-Kategorie - von der PLAYLIST-Taste angesprungen.
PLAYLIST_CATEGORY = "PLAYLIST"
#: Name der Tag-List-Kategorie - von der TAG-LIST-Taste angesprungen.
TAG_LIST_CATEGORY = "TAG LIST"

#: Anzeigereihenfolge der Kategorien in der linken Spalte (S. 19).
CATEGORY_ORDER: tuple[str, ...] = (
    "TRACK", "ARTIST", "ALBUM", "GENRE", "BPM", "KEY",
    PLAYLIST_CATEGORY, TAG_LIST_CATEGORY, "HISTORY", "FOLDER",
)


def _group_sort_key(label: str):
    """Zahlengruppen numerisch, alles andere alphabetisch."""
    try:
        return (0, float(label), "")
    except ValueError:
        return (1, 0.0, label.upper())


def _track_sort_value(entry: BrowseEntry, column: BrowseColumn):
    """Sortierwert einer Zeile, ohne Behandlung fehlender Werte.

    Zahlen bleiben Zahlen (BPM, Bewertung, Laenge werden **numerisch**
    verglichen, nicht als Text), Texte werden gross geschrieben verglichen.
    """
    track = entry.track
    if track is None:
        return (1, 0.0, entry.label.upper())
    if column is BrowseColumn.NUMBER:
        return (0, float(entry.number), "")
    if column is BrowseColumn.BPM:
        return (0, track.original_bpm, "")
    if column is BrowseColumn.RATING:
        return (0, float(track.rating), "")
    if column is BrowseColumn.TIME:
        return (0, track.duration_s, "")
    if column is BrowseColumn.ARTIST:
        return (1, 0.0, (track.artist or "").upper())
    if column is BrowseColumn.KEY:
        # Der gespeicherte Wert der Bibliothek, unveraendert. Es findet
        # keine Camelot-Umrechnung statt - das Projekt fuehrt zwei
        # Schreibweisen (rekordbox liefert "8A", die eigene Analyse auch),
        # eine Umrechnung wuerde hier nur raten.
        return (1, 0.0, (track.key or "").upper())
    if column is BrowseColumn.COLOR:
        return (1, 0.0, (track.color or "").upper())
    return (1, 0.0, track.display_title.upper())


def _is_missing(entry: BrowseEntry, column: BrowseColumn) -> bool:
    """Ob der Wert dieser Spalte fehlt.

    Fehlende Werte landen **immer am Ende**, in beiden Richtungen. Sonst
    stuenden bei absteigender BPM-Sortierung die Tracks ohne BPM ganz oben,
    und eine fehlende Angabe saehe aus wie ein Messwert.
    """
    track = entry.track
    if track is None:
        return False
    if column is BrowseColumn.BPM:
        return track.original_bpm <= 0
    if column is BrowseColumn.RATING:
        return track.rating <= 0
    if column is BrowseColumn.TIME:
        return track.duration_s <= 0
    if column is BrowseColumn.KEY:
        return not track.key
    if column is BrowseColumn.COLOR:
        return not track.color
    if column is BrowseColumn.ARTIST:
        return not track.artist
    return False


def sort_entries(
    entries: Sequence[BrowseEntry],
    column: BrowseColumn,
    *,
    ascending: bool = True,
) -> tuple[BrowseEntry, ...]:
    """Zeilen sortieren - die eine Sortierfunktion fuer alle Listen.

    BROWSE, PLAYLIST, TAG LIST und jede weitere Ansicht rufen dieselbe
    Funktion. Sie liefert eine **neue** Reihenfolge; die uebergebene Folge
    bleibt unveraendert, und die Originalreihenfolge der Quelle wird nie
    ueberschrieben.
    """
    ordered = sorted(entries, key=lambda e: _track_sort_value(e, column))
    if not ascending:
        ordered.reverse()
    # Fehlende Werte ans Ende - nach dem Umdrehen, damit es in beiden
    # Richtungen gilt.
    present = [e for e in ordered if not _is_missing(e, column)]
    missing = [e for e in ordered if _is_missing(e, column)]
    return tuple(present + missing)


class TrackListLibrary:
    """Bibliothek einer Quelle, aufgebaut aus einer flachen Trackliste.

    Die Trackliste kommt als Funktion herein, damit sie sich aendern darf,
    ohne dass die Bibliothek neu gebaut werden muss.
    """

    def __init__(
        self,
        info: SourceInfo,
        tracks: Callable[[], Sequence[TrackInfo]],
        *,
        playlists: Callable[[], Mapping[str, Sequence[str]]] | None = None,
        history: Callable[[], Sequence[str]] | None = None,
        tag_list: Callable[[], Sequence[str]] | None = None,
    ) -> None:
        self._info = info
        self._tracks = tracks
        self._playlists = playlists
        self._history = history
        #: Track-IDs der Tag List dieses Datentraegers, in der Reihenfolge
        #: des Hinzufuegens. Die Liste selbst fuehrt der ``TagListService``;
        #: die Bibliothek liest sie nur.
        self._tag_list = tag_list

    # ------------------------------------------------------------------

    @property
    def source_id(self) -> str:
        return self._info.source_id

    @property
    def info(self) -> SourceInfo:
        """Quellenangaben, ``songs``/``playlists`` aus der echten Liste."""
        return replace(
            self._info,
            songs=len(self.tracks()),
            playlists=len(self.playlists()),
        )

    def tracks(self) -> tuple[TrackInfo, ...]:
        try:
            return tuple(self._tracks())
        except Exception:  # pragma: no cover - defekte Quelle
            return ()

    def playlists(self) -> Mapping[str, Sequence[str]]:
        if self._playlists is None:
            return {}
        try:
            return dict(self._playlists())
        except Exception:  # pragma: no cover
            return {}

    def history_ids(self) -> tuple[str, ...]:
        if self._history is None:
            return ()
        try:
            return tuple(self._history())
        except Exception:  # pragma: no cover
            return ()

    @property
    def has_tag_list(self) -> bool:
        """Ob diese Quelle ueberhaupt eine Tag List fuehrt."""
        return self._tag_list is not None

    def tag_ids(self) -> tuple[str, ...]:
        if self._tag_list is None:
            return ()
        try:
            return tuple(self._tag_list())
        except Exception:  # pragma: no cover
            return ()

    def track(self, track_id: str) -> TrackInfo | None:
        for track in self.tracks():
            if track.track_id == track_id:
                return track
        return None

    # ------------------------------------------------------------------

    def categories(self) -> tuple[str, ...]:
        """Belegte Kategorien in Anzeigereihenfolge.

        Eine Kategorie erscheint nur, wenn sie wirklich etwas enthaelt -
        eine leere Spalte waere eine Anzeige ohne Inhalt.
        """
        tracks = self.tracks()
        if not tracks:
            return ()
        available = ["TRACK"]
        for name in CATEGORY_ORDER:
            if name == "TRACK":
                continue
            if name == PLAYLIST_CATEGORY:
                if self.playlists():
                    available.append(name)
                continue
            if name == TAG_LIST_CATEGORY:
                # Anders als die uebrigen Kategorien erscheint die Tag List
                # auch leer: sie ist eine Arbeitsliste, die der DJ selbst
                # fuellt - sie muss auffindbar sein, bevor etwas drinsteht.
                if self.has_tag_list:
                    available.append(name)
                continue
            if name == "HISTORY":
                if self.history_ids():
                    available.append(name)
                continue
            key = GROUP_CATEGORIES[name]
            if any(key(track) for track in tracks):
                available.append(name)
        return tuple(available)

    # ------------------------------------------------------------------

    def _numbered(self) -> dict[str, int]:
        """Track-ID -> Position in der Quelle, 1-basiert (Spalte "#")."""
        return {
            track.track_id: number
            for number, track in enumerate(self.tracks(), start=1)
        }

    def _track_entries(
        self,
        tracks: Sequence[TrackInfo],
        *,
        column: BrowseColumn,
        ascending: bool,
        numbers: Mapping[str, int] | None = None,
    ) -> tuple[BrowseEntry, ...]:
        """Tracks zu Listenzeilen machen.

        ``numbers`` ist die Spalte "#". Standard ist die Position in der
        Quelle; eine Playlist oder der Verlauf geben ihre **eigene**
        Reihenfolge mit. Das ist nicht nur Kosmetik: nach ``#`` wird auch
        sortiert, und mit den Nummern der Quelle wuerde die Reihenfolge
        einer Playlist beim Anzeigen wieder verlorengehen.
        """
        if numbers is None:
            numbers = self._numbered()
        history = set(self.history_ids())
        tagged = set(self.tag_ids())
        entries = [
            BrowseEntry(
                kind=EntryKind.TRACK,
                label=track.display_title,
                track=track,
                number=numbers.get(track.track_id, 0),
                played=track.track_id in history,
                tagged=track.track_id in tagged,
            )
            for track in tracks
        ]
        # Sortiert wird eine **Kopie**; ``tracks`` und damit die Reihenfolge
        # der Quelle bleiben unberuehrt (Abschnitt 10 der Aufgabe).
        return sort_entries(entries, column, ascending=ascending)

    def _folder_entries(
        self, category: str, *, ascending: bool
    ) -> tuple[BrowseEntry, ...]:
        key = GROUP_CATEGORIES[category]
        counts: dict[str, int] = {}
        for track in self.tracks():
            label = key(track)
            if label:
                counts[label] = counts.get(label, 0) + 1
        labels = sorted(counts, key=_group_sort_key, reverse=not ascending)
        return tuple(
            BrowseEntry(
                kind=EntryKind.FOLDER,
                label=label,
                node_key=label,
                count=counts[label],
            )
            for label in labels
        )

    # ------------------------------------------------------------------

    def node(
        self,
        path: Sequence[str] = (),
        *,
        column: BrowseColumn = BrowseColumn.NUMBER,
        ascending: bool = True,
    ) -> BrowseNode:
        """Eine Ebene der Hierarchie liefern."""
        path = tuple(part for part in path if part)
        info = self._info
        tracks = self.tracks()

        def build(
            title: str,
            entries: tuple[BrowseEntry, ...],
            kind: EntryKind,
        ) -> BrowseNode:
            return BrowseNode(
                source_id=info.source_id,
                path=path,
                title=title,
                entries=entries,
                level_kind=kind,
                sort_column=column,
                ascending=ascending,
            )

        # -- Wurzel --------------------------------------------------------
        if not path:
            if info.has_library:
                categories = self.categories()
                entries = tuple(
                    BrowseEntry(
                        kind=EntryKind.CATEGORY,
                        label=name,
                        node_key=name,
                        count=self._category_count(name),
                    )
                    for name in categories
                )
                return build(info.name, entries, EntryKind.CATEGORY)
            # Ohne Bibliothek: Ordner, sonst direkt die Tracks (S. 19).
            folders = (
                self._folder_entries("FOLDER", ascending=ascending)
                if any(_folder(track) for track in tracks)
                else ()
            )
            if folders:
                return build(info.name, folders, EntryKind.FOLDER)
            return build(
                info.name,
                self._track_entries(
                    tracks, column=column, ascending=ascending
                ),
                EntryKind.TRACK,
            )

        category = path[0]

        # -- Erste Ebene unter der Wurzel ----------------------------------
        if len(path) == 1:
            if category == "TRACK":
                return build(
                    "TRACK",
                    self._track_entries(
                        tracks, column=column, ascending=ascending
                    ),
                    EntryKind.TRACK,
                )
            if category == "HISTORY":
                played = self._history_tracks()
                return build(
                    "HISTORY",
                    self._track_entries(
                        played,
                        column=column, ascending=ascending,
                        # Der Verlauf hat seine eigene Reihenfolge -
                        # zuletzt gespielt zuerst (S. 42).
                        numbers={
                            track.track_id: index
                            for index, track in enumerate(played, start=1)
                        },
                    ),
                    EntryKind.TRACK,
                )
            if category == PLAYLIST_CATEGORY:
                playlists = self.playlists()
                entries = tuple(
                    BrowseEntry(
                        kind=EntryKind.FOLDER,
                        label=name,
                        node_key=name,
                        count=len(ids),
                    )
                    for name, ids in sorted(playlists.items())
                )
                return build(PLAYLIST_CATEGORY, entries, EntryKind.FOLDER)
            if category == TAG_LIST_CATEGORY:
                # Die Tag List ist eine flache Liste, kein Ordner: die
                # TAG-LIST-Taste fuehrt direkt auf die Tracks (S. 40).
                tagged = self._tag_tracks()
                return build(
                    TAG_LIST_CATEGORY,
                    self._track_entries(
                        tagged,
                        column=column, ascending=ascending,
                        # Reihenfolge des Hinzufuegens - das ist hier die
                        # Originalreihenfolge.
                        numbers={
                            track.track_id: index
                            for index, track in enumerate(tagged, start=1)
                        },
                    ),
                    EntryKind.TRACK,
                )
            if category in GROUP_CATEGORIES:
                return build(
                    category,
                    self._folder_entries(category, ascending=ascending),
                    EntryKind.FOLDER,
                )
            # Quelle ohne Bibliothek: die Wurzel zeigt Ordner, ein Schritt
            # tiefer stehen deren Tracks (S. 19).
            in_folder = [
                track for track in tracks if _folder(track) == category
            ]
            return build(
                category,
                self._track_entries(
                    in_folder, column=column, ascending=ascending
                ),
                EntryKind.TRACK,
            )

        # -- Zweite Ebene: Tracks der Gruppe -------------------------------
        group = path[1]
        numbers: dict[str, int] | None = None
        if category == PLAYLIST_CATEGORY:
            ids = list(self.playlists().get(group, ()))
            order = {track_id: i for i, track_id in enumerate(ids)}
            selected = [
                track for track in tracks if track.track_id in order
            ]
            selected.sort(key=lambda t: order[t.track_id])
            # "#" ist in einer Playlist die Position **in dieser Playlist**
            # (S. 20). Damit bleibt die von rekordbox vorgegebene
            # Reihenfolge auch die angezeigte.
            numbers = {
                track.track_id: index
                for index, track in enumerate(selected, start=1)
            }
        elif category in GROUP_CATEGORIES:
            key = GROUP_CATEGORIES[category]
            selected = [track for track in tracks if key(track) == group]
        else:
            selected = []
        return build(
            group,
            self._track_entries(
                selected, column=column, ascending=ascending,
                numbers=numbers,
            ),
            EntryKind.TRACK,
        )

    # ------------------------------------------------------------------

    def _history_tracks(self) -> tuple[TrackInfo, ...]:
        """Verlauf, zuletzt gespielt zuerst (S. 42)."""
        by_id = {track.track_id: track for track in self.tracks()}
        out = []
        for track_id in reversed(self.history_ids()):
            track = by_id.get(track_id)
            if track is not None and track not in out:
                out.append(track)
        return tuple(out)

    def _tag_tracks(self) -> tuple[TrackInfo, ...]:
        """Tracks der Tag List, in der Reihenfolge des Hinzufuegens.

        Aufgeloest wird ueber die Trackliste dieser Quelle. Eine Kennung,
        die es hier nicht (mehr) gibt - etwa nach einem Stickwechsel -
        faellt still weg, statt eine ungueltige Zeile zu erzeugen.
        """
        by_id = {track.track_id: track for track in self.tracks()}
        return tuple(
            track
            for track in (by_id.get(track_id) for track_id in self.tag_ids())
            if track is not None
        )

    def _category_count(self, name: str) -> int:
        if name == "TRACK":
            return len(self.tracks())
        if name == "HISTORY":
            return len(self._history_tracks())
        if name == PLAYLIST_CATEGORY:
            return len(self.playlists())
        if name == TAG_LIST_CATEGORY:
            return len(self._tag_tracks())
        key = GROUP_CATEGORIES[name]
        return len({key(t) for t in self.tracks() if key(t)})


@dataclass
class MediaLibrary:
    """Alle Quellen zusammen - das, was der SOURCE-Bildschirm zeigt."""

    libraries: list[TrackListLibrary] = field(default_factory=list)

    def add(self, library: TrackListLibrary) -> TrackListLibrary:
        self.libraries = [
            existing for existing in self.libraries
            if existing.source_id != library.source_id
        ]
        self.libraries.append(library)
        return library

    def remove(self, source_id: str) -> TrackListLibrary | None:
        """Eine Quelle entfernen - z. B. nach dem Abziehen eines Sticks.

        Gibt die entfernte Quelle zurueck, damit der Aufrufer sehen kann,
        ob wirklich etwas weggefallen ist. Eine unbekannte Kennung ist kein
        Fehler.
        """
        for index, library in enumerate(self.libraries):
            if library.source_id == source_id:
                return self.libraries.pop(index)
        return None

    # ------------------------------------------------------------------

    def sources(self) -> tuple[SourceInfo, ...]:
        return tuple(library.info for library in self.libraries)

    def library(self, source_id: str) -> TrackListLibrary | None:
        for library in self.libraries:
            if library.source_id == source_id:
                return library
        if self.libraries and not source_id:
            return self.libraries[0]
        return None

    def default_source_id(self) -> str:
        """Erste Quelle, die wirklich Tracks hat."""
        for library in self.libraries:
            if library.tracks():
                return library.source_id
        return self.libraries[0].source_id if self.libraries else ""

    def node(
        self,
        source_id: str,
        path: Sequence[str] = (),
        *,
        column: BrowseColumn = BrowseColumn.NUMBER,
        ascending: bool = True,
    ) -> BrowseNode:
        library = self.library(source_id)
        if library is None:
            return BrowseNode(source_id=source_id, path=tuple(path))
        return library.node(path, column=column, ascending=ascending)

    def tracks(self, source_id: str = "") -> tuple[TrackInfo, ...]:
        library = self.library(source_id)
        return library.tracks() if library is not None else ()

    def track(self, track_id: str) -> TrackInfo | None:
        for library in self.libraries:
            track = library.track(track_id)
            if track is not None:
                return track
        return None
