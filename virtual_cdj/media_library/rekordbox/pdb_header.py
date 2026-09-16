"""Kopf und Tabellenzeiger von ``export.pdb`` (DeviceSQL-Format).

**Nur der Dateikopf** - keine Seiten- oder Zeileninhalte. Das genuegt fuer
Phase 1 (Struktur erkennen und pruefen, ob die Datei plausibel ist); Phase 2
baut auf ``PdbHeader.tables`` auf und liest die Tabellenseiten wirklich.

Quelle der Feldnamen und Offsets
---------------------------------
``export.pdb`` ist nicht offiziell dokumentiert. Die hier verwendeten Felder
stammen aus der Reverse-Engineering-Dokumentation von Deep Symmetry
("DJ Link Ecosystem Analysis", Abschnitt "Database Exports",
https://djl-analysis.deepsymmetry.org/rekordbox-export-analysis/exports.html)
und wurden gegen zwei unabhaengige Implementierungen desselben Formats
gegengeprueft: rekordcrate (Rust, https://github.com/Holzhaus/rekordcrate)
und rekordbox-pdb (Python, https://github.com/fragmede/rekordbox-pdb). Alle
drei stimmen fuer die hier genutzten Felder ueberein.

Was **nicht** sicher bekannt ist, wird als ``unknown``/``_reserved``
benannt und nicht interpretiert - siehe docs/rekordbox-usb-import.md,
Abschnitt "Quellen und Sicherheit der Daten".

Layout des Dateikopfs (28 Byte, dann die Tabellenzeiger)::

    Offset  Groesse  Feld
    0x00    4        Nullbytes (Pruefwert: muss 0 sein)
    0x04    4        page_size        - Seitengroesse in Byte
    0x08    4        num_tables       - Anzahl Tabellen
    0x0c    4        unknown_next     - unbekannt ("naechste freie Seite"?)
    0x10    4        unknown_gap      - unbekannt, in keiner der drei Quellen
                                         benannt
    0x14    4        sequence         - Aenderungszaehler der Datenbank
    0x18    4        Nullbytes/Reserve
    0x1c    -        Tabellenzeiger, ``num_tables`` * 16 Byte

Tabellenzeiger (16 Byte je Eintrag)::

    Offset  Groesse  Feld
    0x00    4        table_type   - siehe ``PdbTableType``
    0x04    4        unknown      - unbekannt ("empty_candidate"?)
    0x08    4        first_page   - erste Seite dieser Tabelle
    0x0c    4        last_page    - letzte Seite dieser Tabelle
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

#: Laenge des Dateikopfs vor den Tabellenzeigern.
HEADER_SIZE = 0x1C
#: Laenge eines einzelnen Tabellenzeigers.
TABLE_POINTER_SIZE = 16

#: Grenzwerte fuer die Plausibilitaetspruefung. Keine echte export.pdb liegt
#: ausserhalb dieser Bereiche; sie dienen nur dazu, eine beschaedigte oder
#: fremde Datei zu erkennen, bevor blind Speicher gelesen wird.
_MIN_PAGE_SIZE = 64
_MAX_PAGE_SIZE = 1_048_576
_MAX_TABLES = 64


class PdbTableType(IntEnum):
    """Bekannte Tabellentypen in ``export.pdb`` (siehe Quellenangabe oben).

    ``exportExt.pdb`` (MyTags) verwendet dieselben Zahlenwerte fuer andere
    Tabellen (``tags``/``tag_tracks``) - das betrifft dieses Projekt nicht,
    solange nur ``export.pdb`` gelesen wird.
    """

    TRACKS = 0
    GENRES = 1
    ARTISTS = 2
    ALBUMS = 3
    LABELS = 4
    KEYS = 5
    COLORS = 6
    PLAYLIST_TREE = 7
    PLAYLIST_ENTRIES = 8
    #: Korrektur gegenueber dem ersten Entwurf dieses Moduls: die
    #: Verlaufstabellen sind 11 und 12, nicht 17 und 18. Bestaetigt von
    #: allen drei Quellen (crate-digger ``page_type``, rekordcrate
    #: ``PlainPageType``, ``FORMAT.md``); 17 und 18 sind in echten
    #: Exporten vorhanden, ihr Inhalt ist aber unbekannt.
    HISTORY_PLAYLISTS = 11
    HISTORY_ENTRIES = 12
    ARTWORK = 13
    COLUMNS = 16
    HISTORY = 19


@dataclass(frozen=True)
class PdbTablePointer:
    """Ein Eintrag aus dem Tabellenverzeichnis des Dateikopfs."""

    table_type: int
    first_page: int
    last_page: int

    @property
    def known_type(self) -> PdbTableType | None:
        """``None``, wenn der Zahlenwert keinem bekannten Tabellentyp
        entspricht - kommt bei neueren rekordbox-Versionen vor und ist kein
        Fehler."""
        try:
            return PdbTableType(self.table_type)
        except ValueError:
            return None


@dataclass(frozen=True)
class PdbHeader:
    """Dateikopf von ``export.pdb`` - Seitengroesse und Tabellenverzeichnis."""

    page_size: int
    num_tables: int
    sequence: int
    tables: tuple[PdbTablePointer, ...]

    def table(self, kind: PdbTableType) -> PdbTablePointer | None:
        """Tabellenzeiger eines bestimmten Typs, falls vorhanden."""
        for pointer in self.tables:
            if pointer.table_type == kind:
                return pointer
        return None

    @property
    def known_table_types(self) -> tuple[PdbTableType, ...]:
        return tuple(
            kind
            for pointer in self.tables
            if (kind := pointer.known_type) is not None
        )


class PdbFormatError(ValueError):
    """Datei ist keine gueltige ``export.pdb`` - beschaedigt oder anderes
    Format. Wird von Aufrufern abgefangen; niemals ein Absturz der
    Erkennung (Abschnitt 17, Fehlerbehandlung)."""


def parse_header(path: str | Path) -> PdbHeader:
    """Nur den Dateikopf lesen und plausibilisieren.

    Raises:
        PdbFormatError: Datei zu klein, Pruefwert falsch, oder
            Seitengroesse/Tabellenzahl unplausibel. Enthaelt einen fuer
            Menschen lesbaren Grund (Debug-Ansicht, Abschnitt 18).
    """
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise PdbFormatError(f"Datei nicht lesbar: {error}") from error

    if len(data) < HEADER_SIZE:
        raise PdbFormatError(
            f"Datei zu klein fuer einen export.pdb-Kopf "
            f"({len(data)} von mindestens {HEADER_SIZE} Byte)"
        )

    (zero,) = struct.unpack_from("<I", data, 0x00)
    if zero != 0:
        raise PdbFormatError(
            "Kopf beginnt nicht mit vier Nullbytes - keine export.pdb"
        )

    (page_size,) = struct.unpack_from("<I", data, 0x04)
    (num_tables,) = struct.unpack_from("<I", data, 0x08)
    (sequence,) = struct.unpack_from("<I", data, 0x14)

    if not (_MIN_PAGE_SIZE <= page_size <= _MAX_PAGE_SIZE):
        raise PdbFormatError(f"unplausible Seitengroesse: {page_size} Byte")
    if num_tables > _MAX_TABLES:
        raise PdbFormatError(f"unplausible Tabellenzahl: {num_tables}")

    needed = HEADER_SIZE + num_tables * TABLE_POINTER_SIZE
    if len(data) < needed:
        raise PdbFormatError(
            "Datei endet mitten im Tabellenverzeichnis - "
            f"{len(data)} von {needed} Byte vorhanden"
        )

    tables = []
    offset = HEADER_SIZE
    for _ in range(num_tables):
        table_type, _unknown, first_page, last_page = struct.unpack_from(
            "<IIII", data, offset
        )
        tables.append(
            PdbTablePointer(
                table_type=table_type,
                first_page=first_page,
                last_page=last_page,
            )
        )
        offset += TABLE_POINTER_SIZE

    return PdbHeader(
        page_size=page_size,
        num_tables=num_tables,
        sequence=sequence,
        tables=tuple(tables),
    )
