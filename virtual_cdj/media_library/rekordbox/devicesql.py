"""DeviceSQL-Strings aus ``export.pdb`` lesen und schreiben.

Alle Texte der Datenbank (Titel, Interpret, Dateipfad, ...) stecken in
diesem kleinen Rahmenformat. Es gibt drei Formen; welche gilt, sagt das
erste Byte:

===========  =======================================================
erstes Byte  Bedeutung
===========  =======================================================
ungerade     kurzer ASCII-Text. Laenge = ``(byte >> 1) - 1``, Text ab
             +1. ``0x03`` ist der leere Text.
``0x40``     langer ASCII-Text: ``u2`` Gesamtlaenge ab +1 (die vier
             Rahmenbytes eingerechnet), ein unbenutztes Byte, Text ab
             +4 mit ``Gesamtlaenge - 4`` Byte.
``0x90``     wie ``0x40``, aber UTF-16LE.
===========  =======================================================

Quellen und Abgleich
--------------------
Deep Symmetry, *DJ Link Ecosystem Analysis* (``rekordbox_pdb.ksy`` aus
crate-digger, Typ ``device_sql_string``), gegengeprueft gegen rekordcrate
(Rust) und gegen ``FORMAT.md`` von rekordbox-pdb (Python), das die Regeln
zusaetzlich byte-weise an echten Exporten geprueft hat. Alle drei sind
sich einig.

**Kleines Endian merken:** ``export.pdb`` ist durchgehend little-endian,
die Analysedateien (``ANLZ*``) dagegen big-endian. Lange Unicode-Strings
sind hier deshalb UTF-16LE, in den ANLZ-Dateien UTF-16BE.

Bekannte Besonderheit (aus ``FORMAT.md``, an echten Exporten beobachtet):
Der ISRC im Track-Datensatz benutzt zwar die Kennung ``0x90``, enthaelt
aber **kein** UTF-16, sondern ``0x03`` + ASCII + ``0x00``. Wer stur
UTF-16 dekodiert, bekommt Unsinn. ``decode_string`` erkennt das am
fuehrenden ``0x03`` und liefert den ASCII-Text - der Sonderfall ist mit
``DeviceSqlString.is_mangled_ascii`` sichtbar, damit er nicht stillschweigend
untergeht.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

#: Kennungen der langen Formen.
KIND_LONG_ASCII = 0x40
KIND_LONG_UTF16 = 0x90
#: Rahmen der langen Formen: Kennung + u2 Laenge + 1 unbenutztes Byte.
LONG_HEADER_SIZE = 4
#: Laengster Text, den die kurze Form fassen kann: ``(0xFF >> 1) - 1``.
MAX_SHORT_TEXT = 126


class DeviceSqlError(ValueError):
    """Ein String ist nicht lesbar - abgeschnitten oder unbekannte Form."""


@dataclass(frozen=True)
class DeviceSqlString:
    """Ein gelesener String samt seiner Laenge in der Datei."""

    text: str
    #: Wie viele Byte der String in der Datei belegt (inklusive Rahmen).
    size: int
    #: Das erste Byte, unveraendert - fuer Diagnose und Round-Trip.
    kind: int
    #: Ob die ISRC-Besonderheit gegriffen hat (siehe Modulkopf).
    is_mangled_ascii: bool = False
    #: Ob beim Dekodieren Bytes ersetzt werden mussten. Dann ist der Text
    #: angezeigt, aber nicht verlaesslich.
    had_decode_errors: bool = False

    def __bool__(self) -> bool:
        return bool(self.text)


def decode_string(data: bytes, offset: int) -> DeviceSqlString:
    """Einen DeviceSQL-String an ``offset`` lesen.

    Es wird bewusst tolerant dekodiert: ein einzelnes kaputtes Zeichen in
    einem Titel darf nicht die ganze Bibliothek unlesbar machen. Was
    ersetzt werden musste, steht in ``had_decode_errors``. Strukturfehler
    (Laenge zeigt aus der Datei heraus) sind dagegen echte Fehler.

    Raises:
        DeviceSqlError: Offset oder Laenge liegen ausserhalb der Daten.
    """
    if offset < 0 or offset >= len(data):
        raise DeviceSqlError(
            f"String-Offset {offset} liegt ausserhalb der Daten "
            f"({len(data)} Byte)"
        )
    kind = data[offset]

    if kind in (KIND_LONG_ASCII, KIND_LONG_UTF16):
        return _decode_long(data, offset, kind)
    return _decode_short(data, offset, kind)


def _decode_short(data: bytes, offset: int, kind: int) -> DeviceSqlString:
    # Die Laenge steckt im Byte selbst und zaehlt sich mit: 0x03 -> leer.
    size = kind >> 1
    if size < 1:
        raise DeviceSqlError(
            f"unbrauchbare Kurzstring-Kennung 0x{kind:02x} bei {offset}"
        )
    end = offset + size
    if end > len(data):
        raise DeviceSqlError(
            f"Kurzstring bei {offset} reicht ueber das Ende hinaus"
        )
    raw = data[offset + 1: end]
    text, errors = _decode_bytes(raw, "ascii")
    return DeviceSqlString(
        text=text, size=size, kind=kind, had_decode_errors=errors
    )


def _decode_long(data: bytes, offset: int, kind: int) -> DeviceSqlString:
    if offset + LONG_HEADER_SIZE > len(data):
        raise DeviceSqlError(
            f"Langstring bei {offset} hat keinen vollstaendigen Rahmen"
        )
    (size,) = struct.unpack_from("<H", data, offset + 1)
    if size < LONG_HEADER_SIZE:
        raise DeviceSqlError(
            f"Langstring bei {offset} meldet unmoegliche Laenge {size}"
        )
    end = offset + size
    if end > len(data):
        raise DeviceSqlError(
            f"Langstring bei {offset} reicht ueber das Ende hinaus "
            f"({end} > {len(data)})"
        )
    raw = data[offset + LONG_HEADER_SIZE: end]

    # ISRC-Sonderfall: Kennung 0x90, Inhalt aber ASCII hinter einem 0x03.
    if kind == KIND_LONG_UTF16 and raw[:1] == b"\x03":
        text, errors = _decode_bytes(raw[1:].rstrip(b"\x00"), "ascii")
        return DeviceSqlString(
            text=text, size=size, kind=kind,
            is_mangled_ascii=True, had_decode_errors=errors,
        )

    encoding = "utf-16-le" if kind == KIND_LONG_UTF16 else "ascii"
    text, errors = _decode_bytes(raw, encoding)
    return DeviceSqlString(
        text=text.rstrip("\x00"), size=size, kind=kind,
        had_decode_errors=errors,
    )


def _decode_bytes(raw: bytes, encoding: str) -> tuple[str, bool]:
    """Dekodieren und melden, ob ersetzt werden musste."""
    try:
        return raw.decode(encoding), False
    except UnicodeDecodeError:
        return raw.decode(encoding, errors="replace"), True


def encode_string(text: str) -> bytes:
    """Einen Text in das DeviceSQL-Rahmenformat bringen.

    Die Wahl der Form folgt der Beobachtung aus ``FORMAT.md``, wie
    rekordbox selbst kodiert: reines ASCII bis 126 Zeichen kurz, laenger
    als ``0x40``, alles mit Sonderzeichen als ``0x90`` - unabhaengig von
    der Laenge, eine kurze Unicode-Form gibt es nicht.

    Wird beim Schreiben von Datenbankfeldern gebraucht und von den Tests
    und dem Beispielgenerator, um gueltige Dateien zu bauen.
    """
    try:
        raw = text.encode("ascii")
    except UnicodeEncodeError:
        payload = text.encode("utf-16-le")
        size = LONG_HEADER_SIZE + len(payload)
        return (
            bytes([KIND_LONG_UTF16])
            + struct.pack("<H", size)
            + b"\x00"
            + payload
        )

    if len(raw) <= MAX_SHORT_TEXT:
        return bytes([((len(raw) + 1) << 1) | 1]) + raw
    size = LONG_HEADER_SIZE + len(raw)
    return (
        bytes([KIND_LONG_ASCII]) + struct.pack("<H", size) + b"\x00" + raw
    )


def encoded_size(text: str) -> int:
    """Wie viele Byte ``encode_string(text)`` belegen wird."""
    return len(encode_string(text))
