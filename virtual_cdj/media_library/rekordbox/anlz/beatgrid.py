"""Beatgrid aus den ANLZ-Dateien (``PQTZ``).

Aufbau des Abschnitts (big-endian, Offsets ab Abschnittsanfang)::

    0x00  4  "PQTZ"
    0x04  4  len_header (0x18)
    0x08  4  len_tag
    0x0c  4  unbekannt
    0x10  4  unbekannt (beobachtet: 0x80000)
    0x14  4  num_beats
    0x18  .. Eintraege zu je 8 Byte

Eintrag::

    0x00  2  beat_number   1-4, 1 = Downbeat
    0x02  2  tempo         BPM x 100
    0x04  4  time          Millisekunden bei normaler Geschwindigkeit

Rekordbox speichert das Tempo **je Beat**. Tracks mit wechselndem Tempo
sind damit korrekt abgebildet; eine einzelne BPM-Zahl waere dort falsch.

``PQT2`` (erweitertes Beatgrid in ``.EXT``) wird bewusst **nicht**
gelesen: der Abschnittskopf ist bekannt, die 2 Byte je Haupteintrag sind
in keiner Quelle vollstaendig entschluesselt. Lieber das gesicherte
``PQTZ`` benutzen als geratene Werte anzeigen.

Quelle: crate-digger ``rekordbox_anlz.ksy`` (``beat_grid_tag``,
``beat_grid_beat``), gegengeprueft gegen rekordcrate.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .container import TAG_HEADER_SIZE, AnlzTag, build_tag

TAG_BEAT_GRID = "PQTZ"
#: Erweitertes Beatgrid - erkannt, aber nicht gedeutet (siehe Modulkopf).
TAG_BEAT_GRID_EXTENDED = "PQT2"

#: Kopflaenge des Abschnitts; danach beginnen die Eintraege.
BEAT_GRID_HEADER_SIZE = 0x18
#: Groesse eines Eintrags.
BEAT_SIZE = 8

#: Beobachteter Wert des zweiten unbekannten Felds - nur fuer neu
#: gebaute Abschnitte.
UNKNOWN_AT_0X10 = 0x80000


class BeatGridFormatError(ValueError):
    """Der Beatgrid-Abschnitt ist beschaedigt."""


@dataclass(frozen=True)
class Beat:
    """Ein Beat, so wie er in der Datei steht."""

    #: Position im Takt, 1 = Downbeat.
    beat_number: int
    #: Tempo in Hundertstel-BPM.
    tempo: int
    #: Zeit in Millisekunden.
    time_ms: int

    @property
    def bpm(self) -> float:
        return self.tempo / 100.0

    @property
    def position_s(self) -> float:
        return self.time_ms / 1000.0


def parse_beat_grid(tag: AnlzTag) -> tuple[Beat, ...]:
    """Alle Beats eines ``PQTZ``-Abschnitts lesen.

    Raises:
        BeatGridFormatError: falsche Kennung oder abgeschnittene Daten.
    """
    if tag.fourcc != TAG_BEAT_GRID:
        raise BeatGridFormatError(f"kein Beatgrid-Abschnitt: {tag.fourcc}")
    if len(tag.data) < BEAT_GRID_HEADER_SIZE:
        raise BeatGridFormatError("PQTZ-Abschnitt ist zu kurz")

    (num_beats,) = struct.unpack_from(">I", tag.data, TAG_HEADER_SIZE + 8)
    available = (len(tag.data) - tag.len_header) // BEAT_SIZE
    if num_beats > available:
        raise BeatGridFormatError(
            f"PQTZ meldet {num_beats} Beats, die Daten reichen nur fuer "
            f"{available}"
        )

    beats = []
    at = tag.len_header
    for _ in range(num_beats):
        beat_number, tempo, time_ms = struct.unpack_from(">HHI", tag.data, at)
        beats.append(
            Beat(beat_number=beat_number, tempo=tempo, time_ms=time_ms)
        )
        at += BEAT_SIZE
    return tuple(beats)


def encode_beat_grid(
    beats: tuple[Beat, ...], *, header_fields: bytes = b""
) -> AnlzTag:
    """Einen ``PQTZ``-Abschnitt aus Beats bauen.

    ``header_fields`` sind die urspruenglichen Felder zwischen Tag-Kopf
    und Eintraegen. Werden sie mitgegeben, bleiben die beiden unbekannten
    Werte des Originals erhalten - genau das verlangt die Regel, keine
    unbekannten Daten zu zerstoeren.
    """
    fields = bytearray(
        header_fields or struct.pack(">III", 0, UNKNOWN_AT_0X10, 0)
    )
    struct.pack_into(">I", fields, 8, len(beats))
    payload = bytes(fields) + b"".join(
        struct.pack(">HHI", beat.beat_number, beat.tempo, beat.time_ms)
        for beat in beats
    )
    return build_tag(
        TAG_BEAT_GRID, TAG_HEADER_SIZE + len(fields), payload
    )
