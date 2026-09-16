"""Datentraeger-Erkennung - dynamisch, ohne festen Laufwerksbuchstaben.

``list_volumes()`` fragt bei jedem Aufruf das Betriebssystem neu; es gibt
keine Konfiguration, die ein Laufwerk wie ``E:\\`` fest eintraegt. Damit
funktioniert die Erkennung unabhaengig davon, welchen Buchstaben Windows
einem Stick gerade zuweist.

Die eigentlichen WinAPI-Aufrufe stehen in eigenen, kleinen Funktionen
(``_raw_logical_drive_roots``, ``_raw_drive_type``, ``_raw_volume_info``),
damit Tests sie ohne echte Hardware ersetzen koennen.

``VolumeWatcher`` ist fuer den regelmaessigen Aufruf aus dem GUI-Tick gedacht
(wie ``CdjApplication.tick()`` das schon mit ``AnalysisWorker.drain()`` tut):
``poll()`` ist billig, wenn nichts zu tun ist, und desselt echte
WinAPI-Aufrufe auf ein Mindestintervall, damit das Abfragen mehrerer
Laufwerksbuchstaben die Oberflaeche nicht spuerbar bremst (Abschnitt 13,
Performance).
"""

from __future__ import annotations

import ctypes
import platform
import string
import time
from collections.abc import Callable
from dataclasses import dataclass

#: Windows-Konstanten aus ``GetDriveTypeW`` (winbase.h). Nur die Werte, die
#: als "das koennte ein Rekordbox-USB-Stick sein" infrage kommen, werden von
#: ``list_volumes`` standardmaessig beruecksichtigt.
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

#: Standardmaessig beruecksichtigte Laufwerkstypen: austauschbare Datentraeger
#: (der Normalfall fuer einen USB-Stick) und feste Datentraeger (manche
#: USB-Gehaeuse melden sich als "fest", je nach Formatierung/Firmware -
#: siehe Anforderung: nicht auf einen Laufwerksbuchstaben oder eine einzelne
#: Bauart festlegen). Netzlaufwerke und CD-ROM/RAM-Disk bleiben aussen vor.
DEFAULT_DRIVE_TYPES: tuple[int, ...] = (DRIVE_REMOVABLE, DRIVE_FIXED)

_IS_WINDOWS = platform.system() == "Windows"


@dataclass(frozen=True)
class VolumeInfo:
    """Ein zum Zeitpunkt der Abfrage vorhandener Datentraeger."""

    #: z. B. ``"E:\\"`` - jedes Mal frisch ermittelt, nie fest einprogrammiert.
    root_path: str
    label: str = ""
    filesystem: str = ""
    #: Windows-Datentraeger-Seriennummer als 8-stelliger Hex-Text, z. B.
    #: ``"1A2B3C4D"``. Leer, wenn nicht ermittelbar (Laufwerk nicht bereit).
    #: Dient in Phase 8 als Teil der Cache-Identitaet ("USB-ID").
    serial: str = ""
    drive_type: int = DRIVE_UNKNOWN
    #: Gesamtgroesse in Byte. ``0``, wenn nicht ermittelbar - daraus wird im
    #: SOURCE-Bildschirm ein Gedankenstrich statt einer geschaetzten Zahl.
    total_bytes: int = 0
    #: Freier Platz in Byte. ``0``, wenn nicht ermittelbar.
    free_bytes: int = 0

    @property
    def is_removable(self) -> bool:
        return self.drive_type == DRIVE_REMOVABLE

    @property
    def total_mb(self) -> float:
        return self.total_bytes / (1024 * 1024)

    @property
    def free_mb(self) -> float:
        return self.free_bytes / (1024 * 1024)

    @property
    def is_ready(self) -> bool:
        """Ob das Laufwerk gerade lesbar ist (Seriennummer ermittelbar)."""
        return bool(self.serial)


@dataclass(frozen=True)
class VolumeChange:
    """Unterschied zwischen zwei ``VolumeWatcher.poll()``-Aufrufen."""

    added: tuple[VolumeInfo, ...] = ()
    removed: tuple[VolumeInfo, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.removed


# ----------------------------------------------------------------------
# Rohe WinAPI-Aufrufe - einzeln ersetzbar fuer Tests ohne echte Hardware.
# ----------------------------------------------------------------------


def _raw_logical_drive_roots() -> list[str]:
    """Alle vorhandenen Laufwerksbuchstaben, z. B. ``["C:\\\\", "E:\\\\"]``."""
    if not _IS_WINDOWS:
        return []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
    except OSError:  # pragma: no cover - kein plausibler Fall unter Windows
        return []
    return [
        f"{letter}:\\"
        for index, letter in enumerate(string.ascii_uppercase)
        if mask & (1 << index)
    ]


def _raw_drive_type(root: str) -> int:
    if not _IS_WINDOWS:
        return DRIVE_UNKNOWN
    try:
        return int(ctypes.windll.kernel32.GetDriveTypeW(root))  # type: ignore[attr-defined]
    except OSError:  # pragma: no cover
        return DRIVE_UNKNOWN


def _raw_volume_info(root: str) -> tuple[str, str, str] | None:
    """``(label, filesystem, serial_hex)`` oder ``None`` bei nicht bereitem
    Laufwerk (z. B. Karte gerade entfernt, Laufwerk ohne Medium)."""
    if not _IS_WINDOWS:
        return None
    name_buf = ctypes.create_unicode_buffer(261)
    fs_buf = ctypes.create_unicode_buffer(261)
    serial = ctypes.c_ulong(0)
    max_component = ctypes.c_ulong(0)
    flags = ctypes.c_ulong(0)
    try:
        ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(root),
            name_buf, len(name_buf),
            ctypes.byref(serial),
            ctypes.byref(max_component),
            ctypes.byref(flags),
            fs_buf, len(fs_buf),
        )
    except OSError:
        return None
    if not ok:
        return None
    return (name_buf.value, fs_buf.value, f"{serial.value:08X}")


