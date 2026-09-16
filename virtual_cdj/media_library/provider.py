"""``ExternalLibraryProvider`` - die Schnittstelle zu externen Bibliotheken.

Die uebrige Software (Browser, Deck, Cache) spricht nie direkt mit
Rekordbox-Dateien. Sie kennt nur diese Schnittstelle:

    Rekordbox-USB -> RekordboxUsbProvider  \\
    Serato (spaeter)                        +-> ExternalLibraryProvider -> TrackListLibrary
    Netzwerk-CDJ (spaeter)                 /

``RekordboxUsbProvider`` (Phase 2) ist die erste und bislang einzige
Implementierung. Diese Datei enthaelt bewusst noch keine Formatkenntnis -
die gehoert in ``rekordbox/`` bzw. spaetere Geschwisterpakete.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ..deck.library import TrackListLibrary


class LibraryFormat(str, Enum):
    """Erkanntes Format eines Datentraegers."""

    #: Kein Rekordbox-Export gefunden - ein gewoehnlicher Datentraeger.
    NONE = "NONE"
    #: Datentraeger hat eine Rekordbox-Struktur, aber sie ist beschaedigt
    #: oder ihr Format ist nicht bekannt. Ehrlicher Zwischenzustand statt
    #: eines Absturzes oder einer Erfindung.
    UNKNOWN = "UNKNOWN"
    #: ``PIONEER/rekordbox/export.pdb`` - DeviceSQL, das klassische Format.
    #: Wird von praktisch jedem CDJ/XDJ gelesen. Ab Phase 2 unterstuetzt.
    REKORDBOX_LEGACY = "REKORDBOX_LEGACY"
    #: ``PIONEER/rekordbox/exportLibrary.db`` - "Device Library Plus",
    #: SQLCipher-verschluesselte SQLite-Datenbank neuerer rekordbox-Versionen
    #: (OPUS-QUAD, OMNIS-DUO, XDJ-AZ). Wird erkannt, aber noch nicht
    #: gelesen - siehe docs/rekordbox-usb-import.md, Abschnitt "Offene
    #: Punkte".
    REKORDBOX_DEVICE_LIBRARY_PLUS = "REKORDBOX_DEVICE_LIBRARY_PLUS"

    @property
    def is_supported(self) -> bool:
        """Ob dieses Projekt aus diesem Format wirklich Daten lesen kann."""
        return self is LibraryFormat.REKORDBOX_LEGACY


@dataclass(frozen=True)
class DetectedMedium:
    """Ergebnis der Erkennung eines Datentraegers - unabhaengig vom Format.

    Wird von ``volumes.list_volumes()`` (Datentraeger) und den
    formatspezifischen Detektoren (z. B.
    ``rekordbox.detector.detect_rekordbox``) gemeinsam befuellt.
    """

    #: Wurzelpfad, wie er *jetzt* erkannt wurde - niemals fest einprogrammiert.
    root_path: str
    #: Stabile Kennung fuer den Cache (Phase 8), z. B. die
    #: Datentraeger-Seriennummer als Hex-Text. Leer, wenn nicht ermittelbar.
    volume_id: str = ""
    #: Datentraegername, wie ihn das Betriebssystem meldet.
    label: str = ""
    format: LibraryFormat = LibraryFormat.NONE
    #: Bei einem erkannten Rekordbox-Format: Pfad der Hauptdatenbankdatei.
    database_path: str = ""
    #: Ob ``PIONEER/USBANLZ`` (Beatgrid/Waveform/Cue-Analyse) vorhanden ist.
    has_analysis_dir: bool = False
    #: Ehrlicher Klartext-Hinweis, z. B. warum ein Format nicht unterstuetzt
    #: wird oder was an der Struktur fehlt. Leer, wenn es nichts zu vermerken
    #: gibt.
    note: str = ""

    @property
    def is_rekordbox(self) -> bool:
        return self.format in (
            LibraryFormat.REKORDBOX_LEGACY,
            LibraryFormat.REKORDBOX_DEVICE_LIBRARY_PLUS,
        )

    @property
    def is_usable(self) -> bool:
        """Ob aus diesem Datentraeger jetzt schon gelesen werden kann."""
        return self.format.is_supported


class ExternalLibraryProvider(ABC):
    """Eine externe Bibliotheksquelle, normalisiert auf das Deck-Modell.

    Implementierungen liefern am Ende genau das, was auch die eigene
    Analyse liefert: ``TrackListLibrary`` mit ``TrackInfo``, deren
    ``waveform``/``beat_grid``/``hot_cues``/``memory_cues`` bereits gefuellt
    sind. Der Browser und das Deck behandeln das nicht anders als lokal
    geladene Dateien.

    Ab Phase 2 implementiert von ``rekordbox.provider.RekordboxUsbProvider``.
    """

    @property
    @abstractmethod
    def medium(self) -> DetectedMedium:
        """Der Datentraeger, von dem diese Instanz liest."""

    @abstractmethod
    def build_libraries(self) -> Sequence[TrackListLibrary]:
        """Eine oder mehrere ``TrackListLibrary`` fuer den Browser.

        Mehrzahl, weil ein Datentraeger mehrere Quellen im SOURCE-Bildschirm
        ergeben kann (z. B. Rekordbox-Bibliothek und Verlauf getrennt).
        Darf teuer sein - wird nicht auf dem GUI-Thread aufgerufen
        (Phase 8: Performance).
        """
