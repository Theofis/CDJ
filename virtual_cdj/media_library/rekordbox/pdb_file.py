"""``export.pdb`` als Ganzes lesen - der Reader der Datenbank.

Setzt Kopf (``pdb_header``), Seiten (``pdb_pages``) und Zeilen
(``pdb_rows``) zusammen und liefert je Tabelle die gueltigen Zeilen. Es
wird **nichts** gedeutet und nichts mit Analysedateien verknuepft - das
macht erst ``reader.py``.

Seitenkette je Tabelle
----------------------
Der Dateikopf nennt je Tabelle die erste und die letzte Seite. Von der
ersten aus zeigt jede Seite auf die naechste. Dabei gilt (an echten
Exporten beobachtet und in ``FORMAT.md`` festgehalten):

* Die **erste** Seite einer Tabelle ist eine Indexseite ohne Zeilen; die
  Daten stehen erst ab der naechsten.
* Hinter der letzten Seite zeigt ``next_page`` auf eine bereits
  vorbereitete, aber leere Seite ("empty candidate"). Die kann komplett
  jenseits des Dateiendes liegen, weil rekordbox die Datei nur bis zur
  letzten wirklich beschriebenen Seite anlegt.

Deshalb endet der Durchlauf an mehreren Stellen: bei der letzten Seite,
bei einem Seitentyp, der nicht mehr passt, bei einer Seite ausserhalb der
Datei - und bei einer Seite, die schon einmal besucht wurde. Das letzte
ist eine Schleifensicherung: eine beschaedigte Datei darf das Programm
nicht zum Haengen bringen.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .pdb_header import PdbFormatError, PdbHeader, PdbTableType, parse_header
from .pdb_pages import PdbPage, PdbPageError, RowSlot, parse_page
from .pdb_rows import (
    NamedRow,
    PdbRowError,
    PlaylistEntryRow,
    PlaylistTreeRow,
    TrackRow,
    parse_album_row,
    parse_artist_row,
    parse_color_row,
    parse_history_entry_row,
    parse_key_row,
    parse_named_row,
    parse_playlist_entry_row,
    parse_playlist_tree_row,
    parse_track_row,
)

log = logging.getLogger(__name__)

#: Obergrenze fuer die Seitenkette einer Tabelle. Eine echte Datenbank
#: bleibt weit darunter; die Grenze faengt nur eine kaputte Kette ab.
MAX_PAGES_PER_TABLE = 100_000


@dataclass
class PdbDatabase:
    """Eine geoeffnete ``export.pdb``.

    Die Datei wird einmal vollstaendig gelesen und im Speicher gehalten.
    Ein voller Bibliotheksexport liegt im einstelligen Megabytebereich -
    das ist billiger als tausende Einzelzugriffe auf einen USB-Stick, und
    der Writer braucht ohnehin den unveraenderten Originalstand.
    """

    path: Path
    header: PdbHeader
    data: bytes
    #: Klartext-Hinweise auf uebersprungene Seiten oder Zeilen. Leer heisst
    #: "vollstaendig gelesen".
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------

    @classmethod
    def open(cls, path: str | Path) -> PdbDatabase:
        """Datenbank oeffnen und den Kopf pruefen.

        Raises:
            PdbFormatError: Datei fehlt, ist zu klein oder hat keinen
                gueltigen Kopf.
        """
        file_path = Path(path)
        header = parse_header(file_path)
        try:
            data = file_path.read_bytes()
        except OSError as error:
            raise PdbFormatError(f"Datei nicht lesbar: {error}") from error
        log.debug(
            "export.pdb geoeffnet: %s, %d Byte, %d Tabellen, Sequenz %d",
            file_path, len(data), header.num_tables, header.sequence,
        )
        return cls(path=file_path, header=header, data=data)

    @property
    def page_size(self) -> int:
        return self.header.page_size

    @property
    def page_count(self) -> int:
        return len(self.data) // self.page_size

    # ------------------------------------------------------------------
    # Seiten
    # ------------------------------------------------------------------

    def page(self, index: int) -> PdbPage | None:
        """Eine Seite lesen. ``None``, wenn sie ausserhalb der Datei liegt."""
        if index < 0:
            return None
        start = index * self.page_size
        end = start + self.page_size
        if end > len(self.data):
            return None
        try:
            return parse_page(self.data[start:end], index)
        except PdbPageError as error:
            self._warn(str(error))
            return None

    def pages(self, table_type: int) -> Iterator[PdbPage]:
        """Alle Datenseiten einer Tabelle, in Reihenfolge der Kette."""
        pointer = self.header.table(table_type)
        if pointer is None:
            return
        seen: set[int] = set()
        index = pointer.first_page
        for _ in range(MAX_PAGES_PER_TABLE):
            if index in seen:
                self._warn(
                    f"Tabelle {table_type}: Seitenkette laeuft im Kreis "
                    f"bei Seite {index} - Rest uebersprungen"
                )
                return
            seen.add(index)
            page = self.page(index)
            if page is None:
                return
            if page.table_type != table_type:
                return
            if page.is_data_page:
                yield page
            if index == pointer.last_page:
                return
            index = page.next_page
        self._warn(
            f"Tabelle {table_type}: mehr als {MAX_PAGES_PER_TABLE} Seiten"
        )

    def rows(self, table_type: int) -> Iterator[tuple[PdbPage, RowSlot]]:
        """Alle **gueltigen** Zeilen einer Tabelle.

        Geloeschte Zeilen werden ausgelassen - siehe ``pdb_pages``.
        """
        for page in self.pages(table_type):
            try:
                slots = page.present_slots()
            except PdbPageError as error:
                self._warn(str(error))
                continue
            for slot in slots:
                yield page, slot

    # ------------------------------------------------------------------
    # Tabellen
    # ------------------------------------------------------------------

    def tracks(self) -> tuple[TrackRow, ...]:
        return tuple(self._parse_rows(PdbTableType.TRACKS, parse_track_row))

    def artists(self) -> dict[int, str]:
        return self._names(PdbTableType.ARTISTS, parse_artist_row)

    def albums(self) -> dict[int, str]:
        return self._names(PdbTableType.ALBUMS, parse_album_row)

    def genres(self) -> dict[int, str]:
        return self._names(PdbTableType.GENRES, parse_named_row)

    def labels(self) -> dict[int, str]:
        return self._names(PdbTableType.LABELS, parse_named_row)

    def keys(self) -> dict[int, str]:
        return self._names(PdbTableType.KEYS, parse_key_row)

    def colors(self) -> dict[int, str]:
        return self._names(PdbTableType.COLORS, parse_color_row)

    def artwork(self) -> dict[int, str]:
        return self._names(PdbTableType.ARTWORK, parse_named_row)

    def playlist_tree(self) -> tuple[PlaylistTreeRow, ...]:
        return tuple(
            self._parse_rows(
                PdbTableType.PLAYLIST_TREE, parse_playlist_tree_row
            )
        )

    def playlist_entries(self) -> tuple[PlaylistEntryRow, ...]:
        return tuple(
            self._parse_rows(
                PdbTableType.PLAYLIST_ENTRIES, parse_playlist_entry_row
            )
        )

    def history_playlists(self) -> dict[int, str]:
        return self._names(PdbTableType.HISTORY_PLAYLISTS, parse_named_row)

    def history_entries(self) -> tuple[PlaylistEntryRow, ...]:
        return tuple(
            self._parse_rows(
                PdbTableType.HISTORY_ENTRIES, parse_history_entry_row
            )
        )

    # ------------------------------------------------------------------

    def _parse_rows(self, table_type: int, parse) -> Iterator:
        """Zeilen einer Tabelle lesen; kaputte einzeln ueberspringen.

        Eine unlesbare Zeile darf nicht die ganze Tabelle kosten. Was
        uebersprungen wurde, steht danach in ``warnings``.
        """
        skipped = 0
        for page, slot in self.rows(table_type):
            try:
                yield parse(page.data, slot.offset)
            except (PdbRowError, ValueError, struct.error) as error:
                skipped += 1
                log.debug(
                    "Tabelle %s, Seite %d, Zeile %d uebersprungen: %s",
                    table_type, page.index, slot.slot, error,
                )
        if skipped:
            self._warn(
                f"Tabelle {table_type}: {skipped} unlesbare Zeile(n) "
                f"uebersprungen"
            )

    def _names(self, table_type: int, parse) -> dict[int, str]:
        """Nachschlagetabelle ID -> Name."""
        result: dict[int, str] = {}
        for row in self._parse_rows(table_type, parse):
            named: NamedRow = row
            result[named.id] = named.name
        return result

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
        log.warning("%s: %s", self.path.name, message)
