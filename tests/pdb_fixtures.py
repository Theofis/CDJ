"""Baukasten fuer synthetische ``export.pdb``-Dateien.

Warum synthetisch: ein echter rekordbox-Stick steht auf einem Testrechner
nicht zuverlaessig zur Verfuegung, und ein Test darf nicht davon abhaengen,
welches Laufwerk gerade steckt. Gebaut wird deshalb genau das Format, das
``pdb_header``/``pdb_pages``/``pdb_rows`` dokumentieren - dieselbe
Spezifikation, aus der Sicht des Schreibers statt des Lesers.

Wo die Struktur an einem echten Export ueberprueft wurde, steht es an der
jeweiligen Stelle. Geraten wird hier nichts; was der Leser nicht auswertet
(``unknown``-Felder), bleibt null.

Aufbau der erzeugten Datei::

    Seite 0           Kopf der Datei (Tabellenverzeichnis)
    Seite 1, 3, 5...  Indexseite je Tabelle (ohne Zeilen)
    Seite 2, 4, 6...  Datenseite je Tabelle (die Zeilen)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from virtual_cdj.media_library.rekordbox.pdb_header import (
    HEADER_SIZE,
    TABLE_POINTER_SIZE,
    PdbTableType,
)
from virtual_cdj.media_library.rekordbox.pdb_pages import (
    PAGE_FLAG_INDEX,
    PAGE_HEADER_SIZE,
    ROW_GROUP_SIZE,
    ROWS_PER_GROUP,
)

PAGE_SIZE = 4096


# ----------------------------------------------------------------------
# DeviceSQL-Strings
# ----------------------------------------------------------------------


def short_string(text: str) -> bytes:
    """Kurzer ASCII-String: erstes Byte ``(Laenge << 1) | 1``.

    Die Laenge zaehlt das Laengenbyte mit - so steht es in ``devicesql.py``
    und in allen drei Quellen.
    """
    raw = text.encode("ascii", errors="replace")
    length = len(raw) + 1
    if length > 0x7F:
        raise ValueError("zu lang fuer einen kurzen String")
    return bytes([(length << 1) | 1]) + raw


# ----------------------------------------------------------------------
# Zeilen
# ----------------------------------------------------------------------


@dataclass
class TrackSpec:
    """Die Felder, die ``parse_track_row`` wirklich liest."""

    id: int
    title: str = ""
    file_path: str = ""
    file_name: str = ""
    comment: str = ""
    analyze_path: str = ""
    date_added: str = ""
    artist_id: int = 0
    album_id: int = 0
    genre_id: int = 0
    label_id: int = 0
    key_id: int = 0
    artwork_id: int = 0
    tempo_x100: int = 0
    rating: int = 0
    duration_s: int = 0
    year: int = 0
    track_number: int = 0
    file_type_id: int = 1


#: Zeilenlaenge bis zu den String-Offsets: Kopf (0x5e) + 21 * 2 Byte.
TRACK_FIXED_SIZE = 0x5E + 21 * 2


def track_row(spec: TrackSpec) -> bytes:
    """Eine Track-Zeile bauen."""
    row = bytearray(TRACK_FIXED_SIZE)

    def u4(at: int, value: int) -> None:
        struct.pack_into("<I", row, at, value)

    def u2(at: int, value: int) -> None:
        struct.pack_into("<H", row, at, value)

    u4(0x08, 44100)                      # sample_rate
    u4(0x10, 0)                          # file_size
    u4(0x1C, spec.artwork_id)
    u4(0x20, spec.key_id)
    u4(0x28, spec.label_id)
    u4(0x30, 320)                        # bitrate
    u4(0x34, spec.track_number)
    u4(0x38, spec.tempo_x100)
    u4(0x3C, spec.genre_id)
    u4(0x40, spec.album_id)
    u4(0x44, spec.artist_id)
    u4(0x48, spec.id)
    u2(0x50, spec.year)
    u2(0x54, spec.duration_s)
    row[0x58] = 0                        # color_id
    row[0x59] = spec.rating
    u2(0x5A, spec.file_type_id)

    # Die Strings liegen hinter dem festen Teil. Die Offsets sind
    # zeilenrelativ (u2 je Platz, 21 Plaetze ab 0x5e).
    texts = {
        10: spec.date_added,
        14: spec.analyze_path,
        16: spec.comment,
        17: spec.title,
        19: spec.file_name,
        20: spec.file_path,
    }
    blob = bytearray()
    empty_at = TRACK_FIXED_SIZE          # ein gemeinsamer Leerstring
    blob += short_string("")
    for index in range(21):
        text = texts.get(index, "")
        if not text:
            u2(0x5E + 2 * index, empty_at)
            continue
        u2(0x5E + 2 * index, TRACK_FIXED_SIZE + len(blob))
        blob += short_string(text)
    return bytes(row) + bytes(blob)


def named_row(row_id: int, name: str) -> bytes:
    """Zeile mit ``u4 id`` und Name ab 0x04 - Genre, Label, Artwork."""
    return struct.pack("<I", row_id) + short_string(name)


def key_row(row_id: int, name: str) -> bytes:
    """Tonart: ``u4 id``, ``u4 id`` noch einmal, dann der Name ab 0x08."""
    return struct.pack("<II", row_id, row_id) + short_string(name)


def color_row(row_id: int, name: str) -> bytes:
    """Farbe: ``u2 id`` bei 0x05, Name ab 0x08."""
    row = bytearray(8)
    struct.pack_into("<H", row, 0x05, row_id)
    return bytes(row) + short_string(name)


def artist_row(row_id: int, name: str) -> bytes:
    """Interpret mit nahem Namensoffset (``subtype`` ohne 0x04)."""
    row = bytearray(0x0A)
    struct.pack_into("<H", row, 0x00, 0x0060)   # subtype, kein fernes Offset
    struct.pack_into("<I", row, 0x04, row_id)
    row[0x09] = 0x0A                            # Name beginnt bei 0x0a
    return bytes(row) + short_string(name)


def album_row(row_id: int, name: str) -> bytes:
    """Album mit nahem Namensoffset."""
    row = bytearray(0x16)
    struct.pack_into("<H", row, 0x00, 0x0080)
    struct.pack_into("<I", row, 0x0C, row_id)
    row[0x15] = 0x16
    return bytes(row) + short_string(name)


def playlist_tree_row(
    row_id: int, parent_id: int, sort_order: int, is_folder: bool, name: str
) -> bytes:
    return (
        struct.pack(
            "<IIIII", parent_id, 0, sort_order, row_id, 1 if is_folder else 0
        )
        + short_string(name)
    )


def playlist_entry_row(
    entry_index: int, track_id: int, playlist_id: int
) -> bytes:
    return struct.pack("<III", entry_index, track_id, playlist_id)


def history_entry_row(
    track_id: int, playlist_id: int, entry_index: int
) -> bytes:
    return struct.pack("<III", track_id, playlist_id, entry_index)


# ----------------------------------------------------------------------
# Seiten
# ----------------------------------------------------------------------


@dataclass
class Table:
    """Eine Tabelle mit ihren Zeilen."""

    table_type: int
    rows: list[bytes] = field(default_factory=list)
    #: Slots, die als geloescht markiert werden (Index in ``rows``). Damit
    #: laesst sich pruefen, dass geloeschte Zeilen wirklich uebersprungen
    #: werden und nicht als echte Tracks auftauchen.
    deleted: set[int] = field(default_factory=set)


def build_page(
    index: int, table: Table, next_page: int, *, is_index: bool = False
) -> bytes:
    """Eine Seite bauen - Kopf, Heap und rueckwaerts wachsendes Verzeichnis."""
    page = bytearray(PAGE_SIZE)
    struct.pack_into("<I", page, 0x04, index)
    struct.pack_into("<I", page, 0x08, table.table_type)
    struct.pack_into("<I", page, 0x0C, next_page)
    struct.pack_into("<I", page, 0x10, 1)          # sequence

    rows = [] if is_index else table.rows
    slot_count = len(rows)
    present_count = slot_count - len(table.deleted)
    counts = (slot_count & 0x1FFF) | ((present_count & 0x7FF) << 13)
    page[0x18:0x1B] = counts.to_bytes(3, "little")
    page[0x1B] = PAGE_FLAG_INDEX if is_index else 0

    if is_index or not rows:
        return bytes(page)

    # Heap: Zeilen ab 0x28, jede auf ein Vielfaches von 4 ausgerichtet.
    heap: list[int] = []
    cursor = PAGE_HEADER_SIZE
    for row in rows:
        if cursor + len(row) > PAGE_SIZE - ROW_GROUP_SIZE * 4:
            raise ValueError(
                "Testdaten passen nicht auf eine Seite - der Baukasten "
                "schreibt bewusst nur einseitige Tabellen"
            )
        page[cursor:cursor + len(row)] = row
        heap.append(cursor - PAGE_HEADER_SIZE)
        cursor += (len(row) + 3) & ~3

    # Verzeichnis, gruppenweise rueckwaerts vom Seitenende.
    for slot, heap_offset in enumerate(heap):
        group, within = divmod(slot, ROWS_PER_GROUP)
        base = PAGE_SIZE - group * ROW_GROUP_SIZE
        struct.pack_into("<H", page, base - 6 - 2 * within, heap_offset)
    for group in range((slot_count - 1) // ROWS_PER_GROUP + 1):
        base = PAGE_SIZE - group * ROW_GROUP_SIZE
        mask = 0
        for within in range(ROWS_PER_GROUP):
            slot = group * ROWS_PER_GROUP + within
            if slot < slot_count and slot not in table.deleted:
                mask |= 1 << within
        struct.pack_into("<H", page, base - 4, mask)
    return bytes(page)


def build_pdb(tables: list[Table], *, sequence: int = 1) -> bytes:
    """Eine vollstaendige ``export.pdb`` aus den gegebenen Tabellen."""
    header = bytearray(HEADER_SIZE + len(tables) * TABLE_POINTER_SIZE)
    struct.pack_into("<I", header, 0x04, PAGE_SIZE)
    struct.pack_into("<I", header, 0x08, len(tables))
    struct.pack_into("<I", header, 0x14, sequence)

    pages: list[bytes] = []
    page_index = 1
    for position, table in enumerate(tables):
        first_page = page_index          # Indexseite
        data_page = page_index + 1
        pages.append(
            build_page(first_page, table, data_page, is_index=True)
        )
        pages.append(build_page(data_page, table, data_page + 1))
        struct.pack_into(
            "<IIII",
            header,
            HEADER_SIZE + position * TABLE_POINTER_SIZE,
            table.table_type, 0, first_page, data_page,
        )
        page_index += 2

    first = bytearray(PAGE_SIZE)
    first[0:len(header)] = header
    return bytes(first) + b"".join(pages)


# ----------------------------------------------------------------------
# Fertige Bibliotheken
# ----------------------------------------------------------------------


def simple_library() -> bytes:
    """Eine kleine, vollstaendige Bibliothek.

    3 Tracks, 2 Interpreten, 1 Genre, 1 Tonart, ein Playlist-Ordner mit
    zwei Playlists darin und eine Playlist auf oberster Ebene, dazu eine
    Verlaufsliste. Der dritte Track ist geloescht und darf nicht auftauchen.
    """
    tracks = Table(
        PdbTableType.TRACKS,
        rows=[
            track_row(TrackSpec(
                id=1, title="Erster Track", artist_id=1, genre_id=1,
                key_id=1, tempo_x100=12800, duration_s=210, rating=4,
                year=2024, file_path="/Contents/A/eins.mp3",
                file_name="eins.mp3", comment="ein Kommentar",
                analyze_path="/PIONEER/USBANLZ/P001/00000001/ANLZ0000.DAT",
                date_added="2026-01-15",
            )),
            track_row(TrackSpec(
                id=2, title="Zweiter Track", artist_id=2, genre_id=1,
                tempo_x100=14000, duration_s=180,
                file_path="/Contents/B/zwei.mp3", file_name="zwei.mp3",
            )),
            track_row(TrackSpec(
                id=3, title="Geloeschter Track",
                file_path="/Contents/C/drei.mp3", file_name="drei.mp3",
            )),
        ],
        deleted={2},
    )
    return build_pdb([
        tracks,
        Table(PdbTableType.ARTISTS, rows=[
            artist_row(1, "Interpret Eins"),
            artist_row(2, "Interpret Zwei"),
        ]),
        Table(PdbTableType.ALBUMS, rows=[album_row(1, "Album Eins")]),
        Table(PdbTableType.GENRES, rows=[named_row(1, "Techno")]),
        Table(PdbTableType.LABELS, rows=[named_row(1, "Label Eins")]),
        Table(PdbTableType.KEYS, rows=[key_row(1, "8A")]),
        Table(PdbTableType.COLORS, rows=[color_row(1, "Pink")]),
        Table(PdbTableType.ARTWORK, rows=[
            named_row(1, "/PIONEER/Artwork/00001/a1.jpg"),
        ]),
        Table(PdbTableType.PLAYLIST_TREE, rows=[
            playlist_tree_row(10, 0, 0, True, "Ordner"),
            playlist_tree_row(11, 10, 0, False, "Innen A"),
            playlist_tree_row(12, 10, 1, False, "Innen B"),
            playlist_tree_row(13, 0, 1, False, "Oben"),
        ]),
        Table(PdbTableType.PLAYLIST_ENTRIES, rows=[
            # Absichtlich verdreht eingetragen: der Leser muss nach
            # ``entry_index`` sortieren, nicht nach Zeilenreihenfolge.
            playlist_entry_row(2, 1, 11),
            playlist_entry_row(1, 2, 11),
            playlist_entry_row(1, 1, 12),
            playlist_entry_row(1, 2, 13),
        ]),
        Table(PdbTableType.HISTORY_PLAYLISTS, rows=[
            named_row(20, "HISTORY 001"),
        ]),
        Table(PdbTableType.HISTORY_ENTRIES, rows=[
            history_entry_row(1, 20, 1),
        ]),
    ])


def large_library(count: int = 400) -> bytes:
    """Viele Tracks - fuer den Fall "grosse Bibliothek".

    Die Tracks verteilen sich auf mehrere Seiten; genau das prueft die
    Seitenkette. Eine Seite fasst rund 20 Track-Zeilen.
    """
    rows_per_page = 20
    tables: list[Table] = []
    pages: list[list[bytes]] = []
    for start in range(0, count, rows_per_page):
        pages.append([
            track_row(TrackSpec(
                id=index + 1,
                title=f"Track {index + 1:04d}",
                artist_id=(index % 7) + 1,
                tempo_x100=12000 + (index % 60) * 100,
                duration_s=120 + index % 300,
                file_path=f"/Contents/X/{index + 1:04d}.mp3",
                file_name=f"{index + 1:04d}.mp3",
            ))
            for index in range(start, min(start + rows_per_page, count))
        ])
    tables.append(Table(PdbTableType.TRACKS, rows=pages[0]))
    tables.append(Table(PdbTableType.ARTISTS, rows=[
        artist_row(index, f"Interpret {index}") for index in range(1, 8)
    ]))
    data = bytearray(build_pdb(tables))

    # Weitere Track-Seiten hinten anhaengen und die Kette verlaengern.
    # ``build_pdb`` legt je Tabelle genau eine Datenseite an; hier kommen
    # die restlichen dazu, damit der Test wirklich mehrere Seiten sieht.
    next_index = len(data) // PAGE_SIZE
    chain_from = 2                       # Datenseite der Tracks-Tabelle
    for position, rows in enumerate(pages[1:]):
        table = Table(PdbTableType.TRACKS, rows=rows)
        is_last = position == len(pages) - 2
        data += build_page(
            next_index, table, next_index + 1 if not is_last else next_index
        )
        struct.pack_into("<I", data, chain_from * PAGE_SIZE + 0x0C, next_index)
        chain_from = next_index
        next_index += 1

    # Letzte Seite der Tracks-Tabelle im Tabellenverzeichnis nachtragen.
    struct.pack_into("<I", data, HEADER_SIZE + 0x0C, chain_from)
    return bytes(data)
