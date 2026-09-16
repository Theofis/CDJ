"""Datentraeger-Erkennung, Rekordbox-Leser und die Bruecke nach SOURCE.

Kein Test hier fasst ein echtes Laufwerk an. Die Datentraeger sind
eingesetzte Listen (``VolumeWatcher(lister=...)``), die Datenbanken sind
synthetische ``export.pdb``-Dateien aus ``tests/pdb_fixtures.py``, gebaut
nach derselben Spezifikation, die die Parser dokumentieren. Damit haengt
kein Ergebnis davon ab, welcher Stick gerade im Rechner steckt.

Abgedeckte Faelle aus der Aufgabenstellung:

* Stick beim Programmstart bereits angeschlossen
* Stick nach Programmstart angeschlossen
* Stick waehrend des Betriebs entfernt
* Stick ohne rekordbox-Bibliothek
* unterstuetzte rekordbox-Bibliothek
* nicht unterstuetztes Bibliotheksformat
* beschaedigter Datensatz
* grosse Bibliothek
* Wechsel zwischen mehreren USB-Sticks
"""

from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from tests.pdb_fixtures import large_library, simple_library
from virtual_cdj.deck.library import MediaLibrary, SourceKind
from virtual_cdj.media_library.devices import (
    DeviceStatus,
    MediaDevice,
    UsbDeviceService,
)
from virtual_cdj.media_library.provider import LibraryFormat
from virtual_cdj.media_library.rekordbox.detector import detect_rekordbox
from virtual_cdj.media_library.rekordbox.pdb_header import PdbFormatError
from virtual_cdj.media_library.rekordbox.reader import read_library
from virtual_cdj.media_library.sources import (
    build_library,
    build_source_info,
    flatten_playlists,
    on_device_path,
    track_uid,
)
from virtual_cdj.media_library.volumes import (
    DRIVE_FIXED,
    DRIVE_REMOVABLE,
    VolumeInfo,
    VolumeWatcher,
)

MB = 1024 * 1024


# ----------------------------------------------------------------------
# Hilfsmittel
# ----------------------------------------------------------------------


def make_stick(
    root: Path,
    *,
    database: bytes | None = None,
    device_library_plus: bool = False,
    analysis_dir: bool = True,
) -> Path:
    """Einen rekordbox-Stick im Dateisystem nachbauen."""
    rekordbox = root / "PIONEER" / "rekordbox"
    rekordbox.mkdir(parents=True, exist_ok=True)
    if database is not None:
        (rekordbox / "export.pdb").write_bytes(database)
    if device_library_plus:
        # Kein echter SQLCipher-Inhalt noetig: die Erkennung prueft nur,
        # ob die Datei mit dem SQLite-Magic beginnt.
        (rekordbox / "exportLibrary.db").write_bytes(b"\x01\x02" * 64)
    if analysis_dir:
        (root / "PIONEER" / "USBANLZ").mkdir(parents=True, exist_ok=True)
    (root / "Contents").mkdir(exist_ok=True)
    return root


def volume(
    root: Path,
    *,
    serial: str = "AAAA0001",
    label: str = "STICK",
    removable: bool = True,
    total: int = 32 * 1024 * MB,
    free: int = 12 * 1024 * MB,
) -> VolumeInfo:
    return VolumeInfo(
        root_path=str(root),
        label=label,
        filesystem="FAT32",
        serial=serial,
        drive_type=DRIVE_REMOVABLE if removable else DRIVE_FIXED,
        total_bytes=total,
        free_bytes=free,
    )


class ServiceHarness:
    """Ein ``UsbDeviceService`` mit eingesetzter Datentraegerliste."""

    def __init__(self, volumes: list[VolumeInfo] | None = None) -> None:
        self.volumes: list[VolumeInfo] = list(volumes or [])
        self.now = 0.0
        self.service = UsbDeviceService(
            watcher=VolumeWatcher(
                rescan_interval_s=0.0,
                lister=lambda: tuple(self.volumes),
                clock=lambda: self.now,
            )
        )
        self.attached: list[MediaDevice] = []
        self.detached: list[MediaDevice] = []
        self.updated: list[MediaDevice] = []
        self.service.on_attached.append(self.attached.append)
        self.service.on_detached.append(self.detached.append)
        self.service.on_updated.append(self.updated.append)

    def poll_until_settled(self, timeout_s: float = 5.0) -> None:
        """Abfragen, bis kein Datentraeger mehr gelesen wird."""
        self.service.poll(force=True)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if all(
                device.status is not DeviceStatus.SCANNING
                for device in self.service.devices
            ):
                return
            time.sleep(0.01)
            self.service.poll(force=True)
        raise AssertionError("Lesevorgang wurde nicht fertig")

    def close(self) -> None:
        self.service.close()


