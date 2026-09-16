"""Cue-Punkte, Hot Cues und Loops aus den ANLZ-Dateien.

Es gibt zwei Formen derselben Sache:

``PCOB`` (in ``.DAT``)
    Die alte Liste. Eintraege heissen ``PCPT`` und sind 0x38 = 56 Byte
    lang. Kennt Hot Cues A-C, keine Farben, keine Namen.

``PCO2`` (in ``.EXT``)
    Die mit den Nexus-2-Geraeten eingefuehrte Liste. Eintraege heissen
    ``PCP2``, sind unterschiedlich lang und tragen zusaetzlich Farbe,
    Kommentartext und die Loop-Laenge in Beats. Erst hier gibt es die Hot
    Cues D-H.

Jede der beiden Formen kommt **zweimal** vor: einmal mit ``type = 0`` fuer
die Memory Cues und Loops, einmal mit ``type = 1`` fuer die Hot Cues.

Byte-Layout ``PCPT`` (big-endian, Offsets ab Eintragsanfang)::

    0x00  4  "PCPT"
    0x04  4  len_header (0x1c)
    0x08  4  len_entry  (0x38)
    0x0c  4  hot_cue     0 = Memory Cue, 1-8 = A-H
    0x10  4  status      4 = aktiver Loop, 0 = sonst
    0x14  4  unbekannt   (beobachtet: 0x00100000)
    0x18  2  order_first
    0x1a  2  order_last
    0x1c  1  type        1 = Position, 2 = Loop
    0x1d  3  unbekannt   (beobachtet: 0x00 0x03 0xe8, also 0 und 1000)
    0x20  4  time        Position in Millisekunden
    0x24  4  loop_time   Loop-Ende in ms, 0xffffffff wenn kein Loop
    0x28 16  unbekannt

Byte-Layout ``PCP2``::

    0x00  4  "PCP2"
    0x04  4  len_header (0x2c)
    0x08  4  len_entry
    0x0c  4  hot_cue
    0x10  1  type
    0x11  3  unbekannt   (beobachtet: 0 und 1000)
    0x14  4  time
    0x18  4  loop_time
    0x1c  1  color_id    Farbe eines Memory Cues (Tabelle 0-8)
    0x1d  7  unbekannt
    0x24  2  loop_numerator     Loop-Laenge in Beats, Zaehler
    0x26  2  loop_denominator   Nenner; 0/0 = nicht quantisiert
    0x28  4  len_comment        Bytes des Kommentars, mit Schluss-NUL
    0x2c  .. comment            UTF-16BE
    ..    1  color_code   Rekordbox-Farbnummer des Hot Cues
    ..    1  color_red
    ..    1  color_green
    ..    1  color_blue
    ..    .. Rest, falls vorhanden - unbekannt, bleibt erhalten

Quellen: crate-digger ``rekordbox_anlz.ksy`` (Typen ``cue_tag``,
``cue_entry``, ``cue_extended_tag``, ``cue_extended_entry``),
gegengeprueft gegen rekordcrate, aus dem auch die beobachteten Werte der
unbekannten Felder stammen.

Erhalt unbekannter Bytes
------------------------
``encode_entry()`` baut einen geaenderten Eintrag **auf seinen alten
Bytes auf** und tauscht nur die Felder, die sich wirklich geaendert
haben. Die unbekannten Bereiche (0x14, 0x28-0x37 bei ``PCPT``;
0x11, 0x1d-0x23 und der Rest bei ``PCP2``) wandern unveraendert mit.
Nur ein voellig neuer Cue-Punkt bekommt die oben beobachteten
Vorgabewerte.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace
from enum import IntEnum

from .container import TAG_HEADER_SIZE, AnlzTag, build_tag

#: Kennungen der beiden Listenformen.
TAG_CUES = "PCOB"
TAG_CUES_EXTENDED = "PCO2"

#: Kennungen der Eintraege.
ENTRY_MAGIC = b"PCPT"
ENTRY_MAGIC_EXTENDED = b"PCP2"

#: Feste Groessen der alten Form.
PCPT_HEADER_SIZE = 0x1C
PCPT_ENTRY_SIZE = 0x38
#: Kopfgroesse der erweiterten Eintraege; danach folgt der Kommentar.
PCP2_HEADER_SIZE = 0x2C
#: Vier Farbbytes hinter dem Kommentar.
PCP2_COLOR_SIZE = 4

#: Kopflaengen der beiden Listen-Abschnitte.
PCOB_TAG_HEADER_SIZE = 0x18
PCO2_TAG_HEADER_SIZE = 0x14

#: ``loop_time``, wenn der Eintrag kein Loop ist.
NO_LOOP_TIME = 0xFFFFFFFF

#: Werte der unbekannten Felder, wie sie in echten Dateien beobachtet
#: wurden (rekordcrate). Nur fuer **neue** Eintraege; vorhandene Eintraege
#: behalten ihre eigenen Bytes.
PCPT_UNKNOWN_AT_0X14 = 0x00100000
UNKNOWN_AFTER_TYPE = b"\x00\x03\xe8"

#: Hoechster Hot-Cue-Slot, den die alte ``PCOB``-Liste tragen kann.
#: crate-digger vermerkt, dass die Hot Cues D-H erst mit ``PCO2``
#: eingefuehrt wurden. Ob ein Geraet einen hoeheren Wert in ``PCOB``
#: ueberhaupt liest, ist **nicht geprueft** - deshalb schreibt der Writer
#: hoehere Slots nur in die erweiterte Liste.
MAX_LEGACY_HOT_CUE = 3

#: Farbtabelle der Memory Cues (Feld ``color_id``). Aus rekordcrate; die
#: Hot Cues tragen ihre Farbe dagegen als eigene RGB-Bytes.
MEMORY_CUE_COLORS: dict[int, str] = {
    0: "",
    1: "Pink",
    2: "Red",
    3: "Orange",
    4: "Yellow",
    5: "Green",
    6: "Aqua",
    7: "Blue",
    8: "Purple",
}


class CueListKind(IntEnum):
    """Welche Art Liste ein Abschnitt traegt."""

    MEMORY = 0
    HOT_CUE = 1


class CueEntryType(IntEnum):
    """Position oder Loop."""

    CUE = 1
    LOOP = 2


class CueStatus(IntEnum):
    """Nur in der alten Form: ob der Loop aktiv ist."""

    DISABLED = 0
    ENABLED = 1
    ACTIVE_LOOP = 4


class CueFormatError(ValueError):
    """Eine Cue-Liste ist beschaedigt."""


@dataclass(frozen=True)
class CueEntry:
    """Ein Cue-Punkt, so wie er in der Datei steht.

    Zeiten sind Millisekunden - die Umrechnung in Sekunden passiert erst
    im internen Modell, damit hier nichts durch Rundung verloren geht.
    """

    #: 0 = Memory Cue, 1-8 = Hot Cue A-H.
    hot_cue: int = 0
    type: int = int(CueEntryType.CUE)
    time_ms: int = 0
    #: ``None``, wenn der Eintrag kein Loop ist.
    loop_time_ms: int | None = None

    #: Nur alte Form.
    status: int = 0
    order_first: int = 0
    order_last: int = 0

    #: Nur erweiterte Form.
    color_id: int = 0
    comment: str = ""
    loop_numerator: int = 0
    loop_denominator: int = 0
    #: Farbnummer und RGB des Hot Cues. ``None``, wenn die Datei an
    #: dieser Stelle keine Farbe fuehrt - dann wird auch keine erfunden.
    color_code: int | None = None
    color_rgb: tuple[int, int, int] | None = None

    #: Die urspruenglichen Bytes des Eintrags. Grundlage dafuer, beim
    #: Schreiben nur die geaenderten Felder anzufassen.
    raw: bytes = b""

    @property
    def is_hot_cue(self) -> bool:
        return self.hot_cue > 0

    @property
    def is_loop(self) -> bool:
        return self.type == int(CueEntryType.LOOP)

    @property
    def color_hex(self) -> str:
        """``"#rrggbb"`` oder leer, wenn die Datei keine Farbe nennt."""
        if not self.color_rgb:
            return ""
        red, green, blue = self.color_rgb
        return f"#{red:02x}{green:02x}{blue:02x}"


@dataclass(frozen=True)
class CueList:
    """Eine Cue-Liste, also ein ``PCOB``- oder ``PCO2``-Abschnitt."""

    kind: CueListKind
    #: ``True`` fuer ``PCO2``.
    extended: bool
    entries: tuple[CueEntry, ...] = ()
    #: Die typabhaengigen Felder des Abschnittskopfs, unveraendert. Darin
    #: steckt unter anderem ``memory_count``, dessen Bedeutung in keiner
    #: Quelle geklaert ist - es wird deshalb nur durchgereicht.
    header_fields: bytes = b""

    @property
    def fourcc(self) -> str:
        return TAG_CUES_EXTENDED if self.extended else TAG_CUES

    def entry_for_slot(self, hot_cue: int) -> CueEntry | None:
        for entry in self.entries:
            if entry.hot_cue == hot_cue:
                return entry
        return None


# --------------------------------------------------------------------------
# Lesen
# --------------------------------------------------------------------------


def parse_cue_list(tag: AnlzTag) -> CueList:
    """Einen ``PCOB``- oder ``PCO2``-Abschnitt lesen.

    Raises:
        CueFormatError: Kennung passt nicht oder der Abschnitt ist zu kurz.
    """
    if tag.fourcc == TAG_CUES:
        return _parse_legacy(tag)
    if tag.fourcc == TAG_CUES_EXTENDED:
        return _parse_extended(tag)
    raise CueFormatError(f"kein Cue-Abschnitt: {tag.fourcc}")


def _parse_legacy(tag: AnlzTag) -> CueList:
    data = tag.data
    if len(data) < PCOB_TAG_HEADER_SIZE:
        raise CueFormatError("PCOB-Abschnitt ist zu kurz")
    (list_type,) = struct.unpack_from(">I", data, TAG_HEADER_SIZE)
    (num_cues,) = struct.unpack_from(">H", data, TAG_HEADER_SIZE + 6)

    entries: list[CueEntry] = []
    at = tag.len_header
    for _ in range(num_cues):
        if at + PCPT_ENTRY_SIZE > len(data):
            raise CueFormatError(
                f"PCOB meldet {num_cues} Eintraege, die Daten reichen "
                f"aber nur bis Byte {len(data)}"
            )
        entries.append(_parse_legacy_entry(data[at:at + PCPT_ENTRY_SIZE]))
        at += PCPT_ENTRY_SIZE

    return CueList(
        kind=_kind(list_type),
        extended=False,
        entries=tuple(entries),
        header_fields=data[TAG_HEADER_SIZE:tag.len_header],
    )


def _parse_legacy_entry(raw: bytes) -> CueEntry:
    if raw[:4] != ENTRY_MAGIC:
        raise CueFormatError("Cue-Eintrag beginnt nicht mit PCPT")
    hot_cue, status = struct.unpack_from(">II", raw, 0x0C)
    order_first, order_last = struct.unpack_from(">HH", raw, 0x18)
    entry_type = raw[0x1C]
    time_ms, loop_time = struct.unpack_from(">II", raw, 0x20)
    return CueEntry(
        hot_cue=hot_cue,
        type=entry_type,
        time_ms=time_ms,
        loop_time_ms=None if loop_time == NO_LOOP_TIME else loop_time,
        status=status,
        order_first=order_first,
        order_last=order_last,
        raw=bytes(raw),
    )


def _parse_extended(tag: AnlzTag) -> CueList:
    data = tag.data
    if len(data) < PCO2_TAG_HEADER_SIZE:
        raise CueFormatError("PCO2-Abschnitt ist zu kurz")
    (list_type,) = struct.unpack_from(">I", data, TAG_HEADER_SIZE)
    (num_cues,) = struct.unpack_from(">H", data, TAG_HEADER_SIZE + 4)

    entries: list[CueEntry] = []
    at = tag.len_header
    for _ in range(num_cues):
        if at + TAG_HEADER_SIZE > len(data):
            raise CueFormatError(
                f"PCO2 meldet {num_cues} Eintraege, die Daten sind aber "
                f"nach Byte {at} zu Ende"
            )
        (len_entry,) = struct.unpack_from(">I", data, at + 0x08)
        if len_entry < PCP2_HEADER_SIZE or at + len_entry > len(data):
            raise CueFormatError(
                f"PCO2-Eintrag bei Byte {at} meldet unplausible Laenge "
                f"{len_entry}"
            )
        entries.append(_parse_extended_entry(data[at:at + len_entry]))
        at += len_entry

    return CueList(
        kind=_kind(list_type),
        extended=True,
        entries=tuple(entries),
        header_fields=data[TAG_HEADER_SIZE:tag.len_header],
    )


def _parse_extended_entry(raw: bytes) -> CueEntry:
    if raw[:4] != ENTRY_MAGIC_EXTENDED:
        raise CueFormatError("Cue-Eintrag beginnt nicht mit PCP2")
    (hot_cue,) = struct.unpack_from(">I", raw, 0x0C)
    entry_type = raw[0x10]
    time_ms, loop_time = struct.unpack_from(">II", raw, 0x14)
    color_id = raw[0x1C]
    numerator, denominator = struct.unpack_from(">HH", raw, 0x24)

    comment = ""
    len_comment = 0
    if len(raw) >= PCP2_HEADER_SIZE:
        (len_comment,) = struct.unpack_from(">I", raw, 0x28)
        end = PCP2_HEADER_SIZE + len_comment
        if len_comment and end <= len(raw):
            comment = (
                raw[PCP2_HEADER_SIZE:end]
                .decode("utf-16-be", errors="replace")
                .rstrip("\x00")
            )
        elif len_comment:
            raise CueFormatError(
                f"PCP2-Kommentar meldet {len_comment} Byte, der Eintrag "
                f"hat nur {len(raw)}"
            )

    color_at = PCP2_HEADER_SIZE + len_comment
    color_code: int | None = None
    color_rgb: tuple[int, int, int] | None = None
    if color_at + PCP2_COLOR_SIZE <= len(raw):
        color_code = raw[color_at]
        color_rgb = (
            raw[color_at + 1], raw[color_at + 2], raw[color_at + 3]
        )

    return CueEntry(
        hot_cue=hot_cue,
        type=entry_type,
        time_ms=time_ms,
        loop_time_ms=None if loop_time == NO_LOOP_TIME else loop_time,
        color_id=color_id,
        comment=comment,
        loop_numerator=numerator,
        loop_denominator=denominator,
        color_code=color_code,
        color_rgb=color_rgb,
        raw=bytes(raw),
    )


def _kind(list_type: int) -> CueListKind:
    try:
        return CueListKind(list_type)
    except ValueError as error:
        raise CueFormatError(
            f"unbekannte Cue-Listenart {list_type}"
        ) from error


# --------------------------------------------------------------------------
# Schreiben
# --------------------------------------------------------------------------


def encode_cue_list(cue_list: CueList) -> AnlzTag:
    """Eine Cue-Liste wieder zu einem Abschnitt zusammensetzen.

    Der Abschnittskopf wird aus den urspruenglichen Feldern uebernommen;
    nur die Zahl der Eintraege wird berichtigt. ``memory_count`` und die
    unbenannten Bytes bleiben damit so, wie rekordbox sie geschrieben hat.
    """
    entries = b"".join(
        encode_entry(entry, extended=cue_list.extended)
        for entry in cue_list.entries
    )
    count = len(cue_list.entries)

    if cue_list.extended:
        fields = bytearray(
            cue_list.header_fields
            or struct.pack(">IHH", int(cue_list.kind), 0, 0)
        )
        struct.pack_into(">I", fields, 0, int(cue_list.kind))
        struct.pack_into(">H", fields, 4, count)
        header_size = TAG_HEADER_SIZE + len(fields)
        return build_tag(
            TAG_CUES_EXTENDED, header_size, bytes(fields) + entries
        )

    fields = bytearray(
        cue_list.header_fields
        or struct.pack(">IHHI", int(cue_list.kind), 0, 0, 0)
    )
    struct.pack_into(">I", fields, 0, int(cue_list.kind))
    struct.pack_into(">H", fields, 6, count)
    header_size = TAG_HEADER_SIZE + len(fields)
    return build_tag(TAG_CUES, header_size, bytes(fields) + entries)


def encode_entry(entry: CueEntry, *, extended: bool) -> bytes:
    """Einen Eintrag zu Bytes machen.

    Hat der Eintrag noch seine urspruenglichen Bytes, werden die als
    Grundlage genommen und nur die bekannten Felder hineingeschrieben -
    so bleibt jedes unbekannte Byte erhalten.
    """
    if extended:
        return _encode_extended_entry(entry)
    return _encode_legacy_entry(entry)


def _encode_legacy_entry(entry: CueEntry) -> bytes:
    if len(entry.raw) == PCPT_ENTRY_SIZE:
        raw = bytearray(entry.raw)
    else:
        raw = bytearray(PCPT_ENTRY_SIZE)
        raw[0:4] = ENTRY_MAGIC
        struct.pack_into(
            ">II", raw, 0x04, PCPT_HEADER_SIZE, PCPT_ENTRY_SIZE
        )
        struct.pack_into(">I", raw, 0x14, PCPT_UNKNOWN_AT_0X14)
        raw[0x1D:0x20] = UNKNOWN_AFTER_TYPE

    struct.pack_into(">II", raw, 0x0C, entry.hot_cue, entry.status)
    struct.pack_into(
        ">HH", raw, 0x18, entry.order_first, entry.order_last
    )
    raw[0x1C] = entry.type
    struct.pack_into(
        ">II", raw, 0x20, entry.time_ms,
        NO_LOOP_TIME if entry.loop_time_ms is None else entry.loop_time_ms,
    )
    return bytes(raw)


def _encode_extended_entry(entry: CueEntry) -> bytes:
    comment = entry.comment
    payload = comment.encode("utf-16-be") + b"\x00\x00" if comment else b""
    len_comment = len(payload)

    if len(entry.raw) >= PCP2_HEADER_SIZE:
        head = bytearray(entry.raw[:PCP2_HEADER_SIZE])
        (old_len,) = struct.unpack_from(">I", entry.raw, 0x28)
        tail_at = PCP2_HEADER_SIZE + old_len + PCP2_COLOR_SIZE
        extra = entry.raw[tail_at:] if tail_at < len(entry.raw) else b""
    else:
        head = bytearray(PCP2_HEADER_SIZE)
        head[0:4] = ENTRY_MAGIC_EXTENDED
        head[0x11:0x14] = UNKNOWN_AFTER_TYPE
        extra = b""

    struct.pack_into(">I", head, 0x0C, entry.hot_cue)
    head[0x10] = entry.type
    struct.pack_into(
        ">II", head, 0x14, entry.time_ms,
        NO_LOOP_TIME if entry.loop_time_ms is None else entry.loop_time_ms,
    )
    head[0x1C] = entry.color_id
    struct.pack_into(
        ">HH", head, 0x24, entry.loop_numerator, entry.loop_denominator
    )
    struct.pack_into(">I", head, 0x28, len_comment)

    if entry.color_code is None and entry.color_rgb is None:
        colors = b""
    else:
        red, green, blue = entry.color_rgb or (0, 0, 0)
        colors = bytes([entry.color_code or 0, red, green, blue])

    body = bytearray(bytes(head) + payload + colors + extra)
    struct.pack_into(">I", body, 0x04, PCP2_HEADER_SIZE)
    struct.pack_into(">I", body, 0x08, len(body))
    return bytes(body)


# --------------------------------------------------------------------------
# Reihenfolge der Eintraege
# --------------------------------------------------------------------------


def order_fields(index: int, count: int) -> tuple[int, int]:
    """``order_first``/``order_last`` fuer den ``index``-ten Eintrag.

    Das Muster stammt aus rekordcrate, das beide Felder an echten Dateien
    tabelliert hat: ``order_first`` ist ``0xffff`` beim ersten Eintrag,
    ``0`` beim zweiten und danach der laufende Index; ``order_last``
    zaehlt ab 1 und ist ``0xffff`` beim letzten.

    Warum es beide Felder gibt, ist in keiner Quelle geklaert - deshalb
    wird hier nur nachgebildet, was beobachtet wurde.
    """
    if index == 0:
        first = 0xFFFF
    elif index == 1:
        first = 0
    else:
        first = index
    last = 0xFFFF if index == count - 1 else index + 1
    return first, last


def renumber(entries: tuple[CueEntry, ...]) -> tuple[CueEntry, ...]:
    """Die Ordnungsfelder einer Liste neu vergeben."""
    count = len(entries)
    renumbered = []
    for index, entry in enumerate(entries):
        first, last = order_fields(index, count)
        renumbered.append(
            replace(entry, order_first=first, order_last=last)
        )
    return tuple(renumbered)