def _raw_disk_space(root: str) -> tuple[int, int] | None:
    """``(total_bytes, free_bytes)`` oder ``None``.

    Die Geraeteinformation des SOURCE-Bildschirms (Handbuch S. 18) zeigt
    "Total" und "Available". Ein Laufwerk, das gerade abgezogen wird,
    beantwortet die Frage nicht mehr - dann bleibt der Wert ``None`` und
    die Anzeige schreibt einen Gedankenstrich, statt den letzten bekannten
    Wert weiterzubehaupten.
    """
    if not _IS_WINDOWS:
        return None
    free_for_caller = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    free_total = ctypes.c_ulonglong(0)
    try:
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(root),
            ctypes.byref(free_for_caller),
            ctypes.byref(total),
            ctypes.byref(free_total),
        )
    except OSError:  # pragma: no cover
        return None
    if not ok:
        return None
    # Der fuer den aufrufenden Benutzer verfuegbare Platz ist der ehrliche
    # Wert - bei Kontingenten kann er kleiner sein als der freie Platz.
    return (int(total.value), int(free_for_caller.value))


# ----------------------------------------------------------------------
# Oeffentliche Erkennung
# ----------------------------------------------------------------------


def list_volumes(
    *,
    drive_types: tuple[int, ...] = DEFAULT_DRIVE_TYPES,
    logical_drive_roots: Callable[[], list[str]] = _raw_logical_drive_roots,
    drive_type_of: Callable[[str], int] = _raw_drive_type,
    volume_info_of: Callable[[str], tuple[str, str, str] | None] = _raw_volume_info,
    disk_space_of: Callable[[str], tuple[int, int] | None] = _raw_disk_space,
) -> tuple[VolumeInfo, ...]:
    """Alle passenden Datentraeger *jetzt* abfragen.

    Ein Laufwerk, das gerade nicht bereit ist (z. B. mitten im Aus- oder
    Einstecken), wird stillschweigend uebersprungen statt einen Fehler
    auszuloesen - das naechste ``poll()`` sieht es dann.
    """
    volumes: list[VolumeInfo] = []
    for root in logical_drive_roots():
        drive_type = drive_type_of(root)
        if drive_type not in drive_types:
            continue
        info = volume_info_of(root)
        if info is None:
            continue
        label, filesystem, serial = info
        space = disk_space_of(root)
        total_bytes, free_bytes = space if space is not None else (0, 0)
        volumes.append(
            VolumeInfo(
                root_path=root,
                label=label,
                filesystem=filesystem,
                serial=serial,
                drive_type=drive_type,
                total_bytes=total_bytes,
                free_bytes=free_bytes,
            )
        )
    return tuple(volumes)


class VolumeWatcher:
    """Beobachtet angeschlossene Datentraeger ueber wiederholtes ``poll()``.

    Fuer den Aufruf aus dem GUI-Tick gedacht (siehe ``CdjApplication.tick()``).
    Echte Abfragen passieren hoechstens alle ``rescan_interval_s`` Sekunden -
    dazwischen ist ``poll()`` eine reine Vergleichsoperation auf dem zuletzt
    bekannten Zustand und damit beliebig oft aufrufbar, ohne die Oberflaeche
    zu bremsen.
    """

    def __init__(
        self,
        *,
        rescan_interval_s: float = 1.5,
        lister: Callable[[], tuple[VolumeInfo, ...]] = list_volumes,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rescan_interval_s = rescan_interval_s
        self._lister = lister
        self._clock = clock
        self._known: dict[str, VolumeInfo] = {}
        self._last_scan: float | None = None

    @property
    def known(self) -> tuple[VolumeInfo, ...]:
        return tuple(self._known.values())

    def poll(self, *, force: bool = False) -> VolumeChange:
        now = self._clock()
        if not force and self._last_scan is not None:
            if now - self._last_scan < self._rescan_interval_s:
                return VolumeChange()
        self._last_scan = now

        current = {volume.root_path: volume for volume in self._lister()}

        # Ein Datentraeger gilt als *derselbe*, wenn Pfad **und**
        # Seriennummer gleich geblieben sind. Wird ein Stick gegen einen
        # anderen getauscht, vergibt Windows haeufig denselben Buchstaben;
        # ohne den Vergleich der Seriennummer bliebe der Wechsel unbemerkt
        # und der Browser zeigte weiter die Bibliothek des alten Sticks.
        def same(a: VolumeInfo, b: VolumeInfo) -> bool:
            return a.serial == b.serial

        added = tuple(
            volume
            for path, volume in current.items()
            if path not in self._known or not same(volume, self._known[path])
        )
        removed = tuple(
            volume
            for path, volume in self._known.items()
            if path not in current or not same(volume, current[path])
        )
        self._known = current
        return VolumeChange(added=added, removed=removed)
