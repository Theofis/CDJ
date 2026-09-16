"""Zeilentypen aus ``export.pdb``.

Je Tabelle ein fester Kopf, danach Verweise auf DeviceSQL-Strings. Alle
Offsets sind **relativ zum Zeilenanfang**, alle Zahlen little-endian.

Quellenlage
-----------
Die Offsets stammen aus crate-digger (``rekordbox_pdb.ksy``) und sind
gegen ``FORMAT.md`` von rekordbox-pdb geprueft, das dieselben Zeilen
byte-weise an echten Exporten nachgemessen hat.

Eine echte Abweichung zwischen den Quellen gab es an einer Stelle, und sie
ist entschieden: rekordcrate (Rust) legt das Feld der 21 String-Offsets
auf ``0x5c``, crate-digger und ``FORMAT.md`` auf ``0x5e``. Zwei von drei
Quellen und die einzige, die eine vollstaendige Byte-Karte echter Exporte
zeigt, sagen ``0x5e``; ausserdem steht bei ``0x5c`` laut beiden die
Konstante ``0x0003``, die als String-Offset unmoeglich waere (sie zeigte
mitten in den Zeilenkopf). Deshalb ``0x5e``.

Was unbekannt ist, heisst hier ``unknown`` und wird nicht gedeutet:
``0x04`` (konstant ``0x000C0700``), ``0x14`` (je Track verschieden, ohne
erkannten Zusammenhang), ``0x18``/``0x1a`` (je Datenbank konstant),
``0x56`` (konstant ``0x0029``) und ``0x5c`` (konstant ``0x0003``).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .devicesql import DeviceSqlError, decode_string

#: Offset der 21 String-Offsets in einer Track-Zeile.
TRACK_STRING_OFFSETS_AT = 0x5E
#: Anzahl der String-Plaetze einer Track-Zeile.
TRACK_STRING_COUNT = 21

#: Bedeutung der String-Plaetze, soweit gesichert. Die uebrigen Plaetze
#: sind belegt, ihre Bedeutung ist in keiner Quelle geklaert - sie werden
#: gelesen, aber nicht benannt.
TRACK_STRING_ISRC = 0
TRACK_STRING_MESSAGE = 5
TRACK_STRING_KUVO_PUBLIC = 6
TRACK_STRING_AUTOLOAD_HOT_CUES = 7
TRACK_STRING_DATE_ADDED = 10
TRACK_STRING_RELEASE_DATE = 11
TRACK_STRING_MIX_NAME = 12
TRACK_STRING_ANALYZE_PATH = 14
TRACK_STRING_ANALYZE_DATE = 15
TRACK_STRING_COMMENT = 16
TRACK_STRING_TITLE = 17
TRACK_STRING_FILENAME = 19
TRACK_STRING_FILE_PATH = 20

#: Offset des Bewertungsfelds in der Track-Zeile (ein Byte, 0-5). Der
#: Writer aendert genau dieses Byte an Ort und Stelle.
TRACK_RATING_AT = 0x59
#: Offset der Farbkennung (ein Byte, Verweis in die Farbtabelle).
TRACK_COLOR_AT = 0x58

#: Dateiformat laut Datenbank. Die Zuordnung stammt aus rekordcrate und
#: ``FORMAT.md``; crate-digger nennt das Feld unbekannt. Unbekannte Werte
#: werden als Zahl durchgereicht statt geraten.
FILE_TYPES: dict[int, str] = {
    0: "",
    1: "MP3",
    4: "M4A",
    5: "FLAC",
    0x0B: "WAV",
    0x0C: "AIFF",
}

#: Bit im ``subtype`` von Artist-/Album-Zeilen: gesetzt = der Name liegt
#: weiter weg, als ein Ein-Byte-Offset reicht, und ein u2-Offset folgt.
SUBTYPE_FAR_OFFSET = 0x04


class PdbRowError(ValueError):
    """Eine Zeile ist nicht lesbar - zu kurz oder unplausibel."""


def _u1(data: bytes, at: int) -> int:
    _need(data, at, 1)
    return data[at]


def _u2(data: bytes, at: int) -> int:
    _need(data, at, 2)
    return struct.unpack_from("<H", data, at)[0]


def _u4(data: bytes, at: int) -> int:
    _need(data, at, 4)
    return struct.unpack_from("<I", data, at)[0]


def _need(data: bytes, at: int, size: int) -> None:
    if at < 0 or at + size > len(data):
        raise PdbRowError(
            f"Zeile reicht ueber die Seite hinaus (Offset {at}, "
            f"{size} Byte, Seite {len(data)} Byte)"
        )


def _string(data: bytes, row: int, at: int) -> str:
    """String an einem zeilenrelativen Offset lesen.

    Ein einzelner unlesbarer String macht die Zeile nicht unbrauchbar - er
    wird leer. Titel fehlt ist besser als Track fehlt.
    """
    try:
        return decode_string(data, row + at).text
    except DeviceSqlError:
        return ""


# --------------------------------------------------------------------------
# Track
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackRow:
    """Eine Zeile der Tabelle ``tracks``."""

    id: int
    #: Verweise in die Nachbartabellen. ``0`` heisst "nicht gesetzt".
    artist_id: int = 0
    album_id: int = 0
    genre_id: int = 0
    label_id: int = 0
    key_id: int = 0
    artwork_id: int = 0
    remixer_id: int = 0
    composer_id: int = 0
    original_artist_id: int = 0
    color_id: int = 0

    #: Tempo in Hundertstel-BPM, so wie es in der Datei steht.
    tempo: int = 0
    rating: int = 0
    duration_s: int = 0
    year: int = 0
    track_number: int = 0
    disc_number: int = 0
    play_count: int = 0
    bitrate: int = 0
    sample_rate: int = 0
    sample_depth: int = 0
    file_size: int = 0
    file_type_id: int = 0

    strings: tuple[str, ...] = ()

    @property
    def bpm(self) -> float:
        return self.tempo / 100.0

    @property
    def file_type(self) -> str:
        """Klartext, sonst die rohe Zahl - nie ein geratenes Format."""
        known = FILE_TYPES.get(self.file_type_id)
        if known is not None:
            return known
        return f"0x{self.file_type_id:02x}"

    def string(self, index: int) -> str:
        if 0 <= index < len(self.strings):
            return self.strings[index]
        return ""

    @property
    def title(self) -> str:
        return self.string(TRACK_STRING_TITLE)

    @property
    def file_path(self) -> str:
        return self.string(TRACK_STRING_FILE_PATH)

    @property
    def file_name(self) -> str:
        return self.string(TRACK_STRING_FILENAME)

    @property
    def comment(self) -> str:
        return self.string(TRACK_STRING_COMMENT)

    @property
    def analyze_path(self) -> str:
        return self.string(TRACK_STRING_ANALYZE_PATH)

    @property
    def analyze_date(self) -> str:
        return self.string(TRACK_STRING_ANALYZE_DATE)

    @property
    def date_added(self) -> str:
        return self.string(TRACK_STRING_DATE_ADDED)

    @property
    def release_date(self) -> str:
        return self.string(TRACK_STRING_RELEASE_DATE)

    @property
    def mix_name(self) -> str:
        return self.string(TRACK_STRING_MIX_NAME)

    @property
    def isrc(self) -> str:
        return self.string(TRACK_STRING_ISRC)

    @property
    def autoload_hot_cues(self) -> bool:
        return self.string(TRACK_STRING_AUTOLOAD_HOT_CUES).upper() == "ON"


def parse_track_row(data: bytes, row: int) -> TrackRow:
    """Track-Zeile ab ``row`` lesen."""
    offsets_at = row + TRACK_STRING_OFFSETS_AT
    _need(data, offsets_at, 2 * TRACK_STRING_COUNT)

    strings: list[str] = []
    for index in range(TRACK_STRING_COUNT):
        at = _u2(data, offsets_at + 2 * index)
        strings.append(_string(data, row, at))

    return TrackRow(
        id=_u4(data, row + 0x48),
        artist_id=_u4(data, row + 0x44),
        album_id=_u4(data, row + 0x40),
        genre_id=_u4(data, row + 0x3C),
        label_id=_u4(data, row + 0x28),
        key_id=_u4(data, row + 0x20),
        artwork_id=_u4(data, row + 0x1C),
        remixer_id=_u4(data, row + 0x2C),
        composer_id=_u4(data, row + 0x0C),
        original_artist_id=_u4(data, row + 0x24),
        color_id=_u1(data, row + TRACK_COLOR_AT),
        tempo=_u4(data, row + 0x38),
        rating=_u1(data, row + TRACK_RATING_AT),
        duration_s=_u2(data, row + 0x54),
        year=_u2(data, row + 0x50),
        track_number=_u4(data, row + 0x34),
        disc_number=_u2(data, row + 0x4C),
        play_count=_u2(data, row + 0x4E),
        bitrate=_u4(data, row + 0x30),
        sample_rate=_u4(data, row + 0x08),
        sample_depth=_u2(data, row + 0x52),
        file_size=_u4(data, row + 0x10),
        file_type_id=_u2(data, row + 0x5A),
        strings=tuple(strings),
    )


# --------------------------------------------------------------------------
# Zeilen, die nur eine ID und einen Namen tragen
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NamedRow:
    """``u4 id`` und ein Name - Genre, Label, Artwork, Verlaufsliste."""

    id: int
    name: str


def parse_named_row(data: bytes, row: int) -> NamedRow:
    return NamedRow(id=_u4(data, row + 0x00), name=_string(data, row, 0x04))


def parse_key_row(data: bytes, row: int) -> NamedRow:
    """Tonart: ``u4 id``, ``u4 id`` noch einmal, dann der Name."""
    return NamedRow(id=_u4(data, row + 0x00), name=_string(data, row, 0x08))


def parse_color_row(data: bytes, row: int) -> NamedRow:
    """Farbe: die ID ist hier ein ``u2`` bei 0x05, der Name steht bei 0x08."""
    return NamedRow(id=_u2(data, row + 0x05), name=_string(data, row, 0x08))


def parse_artist_row(data: bytes, row: int) -> NamedRow:
    """Interpret. Der Name liegt hinter einem nahen oder fernen Offset."""
    subtype = _u2(data, row + 0x00)
    if subtype & SUBTYPE_FAR_OFFSET:
        at = _u2(data, row + 0x0A)
    else:
        at = _u1(data, row + 0x09)
    return NamedRow(id=_u4(data, row + 0x04), name=_string(data, row, at))


def parse_album_row(data: bytes, row: int) -> NamedRow:
    """Album. Wie beim Interpreten, nur weiter hinten in der Zeile."""
    subtype = _u2(data, row + 0x00)
    if subtype & SUBTYPE_FAR_OFFSET:
        at = _u2(data, row + 0x16)
    else:
        at = _u1(data, row + 0x15)
    return NamedRow(id=_u4(data, row + 0x0C), name=_string(data, row, at))


# --------------------------------------------------------------------------
# Playlists
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlaylistTreeRow:
    """Ein Knoten des Playlist-Baums: Ordner oder Playlist."""

    id: int
    parent_id: int
    sort_order: int
    is_folder: bool
    name: str


def parse_playlist_tree_row(data: bytes, row: int) -> PlaylistTreeRow:
    return PlaylistTreeRow(
        parent_id=_u4(data, row + 0x00),
        sort_order=_u4(data, row + 0x08),
        id=_u4(data, row + 0x0C),
        is_folder=_u4(data, row + 0x10) != 0,
        name=_string(data, row, 0x14),
    )


@dataclass(frozen=True)
class PlaylistEntryRow:
    """Ein Track an einer Position einer Playlist."""

    entry_index: int
    track_id: int
    playlist_id: int


def parse_playlist_entry_row(data: bytes, row: int) -> PlaylistEntryRow:
    return PlaylistEntryRow(
        entry_index=_u4(data, row + 0x00),
        track_id=_u4(data, row + 0x04),
        playlist_id=_u4(data, row + 0x08),
    )


def parse_history_entry_row(data: bytes, row: int) -> PlaylistEntryRow:
    """Verlaufseintrag - dieselben drei Zahlen, andere Reihenfolge."""
    return PlaylistEntryRow(
        track_id=_u4(data, row + 0x00),
        playlist_id=_u4(data, row + 0x04),
        entry_index=_u4(data, row + 0x08),
    )