# ----------------------------------------------------------------------
# Der Leser
# ----------------------------------------------------------------------


class ReaderTests(unittest.TestCase):
    """``RekordboxLibraryReader`` gegen eine synthetische ``export.pdb``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.path = cls.tmp / "export.pdb"
        cls.path.write_bytes(simple_library())
        cls.library = read_library(
            cls.path, root_path="X:\\", volume_id="V1", label="TEST"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_metadata_comes_from_the_database(self) -> None:
        track = self.library.track(1)
        self.assertIsNotNone(track)
        self.assertEqual(track.title, "Erster Track")
        self.assertEqual(track.artist, "Interpret Eins")
        self.assertEqual(track.genre, "Techno")
        self.assertEqual(track.key, "8A")
        self.assertEqual(track.bpm, 128.0)
        self.assertEqual(track.duration_s, 210.0)
        self.assertEqual(track.rating, 4)
        self.assertEqual(track.year, 2024)
        self.assertEqual(track.comment, "ein Kommentar")
        self.assertEqual(track.date_added, "2026-01-15")
        self.assertEqual(track.file_path, "/Contents/A/eins.mp3")
        self.assertEqual(track.file_type, "MP3")

    def test_deleted_rows_are_skipped(self) -> None:
        """Geloeschte Zeilen bleiben im Heap stehen - sie sind keine Tracks."""
        self.assertEqual(self.library.track_count, 2)
        self.assertIsNone(self.library.track(3))

    def test_missing_values_stay_empty(self) -> None:
        """Kein Wert wird geschaetzt, kein Platzhalter erfunden."""
        track = self.library.track(2)
        self.assertEqual(track.key, "")
        self.assertEqual(track.rating, 0)
        self.assertEqual(track.year, 0)
        self.assertEqual(track.comment, "")
        self.assertEqual(track.album, "")

    def test_analysis_data_is_not_read_here(self) -> None:
        """Beatgrid/Waveform/Cues kommen aus den ANLZ-Dateien, nicht der PDB."""
        for track in self.library.tracks:
            self.assertFalse(track.has_performance_data)
            self.assertEqual(len(track.hot_cues), 8)
            self.assertEqual(track.active_hot_cues, ())

    def test_playlist_folders_keep_their_depth(self) -> None:
        roots = self.library.playlists
        self.assertEqual([node.name for node in roots], ["Ordner", "Oben"])
        folder = roots[0]
        self.assertTrue(folder.is_folder)
        self.assertEqual(
            [child.name for child in folder.children], ["Innen A", "Innen B"]
        )
        self.assertFalse(roots[1].is_folder)

    def test_playlist_order_follows_entry_index(self) -> None:
        """Die Reihenfolge der Playlist ist nicht die der Datenbankzeilen."""
        inner_a = self.library.playlist(11)
        self.assertEqual(inner_a.track_ids, (2, 1))

    def test_playlist_count_ignores_folders(self) -> None:
        self.assertEqual(self.library.playlist_count, 3)

    def test_history_is_separate_from_playlists(self) -> None:
        self.assertEqual([node.name for node in self.library.history],
                         ["HISTORY 001"])
        self.assertEqual(self.library.history[0].track_ids, (1,))

    def test_library_is_read_only(self) -> None:
        self.assertTrue(self.library.read_only)
        self.assertTrue(self.library.read_only_reason)

    def test_reading_does_not_touch_the_file(self) -> None:
        """Nur-Lese-Modus: Inhalt und Aenderungszeit bleiben gleich."""
        before = self.path.read_bytes()
        before_mtime = self.path.stat().st_mtime_ns
        read_library(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, before_mtime)

    def test_large_library_spans_several_pages(self) -> None:
        big_path = self.tmp / "big.pdb"
        big_path.write_bytes(large_library(400))
        started = time.perf_counter()
        big = read_library(big_path)
        elapsed = time.perf_counter() - started
        self.assertEqual(big.track_count, 400)
        self.assertEqual(big.tracks[0].title, "Track 0001")
        self.assertEqual(big.tracks[-1].title, "Track 0400")
        self.assertEqual(big.warnings, ())
        # Das Lesen der Datenbank ist der billige Teil und darf die
        # Oberflaeche nicht spuerbar aufhalten.
        self.assertLess(elapsed, 2.0, f"{elapsed:.2f}s fuer 400 Tracks")

    def test_broken_header_is_reported_not_swallowed(self) -> None:
        broken = self.tmp / "kaputt.pdb"
        broken.write_bytes(b"\x00" * 16)
        with self.assertRaises(PdbFormatError):
            read_library(broken)

    def test_missing_file_is_reported(self) -> None:
        with self.assertRaises(PdbFormatError):
            read_library(self.tmp / "gibtsnicht.pdb")

    def test_truncated_database_loses_only_the_missing_part(self) -> None:
        """Beschaedigter Datensatz: der Rest muss trotzdem ankommen."""
        cut = self.tmp / "abgeschnitten.pdb"
        raw = simple_library()
        # Die Datei hinter den ersten Seiten abschneiden: die spaeteren
        # Tabellen (Playlists) fehlen dann, die Tracks bleiben lesbar.
        cut.write_bytes(raw[: 4096 * 3])
        library = read_library(cut)
        self.assertEqual(library.track_count, 2)
        self.assertEqual(library.playlists, ())


# ----------------------------------------------------------------------
# Die Bruecke nach SOURCE/BROWSE
# ----------------------------------------------------------------------


class BridgeTests(unittest.TestCase):
    """``media_library.sources`` - vom Datentraeger ins Browse-Modell."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.root = make_stick(cls.tmp / "stick", database=simple_library())
        cls.harness = ServiceHarness([volume(cls.root, label="SHORDY")])
        cls.harness.poll_until_settled()
        cls.device = cls.harness.service.devices[0]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.harness.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_source_info_carries_the_real_device_data(self) -> None:
        info = build_source_info(self.device)
        self.assertEqual(info.name, "SHORDY")
        self.assertIs(info.kind, SourceKind.USB)
        self.assertEqual(info.status, "bereit")
        self.assertEqual(info.library_format, "rekordbox (export.pdb)")
        self.assertEqual(info.filesystem, "FAT32")
        self.assertAlmostEqual(info.total_mb, 32 * 1024, places=0)
        self.assertAlmostEqual(info.available_mb, 12 * 1024, places=0)
        self.assertTrue(info.has_library)

    def test_browse_library_shows_the_real_tracks(self) -> None:
        library = build_library(self.device)
        self.assertEqual(len(library.tracks()), 2)
        self.assertEqual(library.info.songs, 2)
        self.assertEqual(library.info.playlists, 3)
        titles = {track.title for track in library.tracks()}
        self.assertEqual(titles, {"Erster Track", "Zweiter Track"})

    def test_categories_appear_only_when_filled(self) -> None:
        library = build_library(self.device)
        categories = library.categories()
        self.assertIn("TRACK", categories)
        self.assertIn("ARTIST", categories)
        self.assertIn("PLAYLIST", categories)
        # Kein Track hat ein Album - die Spalte darf nicht erscheinen.
        self.assertNotIn("ALBUM", categories)

    def test_file_path_becomes_a_path_on_this_volume(self) -> None:
        """Der Laufwerksbuchstabe kommt von der Erkennung, nicht aus der DB."""
        library = build_library(self.device)
        track = next(
            t for t in library.tracks() if t.title == "Erster Track"
        )
        self.assertTrue(track.file_path.startswith(str(self.root)))
        self.assertTrue(track.file_path.endswith("eins.mp3"))
        self.assertNotIn("/Contents", track.file_path)

    def test_empty_stored_path_stays_empty(self) -> None:
        self.assertEqual(on_device_path("D:\\", ""), "")

    def test_track_ids_are_unique_across_volumes(self) -> None:
        """Zwei Sticks vergeben beide Track-ID 1 - sie duerfen sich nicht
        gegenseitig ueberschreiben."""
        self.assertNotEqual(track_uid("V1", 1), track_uid("V2", 1))

    def test_playlist_folders_are_flattened_unambiguously(self) -> None:
        library = build_library(self.device)
        names = set(library.playlists())
        self.assertEqual(names, {"Ordner / Innen A", "Ordner / Innen B", "Oben"})

    def test_playlist_track_order_survives_the_bridge(self) -> None:
        """Die Reihenfolge von rekordbox muss die angezeigte sein.

        Sie darf nicht von der Sortierung der Trackliste ueberschrieben
        werden - "Innen A" fuehrt Track 2 vor Track 1, obwohl Track 1 in
        der Quelle vorne steht.
        """
        library = build_library(self.device)
        node = library.node(("PLAYLIST", "Ordner / Innen A"))
        self.assertEqual(
            [entry.track.title for entry in node.entries],
            ["Zweiter Track", "Erster Track"],
        )

    def test_playlist_numbers_count_within_the_playlist(self) -> None:
        """Die Spalte "#" zaehlt in der Playlist, nicht in der Quelle."""
        library = build_library(self.device)
        node = library.node(("PLAYLIST", "Ordner / Innen A"))
        self.assertEqual([entry.number for entry in node.entries], [1, 2])

    def test_flatten_keeps_slashes_in_playlist_names_readable(self) -> None:
        """Ein Playlist-Name darf selbst einen Schraegstrich enthalten."""
        from virtual_cdj.media_library.model import PlaylistNode

        tree = (
            PlaylistNode(
                id=1, name="Ordner", is_folder=True,
                children=(
                    PlaylistNode(id=2, name="Hardtechno/Industrial",
                                 track_ids=(7,)),
                ),
            ),
        )
        flat = flatten_playlists(tree, volume_id="V1")
        self.assertEqual(
            list(flat), ["Ordner / Hardtechno/Industrial"]
        )

    def test_analysis_fields_stay_empty_until_loaded(self) -> None:
        library = build_library(self.device)
        for track in library.tracks():
            self.assertIsNone(track.waveform)
            self.assertIsNone(track.beat_grid)
            self.assertEqual(track.hot_cues, ())


