"""Uebrige Abschnitte der ANLZ-Dateien: Pfad, VBR-Tabelle, Songstruktur.

``PPTH`` - Pfad der Audiodatei
    ``u4 len_path``, danach der Pfad als **UTF-16BE** mit abschliessendem
    Nullzeichen (``len_path`` zaehlt es mit). Damit laesst sich pruefen,
    ob eine Analysedatei wirklich zum Track gehoert - eine der
    Voraussetzungen, bevor geschrieben werden darf.

``PVBR`` - Sprungtabelle fuer Dateien mit variabler Bitrate
    400 Eintraege zu je 4 Byte. Wird gelesen, aber nicht gedeutet: der
    Decoder dieses Projekts sucht selbst in der Datei, eine zweite
    Sprungtabelle braucht er nicht.

``PSSI`` - Songstruktur (Phrasen)
    Der Inhalt ist **maskiert**: alle Bytes hinter ``len_entries`` sind
    mit einem 19 Byte langen Muster XOR-verknuepft, wobei jedes
    Musterbyte zusaetzlich um die Zahl der Phrasen erhoeht wird. Ob eine
    Datei ueberhaupt maskiert ist, verraet das Feld ``mood``: es darf nur
    1 bis 3 sein, ist maskiert aber viel groesser.

    Hier wird nur **entmaskiert**, nicht gedeutet. Welche Zahl in ``kind``
    welche Phrase meint, haengt vom ``mood`` ab und ist in den Quellen
    ausdruecklich als nicht endgueltig bestaetigt vermerkt. Die Rohbytes
    stehen zur Verfuegung, eine Bedeutung wird nicht behauptet.

Quelle: crate-digger ``rekordbox_anlz.ksy`` (``path_tag``, ``vbr_tag``,
``song_structure_tag``), gegengeprueft gegen rekordcrate.
"""

from __future__ import annotations

import struct

from .container import TAG_HEADER_SIZE, AnlzTag

TAG_PATH = "PPTH"
TAG_VBR = "PVBR"
TAG_SONG_STRUCTURE = "PSSI"

#: Grundmuster der PSSI-Maskierung. Jedes Byte wird zusaetzlich um die
#: Zahl der Phrasen erhoeht.
SONG_STRUCTURE_MASK = bytes(
    (
        0xCB, 0xE1, 0xEE, 0xFA, 0xE5, 0xEE, 0xAD, 0xEE, 0xE9, 0xD2,
        0xE9, 0xEB, 0xE1, 0xE9, 0xF3, 0xE8, 0xE9, 0xF4, 0xE1,
    )
)
#: Ab hier ist der PSSI-Inhalt maskiert: hinter ``len_entries``.
SONG_STRUCTURE_MASKED_FROM = TAG_HEADER_SIZE + 6
#: ``mood`` darf nur 1-3 sein; groesser heisst "noch maskiert".
MAX_PLAUSIBLE_MOOD = 20


class AnlzMetadataError(ValueError):
    """Ein Abschnitt ist beschaedigt."""


def parse_path(tag: AnlzTag) -> str:
    """Den Audiopfad aus einem ``PPTH``-Abschnitt lesen.

    Ein leerer Pfad ist kein Fehler - es gibt Dateien ohne Eintrag.

    Raises:
        AnlzMetadataError: falsche Kennung oder abgeschnittene Daten.
    """
    if tag.fourcc != TAG_PATH:
        raise AnlzMetadataError(f"kein Pfad-Abschnitt: {tag.fourcc}")
    if len(tag.data) < TAG_HEADER_SIZE + 4:
        raise AnlzMetadataError("PPTH-Abschnitt ist zu kurz")
    (len_path,) = struct.unpack_from(">I", tag.data, TAG_HEADER_SIZE)
    if len_path <= 1:
        return ""
    payload = tag.content
    # Das abschliessende Nullzeichen zaehlt in len_path mit.
    text = payload[:max(0, len_path - 2)]
    if len(text) > len(payload):
        raise AnlzMetadataError(
            f"PPTH meldet {len_path} Byte, vorhanden sind {len(payload)}"
        )
    return text.decode("utf-16-be", errors="replace").rstrip("\x00")


def parse_vbr_index(tag: AnlzTag) -> tuple[int, ...]:
    """Die VBR-Sprungtabelle als Zahlen - ungedeutet."""
    if tag.fourcc != TAG_VBR:
        raise AnlzMetadataError(f"kein VBR-Abschnitt: {tag.fourcc}")
    payload = tag.content
    count = len(payload) // 4
    return struct.unpack_from(f">{count}I", payload, 0) if count else ()


def is_song_structure_masked(tag: AnlzTag) -> bool:
    """Ob der Inhalt eines ``PSSI``-Abschnitts noch maskiert ist."""
    if len(tag.data) < SONG_STRUCTURE_MASKED_FROM + 2:
        return False
    (mood,) = struct.unpack_from(">H", tag.data, SONG_STRUCTURE_MASKED_FROM)
    return mood > MAX_PLAUSIBLE_MOOD


def song_structure_mask(phrase_count: int) -> bytes:
    """Das Maskenmuster fuer eine bestimmte Zahl Phrasen."""
    return bytes(
        (value + phrase_count) & 0xFF for value in SONG_STRUCTURE_MASK
    )


def unmask_song_structure(tag: AnlzTag) -> bytes:
    """Den Inhalt eines ``PSSI``-Abschnitts entmaskieren.

    Liefert die Bytes ab ``len_entries``; ist die Datei nicht maskiert,
    kommen sie unveraendert zurueck. Gedeutet wird nichts - siehe
    Modulkopf.
    """
    if tag.fourcc != TAG_SONG_STRUCTURE:
        raise AnlzMetadataError(
            f"kein Songstruktur-Abschnitt: {tag.fourcc}"
        )
    if len(tag.data) < SONG_STRUCTURE_MASKED_FROM:
        raise AnlzMetadataError("PSSI-Abschnitt ist zu kurz")
    (phrase_count,) = struct.unpack_from(
        ">H", tag.data, TAG_HEADER_SIZE + 4
    )
    body = tag.data[SONG_STRUCTURE_MASKED_FROM:]
    if not is_song_structure_masked(tag):
        return body
    mask = song_structure_mask(phrase_count)
    return bytes(
        value ^ mask[index % len(mask)] for index, value in enumerate(body)
    )
