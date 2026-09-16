"""Waveform-Abschnitte der ANLZ-Dateien.

Rekordbox legt mehrere Waveforms nebeneinander ab - grobe fuer den Balken
ueber dem ganzen Track, feine zum Mitlaufen, jeweils einfarbig und
farbig:

======  ======  ======================================================
Tag     Datei   Inhalt
======  ======  ======================================================
PWAV    .DAT    Uebersicht, 1 Byte je Spalte, einfarbig
PWV2    .DAT    sehr kleine Uebersicht aelterer Geraete, 1 Byte
PWV3    .EXT    mitlaufende Waveform, 1 Byte je Spalte, einfarbig
PWV4    .EXT    farbige Uebersicht, 6 Byte je Spalte
PWV5    .EXT    farbige mitlaufende Waveform, 2 Byte je Spalte
======  ======  ======================================================

Spaltenformate
--------------
**1 Byte (PWAV, PWV2, PWV3):** Bits 0-4 Hoehe (0-31), Bits 5-7 Weissgrad.
Kein RGB - eine Helligkeitsstufe.

**2 Byte, big-endian (PWV5):** Bits 15-13 Rot, 12-10 Gruen, 9-7 Blau,
6-2 Hoehe (0-31), Bits 1-0 unbenutzt.

**6 Byte (PWV4):** zwei Byte, die laut Quelle "irgendwie den Weissgrad"
tragen und **nicht entschluesselt** sind, danach vier Energiewerte
(unteres Halbband, unteres, mittleres und oberes Drittel). Daraus wird
hier Rot/Gruen/Blau aus den drei Dritteln gebildet und die Hoehe aus dem
groessten der drei - das ist eine **Darstellungsentscheidung** dieses
Projekts, klar getrennt von den gelesenen Rohwerten, die in
``raw_columns`` erhalten bleiben.

Zeitbezug
---------
Die mitlaufenden Waveforms haben **150 Spalten je Sekunde** (eine halbe
Frame-Laenge bei 75 Frames/s). Die Uebersichten decken dagegen den ganzen
Track ab; ihre Spalten je Sekunde ergeben sich erst aus der Tracklaenge.
Ist die unbekannt, bleibt der Wert ``0.0`` statt einer geratenen Zahl.

Die 3-Band-Waveforms der CDJ-3000-Generation (``PWV6``, ``PWV7`` in
``.2EX``) werden **erkannt, aber nicht gedeutet**: der Abschnittskopf ist
bekannt, die Bedeutung der drei Byte je Spalte in keiner Quelle
gesichert.

Quellen: Deep Symmetry, *DJ Link Ecosystem Analysis*, Abschnitt "Analysis
Files" (Bit-Aufteilung woertlich uebernommen), crate-digger
``rekordbox_anlz.ksy``, gegengeprueft gegen rekordcrate.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .container import TAG_HEADER_SIZE, AnlzTag

TAG_WAVE_PREVIEW = "PWAV"
TAG_WAVE_TINY = "PWV2"
TAG_WAVE_SCROLL = "PWV3"
TAG_WAVE_COLOR_PREVIEW = "PWV4"
TAG_WAVE_COLOR_SCROLL = "PWV5"
#: Erkannt, nicht gedeutet (siehe Modulkopf).
TAG_WAVE_3BAND_PREVIEW = "PWV6"
TAG_WAVE_3BAND_SCROLL = "PWV7"

#: Alle Kennungen, die dieses Modul lesen kann.
KNOWN_TAGS = (
    TAG_WAVE_PREVIEW,
    TAG_WAVE_TINY,
    TAG_WAVE_SCROLL,
    TAG_WAVE_COLOR_PREVIEW,
    TAG_WAVE_COLOR_SCROLL,
)

#: Spalten je Sekunde der mitlaufenden Waveforms. 75 Frames je Sekunde,
#: eine Spalte je halbem Frame.
SCROLL_COLUMNS_PER_SECOND = 150.0

#: Groesster Hoehenwert einer Spalte (5 Bit).
MAX_HEIGHT = 31.0
#: Groesster Farbwert einer Spalte (3 Bit).
MAX_COLOR = 7.0
#: Groesster Energiewert einer Spalte der farbigen Uebersicht (1 Byte).
MAX_ENERGY = 255.0


class WaveformFormatError(ValueError):
    """Ein Waveform-Abschnitt ist beschaedigt."""


@dataclass(frozen=True)
class WaveformColumns:
    """Die Spalten eines Waveform-Abschnitts.

    ``height`` und die Farbanteile sind auf 0.0-1.0 normiert.
    ``raw_columns`` enthaelt die ungedeuteten Bytes je Spalte, damit
    spaeter jemand nachrechnen kann, ohne die Datei erneut zu lesen.
    """

    fourcc: str
    height: tuple[float, ...] = ()
    red: tuple[float, ...] = ()
    green: tuple[float, ...] = ()
    blue: tuple[float, ...] = ()
    raw_columns: tuple[bytes, ...] = ()

    @property
    def is_color(self) -> bool:
        return self.fourcc in (
            TAG_WAVE_COLOR_PREVIEW, TAG_WAVE_COLOR_SCROLL
        )

    @property
    def is_scrolling(self) -> bool:
        """Ob es eine mitlaufende Waveform mit festem Zeitbezug ist."""
        return self.fourcc in (TAG_WAVE_SCROLL, TAG_WAVE_COLOR_SCROLL)

    def __len__(self) -> int:
        return len(self.height)

    def columns_per_second(self, duration_s: float) -> float:
        """Spalten je Sekunde.

        Bei den mitlaufenden Waveforms steht die Zahl fest. Bei den
        Uebersichten braucht es die Tracklaenge; ohne sie ``0.0``.
        """
        if self.is_scrolling:
            return SCROLL_COLUMNS_PER_SECOND
        if duration_s > 0 and self.height:
            return len(self.height) / duration_s
        return 0.0


def parse_waveform(tag: AnlzTag) -> WaveformColumns:
    """Einen Waveform-Abschnitt lesen.

    Raises:
        WaveformFormatError: unbekannte Kennung oder abgeschnittene Daten.
    """
    if tag.fourcc in (TAG_WAVE_PREVIEW, TAG_WAVE_TINY):
        return _parse_single_byte(tag, _preview_payload(tag))
    if tag.fourcc == TAG_WAVE_SCROLL:
        return _parse_single_byte(tag, _sized_payload(tag, expect=1))
    if tag.fourcc == TAG_WAVE_COLOR_SCROLL:
        return _parse_color_scroll(tag, _sized_payload(tag, expect=2))
    if tag.fourcc == TAG_WAVE_COLOR_PREVIEW:
        return _parse_color_preview(tag, _sized_payload(tag, expect=6))
    raise WaveformFormatError(
        f"kein lesbarer Waveform-Abschnitt: {tag.fourcc}"
    )


def _preview_payload(tag: AnlzTag) -> bytes:
    """``PWAV``/``PWV2``: ``len_data`` und ein unbekanntes Feld."""
    if len(tag.data) < TAG_HEADER_SIZE + 8:
        raise WaveformFormatError(f"{tag.fourcc}-Abschnitt ist zu kurz")
    (len_data,) = struct.unpack_from(">I", tag.data, TAG_HEADER_SIZE)
    payload = tag.content
    if len_data > len(payload):
        raise WaveformFormatError(
            f"{tag.fourcc} meldet {len_data} Byte, vorhanden sind "
            f"{len(payload)}"
        )
    return payload[:len_data]


def _sized_payload(tag: AnlzTag, *, expect: int) -> bytes:
    """Abschnitte mit ``len_entry_bytes`` und ``len_entries``."""
    if len(tag.data) < TAG_HEADER_SIZE + 8:
        raise WaveformFormatError(f"{tag.fourcc}-Abschnitt ist zu kurz")
    entry_size, count = struct.unpack_from(">II", tag.data, TAG_HEADER_SIZE)
    if entry_size != expect:
        raise WaveformFormatError(
            f"{tag.fourcc} meldet {entry_size} Byte je Spalte, erwartet "
            f"waren {expect}"
        )
    needed = entry_size * count
    payload = tag.content
    if needed > len(payload):
        raise WaveformFormatError(
            f"{tag.fourcc} meldet {count} Spalten zu {entry_size} Byte, "
            f"vorhanden sind {len(payload)} Byte"
        )
    return payload[:needed]


def _parse_single_byte(tag: AnlzTag, payload: bytes) -> WaveformColumns:
    """Ein Byte je Spalte: Hoehe in den unteren fuenf Bit."""
    heights = []
    whiteness = []
    for value in payload:
        heights.append((value & 0x1F) / MAX_HEIGHT)
        whiteness.append((value >> 5) / MAX_COLOR)
    shade = tuple(whiteness)
    return WaveformColumns(
        fourcc=tag.fourcc,
        height=tuple(heights),
        red=shade, green=shade, blue=shade,
        raw_columns=tuple(bytes([value]) for value in payload),
    )


def _parse_color_scroll(tag: AnlzTag, payload: bytes) -> WaveformColumns:
    """Zwei Byte je Spalte: drei Farben zu je drei Bit, dann die Hoehe."""
    heights, reds, greens, blues, raw = [], [], [], [], []
    for at in range(0, len(payload), 2):
        (value,) = struct.unpack_from(">H", payload, at)
        reds.append((value >> 13 & 0x07) / MAX_COLOR)
        greens.append((value >> 10 & 0x07) / MAX_COLOR)
        blues.append((value >> 7 & 0x07) / MAX_COLOR)
        heights.append((value >> 2 & 0x1F) / MAX_HEIGHT)
        raw.append(payload[at:at + 2])
    return WaveformColumns(
        fourcc=tag.fourcc,
        height=tuple(heights),
        red=tuple(reds), green=tuple(greens), blue=tuple(blues),
        raw_columns=tuple(raw),
    )


def _parse_color_preview(tag: AnlzTag, payload: bytes) -> WaveformColumns:
    """Sechs Byte je Spalte.

    Die ersten zwei Byte sind nicht entschluesselt und werden nur in
    ``raw_columns`` weitergereicht. Aus den drei Frequenzdritteln entsteht
    die Farbe, aus dem lautesten Drittel die Hoehe.
    """
    heights, reds, greens, blues, raw = [], [], [], [], []
    for at in range(0, len(payload), 6):
        column = payload[at:at + 6]
        low = column[3] / MAX_ENERGY
        mid = column[4] / MAX_ENERGY
        high = column[5] / MAX_ENERGY
        reds.append(low)
        greens.append(mid)
        blues.append(high)
        heights.append(max(low, mid, high))
        raw.append(column)
    return WaveformColumns(
        fourcc=tag.fourcc,
        height=tuple(heights),
        red=tuple(reds), green=tuple(greens), blue=tuple(blues),
        raw_columns=tuple(raw),
    )