# ----------------------------------------------------------------------
# Der Dienst
# ----------------------------------------------------------------------


class DeviceServiceTests(unittest.TestCase):
    """``UsbDeviceService`` - anstecken, lesen, abziehen, wechseln."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.harness: ServiceHarness | None = None

    def tearDown(self) -> None:
        if self.harness is not None:
            self.harness.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def stick(self, name: str, **kwargs) -> Path:
        return make_stick(self.tmp / name, **kwargs)

    # -- Grundfaelle ---------------------------------------------------

    def test_stick_already_attached_at_startup(self) -> None:
        root = self.stick("a", database=simple_library())
        self.harness = ServiceHarness([volume(root)])
        self.harness.poll_until_settled()

        device = self.harness.service.devices[0]
        self.assertIs(device.status, DeviceStatus.READY)
        self.assertEqual(device.track_count, 2)
        self.assertEqual(device.playlist_count, 3)
        self.assertEqual([d.name for d in self.harness.attached], ["STICK"])

    def test_stick_attached_after_startup(self) -> None:
        self.harness = ServiceHarness([])
        self.harness.poll_until_settled()
        self.assertEqual(self.harness.service.devices, ())

        root = self.stick("b", database=simple_library())
        self.harness.volumes.append(volume(root, serial="BBBB0002"))
        self.harness.poll_until_settled()

        self.assertEqual(len(self.harness.service.devices), 1)
        self.assertEqual(len(self.harness.attached), 1)
        self.assertIs(
            self.harness.service.devices[0].status, DeviceStatus.READY
        )

    def test_stick_removed_during_operation(self) -> None:
        root = self.stick("c", database=simple_library())
        self.harness = ServiceHarness([volume(root, serial="CCCC0003")])
        self.harness.poll_until_settled()

        self.harness.volumes.clear()
        self.harness.service.poll(force=True)

        self.assertEqual(self.harness.service.devices, ())
        self.assertEqual(len(self.harness.detached), 1)
        self.assertEqual(self.harness.detached[0].track_count, 2)

    def test_stick_without_rekordbox_library(self) -> None:
        root = self.tmp / "leer"
        (root / "Musik").mkdir(parents=True)
        self.harness = ServiceHarness([volume(root, serial="DDDD0004")])
        self.harness.poll_until_settled()

        device = self.harness.service.devices[0]
        self.assertIs(device.status, DeviceStatus.NO_LIBRARY)
        self.assertIs(device.medium.format, LibraryFormat.NONE)
        self.assertIsNone(device.library)
        self.assertEqual(device.status_text, "keine rekordbox-Bibliothek")
        # Ein Wechseldatentraeger bleibt eine Quelle, auch ohne Bibliothek.
        self.assertTrue(device.is_media_source)

    def test_unsupported_library_format_is_named_not_faked(self) -> None:
        root = self.stick("plus", device_library_plus=True)
        self.harness = ServiceHarness([volume(root, serial="EEEE0005")])
        self.harness.poll_until_settled()

        device = self.harness.service.devices[0]
        self.assertIs(device.status, DeviceStatus.UNSUPPORTED)
        self.assertIs(
            device.medium.format,
            LibraryFormat.REKORDBOX_DEVICE_LIBRARY_PLUS,
        )
        self.assertIsNone(device.library)
        self.assertIn("nicht unterstuetzt", device.status_text)
        self.assertIn("Device Library Plus", device.warning)
        # Kein Vortaeuschen einer leeren Bibliothek.
        self.assertEqual(device.track_count, 0)
        self.assertFalse(device.status.has_library)

    def test_corrupt_database_ends_in_a_readable_error(self) -> None:
        root = self.stick("kaputt", database=b"\x00" * 32)
        self.harness = ServiceHarness([volume(root, serial="FFFF0006")])
        self.harness.poll_until_settled()

        device = self.harness.service.devices[0]
        self.assertIs(device.status, DeviceStatus.ERROR)
        self.assertIsNone(device.library)
        self.assertTrue(device.warning)

    def test_several_sticks_are_kept_apart(self) -> None:
        first = self.stick("s1", database=simple_library())
        second = self.stick("s2", database=large_library(60))
        self.harness = ServiceHarness([
            volume(first, serial="1111AAAA", label="EINS"),
            volume(second, serial="2222BBBB", label="ZWEI"),
        ])
        self.harness.poll_until_settled()

        by_name = {d.name: d for d in self.harness.service.devices}
        self.assertEqual(set(by_name), {"EINS", "ZWEI"})
        self.assertEqual(by_name["EINS"].track_count, 2)
        self.assertEqual(by_name["ZWEI"].track_count, 60)
        self.assertNotEqual(
            by_name["EINS"].source_id, by_name["ZWEI"].source_id
        )

    def test_swapping_sticks_on_the_same_drive_letter(self) -> None:
        """Windows vergibt oft denselben Buchstaben - die Seriennummer
        entscheidet, ob es derselbe Stick ist."""
        root = self.tmp / "wechsel"
        make_stick(root, database=simple_library())
        self.harness = ServiceHarness([
            volume(root, serial="AAAA1111", label="ALT"),
        ])
        self.harness.poll_until_settled()
        self.assertEqual(self.harness.service.devices[0].name, "ALT")

        # Derselbe Pfad, andere Seriennummer und andere Bibliothek.
        shutil.rmtree(root)
        make_stick(root, database=large_library(40))
        self.harness.volumes[:] = [
            volume(root, serial="BBBB2222", label="NEU")
        ]
        self.harness.poll_until_settled()

        devices = self.harness.service.devices
        self.assertEqual([d.name for d in devices], ["NEU"])
        self.assertEqual(devices[0].track_count, 40)
        self.assertEqual([d.name for d in self.harness.detached], ["ALT"])

    def test_system_disk_is_not_a_source(self) -> None:
        root = self.tmp / "systemplatte"
        root.mkdir()
        self.harness = ServiceHarness([
            volume(root, serial="5555CCCC", label="Windows", removable=False),
        ])
        self.harness.poll_until_settled()
        self.assertFalse(self.harness.service.devices[0].is_media_source)

    def test_fixed_drive_with_library_is_a_source(self) -> None:
        root = self.stick("ssd", database=simple_library())
        self.harness = ServiceHarness([
            volume(root, serial="6666DDDD", label="SSD", removable=False),
        ])
        self.harness.poll_until_settled()

        device = self.harness.service.devices[0]
        self.assertTrue(device.is_media_source)
        # Aber es ist kein USB-Stick und bekommt auch nicht dessen Symbol.
        self.assertIs(build_source_info(device).kind, SourceKind.FILE)

    def test_manual_refresh_rereads_the_library(self) -> None:
        root = self.stick("refresh", database=simple_library())
        self.harness = ServiceHarness([volume(root, serial="7777EEEE")])
        self.harness.poll_until_settled()
        self.assertEqual(self.harness.service.devices[0].track_count, 2)

        # Der Stick wurde zwischenzeitlich in rekordbox veraendert.
        (root / "PIONEER" / "rekordbox" / "export.pdb").write_bytes(
            large_library(30)
        )
        self.harness.service.refresh()
        self.harness.poll_until_settled()
        self.assertEqual(self.harness.service.devices[0].track_count, 30)

    def test_result_of_a_removed_stick_is_discarded(self) -> None:
        """Wird waehrend des Lesens abgezogen, darf kein Ergebnis ankommen."""
        root = self.stick("weg", database=simple_library())
        self.harness = ServiceHarness([volume(root, serial="8888FFFF")])
        self.harness.service.poll(force=True)
        # Noch waehrend SCANNING abziehen.
        self.harness.volumes.clear()
        self.harness.service.poll(force=True)
        self.assertEqual(self.harness.service.devices, ())

        # Das Ergebnis des laufenden Lesevorgangs darf nichts zurueckbringen.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            self.harness.service.poll(force=True)
            time.sleep(0.01)
        self.assertEqual(self.harness.service.devices, ())
        self.assertEqual(self.harness.updated, [])

    def test_service_closes_without_leaving_a_thread(self) -> None:
        root = self.stick("zu", database=simple_library())
        self.harness = ServiceHarness([volume(root, serial="9999AAAA")])
        self.harness.poll_until_settled()
        self.assertTrue(self.harness.service.is_running)
        self.harness.service.close()
        self.assertFalse(self.harness.service.is_running)


# ----------------------------------------------------------------------
# Das Zusammenspiel mit der Quellenliste
# ----------------------------------------------------------------------


class MediaLibraryWiringTests(unittest.TestCase):
    """Datentraeger kommen und gehen - die Quellenliste folgt."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.model = MediaLibrary()
        self.harness = ServiceHarness([])
        self.harness.service.on_attached.append(self._changed)
        self.harness.service.on_updated.append(self._changed)
        self.harness.service.on_detached.append(
            lambda device: self.model.remove(device.source_id)
        )

    def tearDown(self) -> None:
        self.harness.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _changed(self, device: MediaDevice) -> None:
        if device.is_media_source:
            self.model.add(build_library(device))

    def test_source_appears_and_disappears_with_the_stick(self) -> None:
        root = make_stick(self.tmp / "x", database=simple_library())
        self.harness.volumes.append(volume(root, serial="ABCD1234"))
        self.harness.poll_until_settled()

        sources = self.model.sources()
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].songs, 2)
        self.assertEqual(self.model.default_source_id(), sources[0].source_id)

        self.harness.volumes.clear()
        self.harness.service.poll(force=True)
        self.assertEqual(self.model.sources(), ())
        self.assertEqual(self.model.default_source_id(), "")

    def test_browse_reaches_the_tracks_of_the_stick(self) -> None:
        root = make_stick(self.tmp / "y", database=simple_library())
        self.harness.volumes.append(volume(root, serial="BCDE2345"))
        self.harness.poll_until_settled()

        source_id = self.model.default_source_id()
        root_node = self.model.node(source_id)
        labels = {entry.label for entry in root_node.entries}
        self.assertIn("TRACK", labels)
        self.assertIn("PLAYLIST", labels)

        tracks = self.model.node(source_id, ("TRACK",))
        self.assertEqual(len(tracks.entries), 2)
        self.assertTrue(all(entry.is_track for entry in tracks.entries))

    def test_unsupported_stick_is_visible_but_empty(self) -> None:
        """Ein erkannter, nicht lesbarer Stick muss sichtbar bleiben."""
        root = make_stick(self.tmp / "z", device_library_plus=True)
        self.harness.volumes.append(volume(root, serial="CDEF3456"))
        self.harness.poll_until_settled()

        sources = self.model.sources()
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].songs, 0)
        self.assertFalse(sources[0].has_library)
        self.assertIn("nicht unterstuetzt", sources[0].status)
        self.assertTrue(sources[0].warning)


# ----------------------------------------------------------------------
# Erkennung ohne Datenbankinhalt
# ----------------------------------------------------------------------


class DetectionTests(unittest.TestCase):
    """``detect_rekordbox`` mit echten Dateien, aber ohne echten Stick."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_analysis_directory_is_noted(self) -> None:
        root = make_stick(
            self.tmp / "ohne-anlz",
            database=simple_library(),
            analysis_dir=False,
        )
        medium = detect_rekordbox(volume(root))
        self.assertIs(medium.format, LibraryFormat.REKORDBOX_LEGACY)
        self.assertFalse(medium.has_analysis_dir)
        self.assertIn("USBANLZ", medium.note)

    def test_legacy_wins_when_both_formats_are_present(self) -> None:
        root = make_stick(
            self.tmp / "beide",
            database=simple_library(),
            device_library_plus=True,
        )
        medium = detect_rekordbox(volume(root))
        self.assertIs(medium.format, LibraryFormat.REKORDBOX_LEGACY)
        self.assertTrue(medium.database_path.endswith("export.pdb"))
        self.assertIn("Device Library Plus", medium.note)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
