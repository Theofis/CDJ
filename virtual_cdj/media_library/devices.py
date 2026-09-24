"""``UsbDeviceService`` - angeschlossene Datentraeger als Datenquellen.

Die Schicht zwischen ``volumes.py`` (welche Laufwerke gibt es?) und der
Anwendung (welche Quellen zeigt SOURCE?). Sie beantwortet eine Frage:

    Welche Datentraeger haengen gerade dran, und was steht darauf?

Aufgabenteilung
---------------
``volumes.VolumeWatcher``   meldet hinzugekommene/entfernte Laufwerke
``rekordbox.detector``       erkennt das Bibliotheksformat
``rekordbox.reader``         liest ``export.pdb``
``UsbDeviceService``         faedelt das zusammen und haelt den Zustand

Threads
-------
Das Erkennen ist billig (Dateisystemabfragen), das Lesen der Datenbank
nicht unbedingt: an einem echten Stick mit 658 Tracks sind es ~50 ms, bei
einem langsamen Stick oder einer grossen Bibliothek deutlich mehr. Deshalb
dasselbe Muster wie beim ``AnalysisWorker``: das Lesen laeuft in einem
eigenen Thread, das Ergebnis kommt ueber eine Queue, und **abgeholt wird es
im GUI-Thread** in ``poll()``. Es ruft nie jemand aus einem fremden Thread
in die Oberflaeche.

Sicherheit
----------
Es wird ausschliesslich gelesen. Der Service kennt keinen Schreibpfad; er
haelt auch keine offene Datei und keine Datenbankverbindung ueber den
Lesevorgang hinaus. Wird ein Stick waehrend des Lesens abgezogen, endet
der Auftrag mit einem Fehlerzustand, und das Ergebnis wird verworfen,
weil das Geraet nicht mehr in der Liste steht.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum

from .model import DeviceLibrary
from .provider import DetectedMedium, LibraryFormat
from .rekordbox.detector import detect_rekordbox
from .rekordbox.pdb_header import PdbFormatError
from .rekordbox.reader import read_library
from .volumes import VolumeInfo, VolumeWatcher

log = logging.getLogger(__name__)


class DeviceStatus(str, Enum):
    """Zustand eines Datentraegers - genau das, was SOURCE anzeigt."""

    #: Erkannt, Bibliothek wird gerade gelesen.
    SCANNING = "SCANNING"
    #: Rekordbox-Bibliothek gelesen, benutzbar.
    READY = "READY"
    #: Datentraeger ohne Rekordbox-Export.
    NO_LIBRARY = "NO_LIBRARY"
    #: Rekordbox-Struktur erkannt, Format wird (noch) nicht unterstuetzt.
    UNSUPPORTED = "UNSUPPORTED"
    #: Struktur vorhanden, aber nicht lesbar (beschaedigt, Zugriffsfehler).
    ERROR = "ERROR"

    @property
    def has_library(self) -> bool:
        return self is DeviceStatus.READY


#: Klartext je Zustand - die Oberflaeche formuliert nichts selbst.
STATUS_TEXT: dict[DeviceStatus, str] = {
    DeviceStatus.SCANNING: "wird gelesen",
    DeviceStatus.READY: "bereit",
    DeviceStatus.NO_LIBRARY: "keine rekordbox-Bibliothek",
    DeviceStatus.UNSUPPORTED: "Format nicht unterstuetzt",
    DeviceStatus.ERROR: "Fehler",
}

#: Klartext je Bibliotheksformat. ``NONE`` bleibt leer - den Gedankenstrich
#: setzt die Anzeige, nicht die Datenschicht.
FORMAT_TEXT: dict[LibraryFormat, str] = {
    LibraryFormat.NONE: "",
    LibraryFormat.UNKNOWN: "unbekannt",
    LibraryFormat.REKORDBOX_LEGACY: "rekordbox (export.pdb)",
    LibraryFormat.REKORDBOX_DEVICE_LIBRARY_PLUS: (
        "rekordbox Device Library Plus"
    ),
}


@dataclass(frozen=True)
class MediaDevice:
    """Ein angeschlossener Datentraeger samt dem, was darauf gefunden wurde."""

    volume: VolumeInfo
    medium: DetectedMedium
    status: DeviceStatus = DeviceStatus.SCANNING
    #: Gelesene Bibliothek. ``None``, solange nicht gelesen oder nicht
    #: lesbar - niemals eine leere Ersatzbibliothek.
    library: DeviceLibrary | None = None
    #: Klartextgrund bei ``ERROR``. Sonst leer.
    error: str = ""

    # ------------------------------------------------------------------

    @property
    def device_id(self) -> str:
        """Stabile Kennung.

        Seriennummer, solange es eine gibt - der Laufwerksbuchstabe aendert
        sich beim naechsten Einstecken, die Seriennummer nicht. Ohne
        Seriennummer bleibt der Pfad als Notbehelf.
        """
        return self.volume.serial or self.volume.root_path

    @property
    def source_id(self) -> str:
        """Kennung der Quelle im Browse-Modell."""
        return f"USB:{self.device_id}"

    @property
    def name(self) -> str:
        """Anzeigename: Datentraegername, sonst der Pfad."""
        return self.volume.label or self.volume.root_path

    @property
    def is_media_source(self) -> bool:
        """Ob dieser Datentraeger im SOURCE-Bildschirm auftauchen soll.

        ``list_volumes()`` liefert bewusst auch feste Laufwerke, weil
        manche USB-Gehaeuse sich als "fest" melden - auf einen
        Laufwerkstyp darf man sich nicht verlassen. Die Systemplatte ist
        deshalb trotzdem keine DJ-Quelle. Die Unterscheidung:

        * Wechseldatentraeger -> immer eine Quelle, auch ohne Bibliothek
          (der Dateibrowser-Fallback greift dort spaeter).
        * festes Laufwerk -> nur, wenn wirklich eine rekordbox-Struktur
          darauf liegt.
        """
        return self.volume.is_removable or self.medium.is_rekordbox

    @property
    def status_text(self) -> str:
        return STATUS_TEXT.get(self.status, self.status.value)

    @property
    def format_text(self) -> str:
        return FORMAT_TEXT.get(self.medium.format, self.medium.format.value)

    @property
    def track_count(self) -> int:
        return self.library.track_count if self.library is not None else 0

    @property
    def playlist_count(self) -> int:
        return self.library.playlist_count if self.library is not None else 0

    @property
    def warning(self) -> str:
        """Fehler oder Hinweis in einem Satz. Leer heisst "nichts zu melden"."""
        if self.error:
            return self.error
        if self.library is not None and self.library.warnings:
            return "; ".join(self.library.warnings)
        return self.medium.note


@dataclass
class DeviceChange:
    """Was sich seit dem letzten ``poll()`` geaendert hat."""

    attached: tuple[MediaDevice, ...] = ()
    detached: tuple[MediaDevice, ...] = ()
    #: Geraete, deren Zustand sich geaendert hat (z. B. fertig gelesen).
    updated: tuple[MediaDevice, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.attached or self.detached or self.updated)


# ----------------------------------------------------------------------


@dataclass(frozen=True)
class _ReadJob:
    """Auftrag an den Lesethread."""

    device_id: str
    medium: DetectedMedium


@dataclass(frozen=True)
class _ReadResult:
    device_id: str
    library: DeviceLibrary | None = None
    error: str = ""


class UsbDeviceService:
    """Erkennt Datentraeger und liest ihre Bibliothek - nur lesend.

    Ablauf je Datentraeger::

        angesteckt -> erkennen (billig, im GUI-Thread)
                   -> SCANNING
                   -> lesen (Thread)
                   -> READY / UNSUPPORTED / NO_LIBRARY / ERROR

    ``poll()`` ist fuer den Aufruf aus dem GUI-Tick gedacht und darf beliebig
    oft gerufen werden; echte Betriebssystemabfragen sind darin auf
    ``rescan_interval_s`` gedrosselt (siehe ``VolumeWatcher``).
    """

    def __init__(
        self,
        *,
        rescan_interval_s: float = 1.5,
        watcher: VolumeWatcher | None = None,
        detector: Callable[[VolumeInfo], DetectedMedium] = detect_rekordbox,
        library_reader: Callable[[DetectedMedium], DeviceLibrary] | None = None,
        name: str = "usb-library-reader",
    ) -> None:
        self._watcher = (
            watcher
            if watcher is not None
            else VolumeWatcher(rescan_interval_s=rescan_interval_s)
        )
        self._detect = detector
        #: Austauschbar, damit Tests ohne echten Datentraeger auskommen und
        #: damit spaeter ein zweites Format (Device Library Plus, Serato)
        #: hier eingehaengt werden kann, ohne diese Klasse zu aendern.
        self._read_library = (
            library_reader if library_reader is not None else _read_rekordbox
        )
        self._devices: dict[str, MediaDevice] = {}
        #: Pfad -> Kennung, damit ein entferntes Laufwerk seinem Geraet
        #: zugeordnet werden kann (der Pfad ist das, was verschwindet).
        self._by_path: dict[str, str] = {}
        #: Pfade, die ueber USB STOP freigegeben wurden. Sie bleiben aus der
        #: Geraeteliste draussen, solange das Laufwerk physisch da ist -
        #: sonst waere USB STOP nach dem naechsten ``poll()`` wirkungslos.
        self._released: set[str] = set()

        self._jobs: queue.Queue[_ReadJob | None] = queue.Queue()
        self._results: queue.Queue[_ReadResult] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._name = name
        self._stopping = threading.Event()

        #: Rueckrufe, immer im GUI-Thread aus ``poll()`` heraus.
        self.on_attached: list[Callable[[MediaDevice], None]] = []
        self.on_detached: list[Callable[[MediaDevice], None]] = []
        self.on_updated: list[Callable[[MediaDevice], None]] = []

    # ------------------------------------------------------------------
    # Lebenszyklus
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._run, name=self._name, daemon=True
        )
        self._thread.start()

    def close(self, timeout: float = 2.0) -> None:
        """Lesethread beenden. Danach ist kein Dateizugriff mehr offen."""
        if not self.is_running:
            return
        self._stopping.set()
        self._jobs.put(None)
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Zustand
    # ------------------------------------------------------------------

    @property
    def devices(self) -> tuple[MediaDevice, ...]:
        """Alle bekannten Datentraeger in Reihenfolge des Anschliessens."""
        return tuple(self._devices.values())

    def device(self, device_id: str) -> MediaDevice | None:
        return self._devices.get(device_id)

    # ------------------------------------------------------------------
    # Abfrage
    # ------------------------------------------------------------------

    def poll(self, *, force: bool = False) -> DeviceChange:
        """Aus dem GUI-Tick aufrufen. Blockiert nicht."""
        change = self._watcher.poll(force=force)

        detached: list[MediaDevice] = []
        for volume in change.removed:
            # Ein physisch abgezogenes Laufwerk hebt eine vorherige
            # Freigabe auf: beim naechsten Einstecken ist es wieder eine
            # ganz normale neue Quelle.
            self._released.discard(volume.root_path)
            device_id = self._by_path.pop(volume.root_path, "")
            device = self._devices.pop(device_id, None)
            if device is not None:
                log.info("Datentraeger entfernt: %s", device.name)
                detached.append(device)

        attached: list[MediaDevice] = []
        for volume in change.added:
            if volume.root_path in self._released:
                continue
            device = self._attach(volume)
            attached.append(device)

        updated = self._drain_results()

        for device in detached:
            self._notify(self.on_detached, device)
        for device in attached:
            self._notify(self.on_attached, device)
        for device in updated:
            self._notify(self.on_updated, device)

        return DeviceChange(
            attached=tuple(attached),
            detached=tuple(detached),
            updated=tuple(updated),
        )

    def refresh(self) -> DeviceChange:
        """Manuelles Aktualisieren (Handbuch S. 18: Quelle neu einlesen).

        Erzwingt eine Abfrage des Betriebssystems und liest die Bibliothek
        jedes bereits bekannten Datentraegers neu - ein Stick, der
        zwischenzeitlich in rekordbox veraendert wurde, kommt so ohne
        Aus- und Einstecken wieder in den richtigen Stand.
        """
        change = self.poll(force=True)
        for device in self.devices:
            if device.status is DeviceStatus.SCANNING:
                continue
            self._rescan(device.volume)
        return change

    def disconnect(self, device_id: str) -> MediaDevice | None:
        """USB STOP: einen Datentraeger freigeben (Handbuch S. 18).

        Das ist die **Quellen**-Trennung, nicht das Auswerfen des Laufwerks
        durch das Betriebssystem. Der Dienst vergisst das Geraet, meldet es
        ueber ``on_detached`` ab, und die Quelle verschwindet aus SOURCE.
        Ein noch laufender Lesevorgang laeuft ins Leere: sein Ergebnis wird
        in ``_drain_results`` verworfen, weil das Geraet nicht mehr in der
        Liste steht.

        Bewusst **kein** OS-Eject: ein erzwungenes Aushaengen waehrend noch
        Puffer offen sind, ist genau der Weg, auf dem rekordbox-Sticks
        kaputtgehen. Das physische Abziehen bleibt Sache der Person davor -
        nach dieser Freigabe greift das Programm nicht mehr auf den Stick
        zu, und genau das ist die Aussage von USB STOP.

        Rueckgabe: das freigegebene Geraet, oder ``None`` bei unbekannter
        Kennung (kein Fehler - zweimal STOP ist zweimal dasselbe Ziel).
        """
        device = self._devices.pop(device_id, None)
        if device is None:
            return None
        root_path = device.volume.root_path
        self._by_path.pop(root_path, None)
        self._released.add(root_path)
        log.info("Datentraeger freigegeben (USB STOP): %s", device.name)
        self._notify(self.on_detached, device)
        return device

    # ------------------------------------------------------------------

    def _attach(self, volume: VolumeInfo) -> MediaDevice:
        """Ein neues Laufwerk erkennen und das Lesen anstossen."""
        device = self._rescan(volume)
        log.info(
            "Datentraeger erkannt: %s (%s) -> %s",
            device.name, volume.root_path, device.medium.format.value,
        )
        return device

    def _rescan(self, volume: VolumeInfo) -> MediaDevice:
        """Format erkennen und - falls lesbar - das Lesen beauftragen."""
        medium = self._detect(volume)
        device = MediaDevice(
            volume=volume,
            medium=medium,
            status=_initial_status(medium),
        )
        self._devices[device.device_id] = device
        self._by_path[volume.root_path] = device.device_id
        if device.status is DeviceStatus.SCANNING:
            self._jobs.put(_ReadJob(device_id=device.device_id, medium=medium))
            self.start()
        return device

    def _drain_results(self) -> tuple[MediaDevice, ...]:
        """Fertige Lesevorgaenge uebernehmen."""
        updated: list[MediaDevice] = []
        while True:
            try:
                result = self._results.get_nowait()
            except queue.Empty:
                break
            device = self._devices.get(result.device_id)
            if device is None:
                # Der Stick wurde waehrend des Lesens abgezogen. Das
                # Ergebnis gehoert zu einem Geraet, das es nicht mehr gibt -
                # es wird verworfen, nicht angezeigt.
                log.info(
                    "Lesevorgang verworfen, Datentraeger %s ist weg",
                    result.device_id,
                )
                continue
            if result.library is not None:
                device = replace(
                    device,
                    status=DeviceStatus.READY,
                    library=result.library,
                    error="",
                )
            else:
                device = replace(
                    device,
                    status=DeviceStatus.ERROR,
                    library=None,
                    error=result.error,
                )
            self._devices[result.device_id] = device
            updated.append(device)
        return tuple(updated)

    @staticmethod
    def _notify(
        hooks: list[Callable[[MediaDevice], None]], device: MediaDevice
    ) -> None:
        """Rueckrufe aufrufen; ein defekter Zuhoerer stoppt den Rest nicht."""
        for hook in hooks:
            try:
                hook(device)
            except Exception:  # pragma: no cover - defekter Zuhoerer
                log.exception("Rueckruf fuer %s fehlgeschlagen", device.name)

    # ------------------------------------------------------------------
    # Lesethread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stopping.is_set():
            job = self._jobs.get()
            if job is None:
                return
            self._results.put(self._read(job))

    def _read(self, job: _ReadJob) -> _ReadResult:
        """Eine Bibliothek lesen. Wirft nie - Fehler werden Text."""
        started = time.perf_counter()
        try:
            library = self._read_library(job.medium)
        except PdbFormatError as error:
            return _ReadResult(
                device_id=job.device_id,
                error=f"export.pdb nicht lesbar: {error}",
            )
        except OSError as error:
            # Typisch, wenn der Stick mitten im Lesen abgezogen wird.
            return _ReadResult(
                device_id=job.device_id,
                error=f"Datentraeger nicht mehr erreichbar: {error}",
            )
        except Exception as error:  # pragma: no cover - unerwartet
            log.exception("Unerwarteter Fehler beim Lesen von %s", job.medium)
            return _ReadResult(
                device_id=job.device_id,
                error=f"{type(error).__name__}: {error}",
            )
        log.info(
            "%s in %.3f s gelesen: %d Tracks",
            job.medium.database_path,
            time.perf_counter() - started,
            library.track_count,
        )
        return _ReadResult(device_id=job.device_id, library=library)


def _read_rekordbox(medium: DetectedMedium) -> DeviceLibrary:
    """Standardleser: ``export.pdb`` im Nur-Lese-Modus."""
    return read_library(
        medium.database_path,
        root_path=medium.root_path,
        volume_id=medium.volume_id,
        label=medium.label,
    )


def _initial_status(medium: DetectedMedium) -> DeviceStatus:
    """Zustand direkt nach dem Erkennen, noch ohne Lesevorgang."""
    if medium.format is LibraryFormat.NONE:
        return DeviceStatus.NO_LIBRARY
    if medium.format is LibraryFormat.UNKNOWN:
        return DeviceStatus.ERROR
    if not medium.format.is_supported:
        return DeviceStatus.UNSUPPORTED
    return DeviceStatus.SCANNING


__all__ = [
    "DeviceChange",
    "DeviceStatus",
    "FORMAT_TEXT",
    "STATUS_TEXT",
    "MediaDevice",
    "UsbDeviceService",
]
