"""Seiten und Zeilenverzeichnis von ``export.pdb``.

Eine Seite besteht aus einem 0x28 Byte langen Kopf, danach einem Heap, in
dem die Zeilen liegen, und ganz am Ende einem Verzeichnis, das **rueckwaerts**
vom Seitenende her waechst::

    0x00  Seitenkopf (0x28 Byte)
    0x28  Heap: Zeilen, aufsteigend, Abstaende in Vielfachen von 4
     ...
          freier Platz
     ...
          Zeilenverzeichnis, Gruppen zu je 16 Zeilen, rueckwaerts
    Ende

Zeilenverzeichnis
-----------------
Gruppe ``g`` liegt bei ``base = page_size - g * 0x24``. Darin::

    base - 2        u2  Schreibmaske  (welche Zeile der letzte Speichervorgang
                        angefasst hat)
    base - 4        u2  Anwesenheitsmaske (Bit i gesetzt = Zeile i gueltig)
    base - 6 - 2*i  u2  Offset der Zeile i, **relativ zum Heap-Anfang** (0x28)

**Geloeschte Zeilen bleiben als Muell im Heap stehen.** Ihr Bit in der
Anwesenheitsmaske ist geloescht, Offset und Slot bleiben aber fuer immer
erhalten. Wer die Maske ignoriert, liest geloeschte Tracks als echte -
deshalb ist ``present`` hier kein Zusatz, sondern Pflicht.

Zaehlung der Slots
------------------
Die Zahl der belegten Verzeichnisplaetze (lebende **und** geloeschte) steht
als 13-Bit-Feld ab 0x18, direkt danach 11 Bit mit der Zahl der lebenden
Zeilen::

    v = u4 aus den drei Byte ab 0x18 (little-endian)
    slot_count    = v & 0x1FFF
    present_count = (v >> 13) & 0x7FF

Quellen
-------
crate-digger (``rekordbox_pdb.ksy``, Typen ``page``, ``row_group``,
``row_ref``) und ``FORMAT.md`` von rekordbox-pdb, das dieselbe Zaehlung an
vier echten Exporten nachgemessen hat (202 von 202 Datenseiten). Beide
stimmen ueberein.

Eine Abweichung zwischen den Quellen ist bekannt und hier vermerkt: die
Kaitai-Datei liest die Schreibmaske bei ``base`` statt bei ``base - 2``,
was sich mit dem Offset der 16. Zeile derselben Gruppe ueberschneiden
wuerde. ``FORMAT.md`` (``base - 2``) ist in sich stimmig und wird deshalb
verwendet. Fuer das Lesen der Zeilen spielt es keine Rolle - die
Schreibmaske wird nur beim Schreiben gebraucht.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

#: Laenge des Seitenkopfs; hier beginnt der Heap.
PAGE_HEADER_SIZE = 0x28
#: Platz, den eine Gruppe von bis zu 16 Zeilenoffsets einnimmt.
ROW_GROUP_SIZE = 0x24
#: Zeilen je Gruppe.
ROWS_PER_GROUP = 16

#: Bit 0x40 in den Seitenflags: gesetzt = Indexseite ohne Zeilen.
PAGE_FLAG_INDEX = 0x40


class PdbPageError(ValueError):
    """Eine Seite ist nicht lesbar - abgeschnitten oder unplausibel."""


@dataclass(frozen=True)
class RowSlot:
    """Ein Platz im Zeilenverzeichnis."""

    #: Laufende Nummer im Verzeichnis der Seite.
    slot: int
    #: Offset der Zeile relativ zum Seitenanfang.
    offset: int
    #: Ob die Zeile gueltig ist. Geloeschte Zeilen sind hier ``False`` und
    #: duerfen nicht gelesen werden.
    present: bool


@dataclass(frozen=True)
class PdbPage:
    """Eine Seite aus ``export.pdb`` samt ihrer Rohbytes."""

    index: int
    table_type: int
    next_page: int
    sequence: int
    #: Belegte Verzeichnisplaetze, geloeschte eingeschlossen.
    slot_count: int
    #: Gueltige Zeilen laut Kopf.
    present_count: int
    page_flags: int
    free_size: int
    used_size: int
    #: Die komplette Seite, unveraendert. Der Writer arbeitet darauf.
    data: bytes

    @property
    def is_data_page(self) -> bool:
        """Ob die Seite Zeilen enthaelt.

        Die erste Seite jeder Tabelle ist in allen bekannten Exporten eine
        Indexseite ohne Zeilen; die echten Daten stehen erst auf der
        naechsten.
        """
        return self.page_flags & PAGE_FLAG_INDEX == 0

    @property
    def group_count(self) -> int:
        if self.slot_count <= 0:
            return 0
        return (self.slot_count - 1) // ROWS_PER_GROUP + 1

    def slots(self) -> tuple[RowSlot, ...]:
        """Alle Verzeichnisplaetze der Seite, in Slot-Reihenfolge.

        Auch die geloeschten - wer nur die gueltigen will, nimmt
        ``present_slots()``. Der Writer braucht beide.
        """
        if not self.is_data_page or self.slot_count <= 0:
            return ()
        page_size = len(self.data)
        found: list[RowSlot] = []
        for slot in range(self.slot_count):
            group, within = divmod(slot, ROWS_PER_GROUP)
            base = page_size - group * ROW_GROUP_SIZE
            flags_at = base - 4
            offset_at = base - 6 - 2 * within
            if offset_at < PAGE_HEADER_SIZE or flags_at < PAGE_HEADER_SIZE:
                raise PdbPageError(
                    f"Seite {self.index}: Zeilenverzeichnis ragt in den "
                    f"Seitenkopf (Slot {slot})"
                )
            (present_flags,) = struct.unpack_from("<H", self.data, flags_at)
            (heap_offset,) = struct.unpack_from("<H", self.data, offset_at)
            offset = PAGE_HEADER_SIZE + heap_offset
            if offset >= page_size:
                raise PdbPageError(
                    f"Seite {self.index}: Zeile {slot} zeigt auf {offset}, "
                    f"ausserhalb der Seite ({page_size} Byte)"
                )
            found.append(
                RowSlot(
                    slot=slot,
                    offset=offset,
                    present=bool(present_flags >> within & 1),
                )
            )
        return tuple(found)

    def present_slots(self) -> tuple[RowSlot, ...]:
        """Nur die gueltigen Zeilen."""
        return tuple(slot for slot in self.slots() if slot.present)


def parse_page(data: bytes, index: int) -> PdbPage:
    """Eine Seite aus ihren Rohbytes lesen.

    ``data`` ist genau eine Seite. Der Aufrufer schneidet sie aus der
    Datei - so bleibt hier alles seitenrelativ und es gibt keine zweite
    Stelle, an der mit Seitengroessen gerechnet wird.

    Raises:
        PdbPageError: Seite zu kurz oder Kopf unplausibel.
    """
    if len(data) < PAGE_HEADER_SIZE:
        raise PdbPageError(
            f"Seite {index} ist mit {len(data)} Byte zu kurz fuer einen "
            f"Seitenkopf ({PAGE_HEADER_SIZE} Byte)"
        )

    (stored_index,) = struct.unpack_from("<I", data, 0x04)
    (table_type,) = struct.unpack_from("<I", data, 0x08)
    (next_page,) = struct.unpack_from("<I", data, 0x0C)
    (sequence,) = struct.unpack_from("<I", data, 0x10)

    # 13 Bit Slots, danach 11 Bit gueltige Zeilen - zusammen drei Byte.
    counts = int.from_bytes(data[0x18:0x1B], "little")
    slot_count = counts & 0x1FFF
    present_count = (counts >> 13) & 0x7FF

    page_flags = data[0x1B]
    (free_size,) = struct.unpack_from("<H", data, 0x1C)
    (used_size,) = struct.unpack_from("<H", data, 0x1E)

    if stored_index != index:
        raise PdbPageError(
            f"Seite {index} nennt sich selbst {stored_index} - Datei "
            f"beschaedigt oder Seitengroesse falsch"
        )

    return PdbPage(
        index=index,
        table_type=table_type,
        next_page=next_page,
        sequence=sequence,
        slot_count=slot_count,
        present_count=present_count,
        page_flags=page_flags,
        free_size=free_size,
        used_size=used_size,
        data=data,
    )
