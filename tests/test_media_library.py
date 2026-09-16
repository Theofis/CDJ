"""Phase 1: Datentraeger- und Rekordbox-Strukturerkennung.

Es steht kein echter Rekordbox-USB-Stick zur Verfuegung. Die Tests bauen
deshalb synthetische ``export.pdb``-Koepfe und Verzeichnisbaeume nach der in
``virtual_cdj/media_library/rekordbox/pdb_header.py`` dokumentierten,
gegengeprueften Spezifikation nach - keine erfundenen Werte, sondern die
belegten Offsets, absichtlich falsch nur dort, wo ein Fehlerfall geprueft
wird.

Siehe docs/rekordbox-usb-import.md fuer den Stand je Phase.
"""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from virtual_cdj.media_library.provider import LibraryFormat
from virtual_cdj.media_library.rekordbox.detector import (
    ANALYSIS_DIR_RELATIVE,
    DEVICE_LIBRARY_PLUS_RELATIVE,
    LEGACY_DB_RELATIVE,
    detect_rekordbox,
)
from virtual_cdj.media_library.rekordbox.pdb_header import (
    PdbFormatError,
    PdbTableType,
    parse_header,
)
from virtual_cdj.media_library.volumes import (
    DRIVE_FIXED,
    DRIVE_REMOTE,
    DRIVE_REMOVABLE,
    VolumeInfo,
    VolumeWatcher,
    list_volumes,
)


# --------------------------------------------------------------------------
# Hilfsfunktion: einen export.pdb-Kopf nach der dokumentierten Spezifikation
# von Hand zusammensetzen.
# --------------------------------------------------------------------------


def _build_pdb_header_bytes(
    *,
    page_size: int = 4096,
    tables: tuple[tuple[int, int, int], ...] = ((0, 10, 20), (7, 30, 31)),
    sequence: int = 7,
) -> bytes:
    """``(table_type, first_page, last_page)`` je Tabelle."""
    header = struct.pack(
        "<IIIIII",
        0,  # Nullbytes-Pruefwert
        page_size,
        len(tables),
        0,  # unknown_next
        0,  # unknown_gap
        sequence,
    )
    header += struct.pack("<I", 0)  # Reserve bei 0x18
    for table_type, first_page, last_page in tables:
        header += struct.pack("<IIII", table_type, 0, first_page, last_page)
    return header


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# --------------------------------------------------------------------------
# PdbHeader
# --------------------------------------------------------------------------


class PdbHeaderTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(tmp)

    def test_parses_valid_header(self) -> None:
        path = self.root / "export.pdb"
        path.write_bytes(
            _build_pdb_header_bytes(
                page_size=4096,
                tables=((0, 10, 20), (7, 30, 31)),
                sequence=42,
            )
        )
        header = parse_header(path)
        self.assertEqual(header.page_size, 4096)
        self.assertEqual(header.num_tables, 2)
        self.assertEqual(header.sequence, 42)
        tracks = header.table(PdbTableType.TRACKS)
        self.assertIsNotNone(tracks)
        self.assertEqual((tracks.first_page, tracks.last_page), (10, 20))
        playlists = header.table(PdbTableType.PLAYLIST_TREE)
        self.assertIsNotNone(playlists)
        self.assertEqual((playlists.first_page, playlists.last_page), (30, 31))
        self.assertIsNone(header.table(PdbTableType.ARTWORK))

    def test_unknown_table_type_does_not_crash(self) -> None:
        path = self.root / "export.pdb"
        path.write_bytes(_build_pdb_header_bytes(tables=((999, 1, 2),)))
        header = parse_header(path)
        self.assertIsNone(header.tables[0].known_type)
        self.assertEqual(header.known_table_types, ())

    def test_rejects_too_small_file(self) -> None:
        path = self.root / "export.pdb"
        path.write_bytes(b"\x00" * 8)
        with self.assertRaises(PdbFormatError):
            parse_header(path)

    def test_rejects_wrong_magic(self) -> None:
        path = self.root / "export.pdb"
        data = bytearray(_build_pdb_header_bytes())
        data[0:4] = struct.pack("<I", 1)  # sollte 0 sein
        path.write_bytes(bytes(data))
        with self.assertRaises(PdbFormatError):
            parse_header(path)

    def test_rejects_implausible_page_size(self) -> None:
        path = self.root / "export.pdb"
        path.write_bytes(_build_pdb_header_bytes(page_size=3))
        with self.assertRaises(PdbFormatError):
            parse_header(path)

    def test_rejects_truncated_table_pointers(self) -> None:
        path = self.root / "export.pdb"
        full = _build_pdb_header_bytes(tables=((0, 1, 2), (1, 3, 4), (2, 5, 6)))
        path.write_bytes(full[:-10])  # mitten im letzten Zeiger abgeschnitten
        with self.assertRaises(PdbFormatError):
            parse_header(path)

    def test_missing_file_raises_format_error_not_os_error(self) -> None:
        with self.assertRaises(PdbFormatError):
            parse_header(self.root / "does-not-exist.pdb")


# --------------------------------------------------------------------------
# Datentraeger-Erkennung (VolumeWatcher / list_volumes)
# --------------------------------------------------------------------------


class VolumeListingTests(unittest.TestCase):
    def test_filters_by_drive_type_and_skips_not_ready(self) -> None:
        roots = ["C:\\", "E:\\", "F:\\", "Z:\\"]
        types = {
            "C:\\": DRIVE_FIXED,
            "E:\\": DRIVE_REMOVABLE,
            "F:\\": DRIVE_REMOVABLE,  # nicht bereit -> muss uebersprungen werden
            "Z:\\": DRIVE_REMOTE,  # Netzlaufwerk -> per Vorgabe ausgeschlossen
        }
        info = {
            "C:\\": ("System", "NTFS", "AAAAAAAA"),
            "E:\\": ("PIONEER DJ", "FAT32", "BBBBBBBB"),
            "F:\\": None,
            "Z:\\": ("Share", "NTFS", "CCCCCCCC"),
        }
        volumes = list_volumes(
            logical_drive_roots=lambda: roots,
            drive_type_of=lambda root: types[root],
            volume_info_of=lambda root: info[root],
        )
        self.assertEqual({v.root_path for v in volumes}, {"C:\\", "E:\\"})
        usb = next(v for v in volumes if v.root_path == "E:\\")
        self.assertEqual(usb.label, "PIONEER DJ")
        self.assertEqual(usb.serial, "BBBBBBBB")
        self.assertTrue(usb.is_removable)
        self.assertTrue(usb.is_ready)

    def test_no_hardcoded_drive_letter(self) -> None:
        """Dieselbe Erkennung muss unabhaengig vom konkreten Buchstaben
        funktionieren - das ist die eigentliche Anforderung, nicht nur ein
        Implementierungsdetail."""
        for letter in ("D:\\", "G:\\", "X:\\"):
            volumes = list_volumes(
                logical_drive_roots=lambda letter=letter: [letter],
                drive_type_of=lambda root: DRIVE_REMOVABLE,
                volume_info_of=lambda root: ("STICK", "FAT32", "11111111"),
            )
            self.assertEqual(volumes[0].root_path, letter)


class VolumeWatcherTests(unittest.TestCase):
    def test_detects_added_and_removed_volumes(self) -> None:
        clock = _FakeClock()
        snapshots = [
            (),
            (_vol("E:\\"),),
            (_vol("E:\\"), _vol("F:\\")),
            (_vol("F:\\"),),
        ]
        watcher = VolumeWatcher(
            rescan_interval_s=1.0,
            lister=_Sequence(snapshots),
            clock=clock,
        )

        change = watcher.poll()
        self.assertEqual(change.added, ())
        self.assertEqual(change.removed, ())

        clock.advance(2.0)
        change = watcher.poll()
        self.assertEqual([v.root_path for v in change.added], ["E:\\"])
        self.assertEqual(change.removed, ())

        clock.advance(2.0)
        change = watcher.poll()
        self.assertEqual([v.root_path for v in change.added], ["F:\\"])
        self.assertEqual(change.removed, ())

        clock.advance(2.0)
        change = watcher.poll()
        self.assertEqual(change.added, ())
        self.assertEqual([v.root_path for v in change.removed], ["E:\\"])

        self.assertEqual([v.root_path for v in watcher.known], ["F:\\"])

    def test_respects_rescan_interval_until_forced(self) -> None:
        clock = _FakeClock()
        snapshots = [(), (_vol("E:\\"),)]
        watcher = VolumeWatcher(
            rescan_interval_s=5.0, lister=_Sequence(snapshots), clock=clock
        )
        watcher.poll()  # erste Abfrage, leer
        clock.advance(0.1)  # deutlich unter dem Intervall
        change = watcher.poll()
        self.assertTrue(change.is_empty)  # kein Rescan, obwohl sich was
        # geaendert haette - genau das soll das Intervall verhindern.

        change = watcher.poll(force=True)
        self.assertEqual([v.root_path for v in change.added], ["E:\\"])


def _vol(root: str) -> VolumeInfo:
    return VolumeInfo(
        root_path=root, label="PIONEER DJ", filesystem="FAT32",
        serial="DEADBEEF", drive_type=DRIVE_REMOVABLE,
    )


class _FakeClock:
    def __init__(self) -> None:
        self._now = 0.0

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def __call__(self) -> float:
        return self._now


class _Sequence:
    def __init__(self, snapshots: list[tuple[VolumeInfo, ...]]) -> None:
        self._snapshots = list(snapshots)
        self._index = -1

    def __call__(self) -> tuple[VolumeInfo, ...]:
        self._index = min(self._index + 1, len(self._snapshots) - 1)
        return self._snapshots[self._index]


# --------------------------------------------------------------------------
# Rekordbox-Strukturerkennung
# --------------------------------------------------------------------------


class RekordboxDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(tmp)
        self.volume = VolumeInfo(
            root_path=str(self.root), label="PIONEER DJ",
            filesystem="FAT32", serial="CAFEBABE", drive_type=DRIVE_REMOVABLE,
        )

    def test_no_rekordbox_structure(self) -> None:
        (self.root / "Music" / "song.mp3").parent.mkdir(parents=True)
        (self.root / "Music" / "song.mp3").write_bytes(b"")
        medium = detect_rekordbox(self.volume)
        self.assertEqual(medium.format, LibraryFormat.NONE)
        self.assertFalse(medium.is_rekordbox)
        self.assertEqual(medium.root_path, str(self.root))
        self.assertEqual(medium.volume_id, "CAFEBABE")

    def test_valid_legacy_structure_with_analysis_dir(self) -> None:
        _write(
            self.root / LEGACY_DB_RELATIVE,
            _build_pdb_header_bytes(tables=((0, 1, 2),)),
        )
        (self.root / ANALYSIS_DIR_RELATIVE / "P001" / "00000001").mkdir(
            parents=True
        )
        medium = detect_rekordbox(self.volume)
        self.assertEqual(medium.format, LibraryFormat.REKORDBOX_LEGACY)
        self.assertTrue(medium.is_rekordbox)
        self.assertTrue(medium.is_usable)
        self.assertTrue(medium.has_analysis_dir)
        self.assertEqual(
            medium.database_path, str(self.root / LEGACY_DB_RELATIVE)
        )
        self.assertEqual(medium.note, "")

    def test_valid_legacy_structure_without_analysis_dir(self) -> None:
        _write(self.root / LEGACY_DB_RELATIVE, _build_pdb_header_bytes())
        medium = detect_rekordbox(self.volume)
        self.assertEqual(medium.format, LibraryFormat.REKORDBOX_LEGACY)
        self.assertFalse(medium.has_analysis_dir)
        self.assertIn("USBANLZ", medium.note)

    def test_corrupted_export_pdb_is_reported_not_crashed(self) -> None:
        _write(self.root / LEGACY_DB_RELATIVE, b"nicht wirklich eine pdb")
        medium = detect_rekordbox(self.volume)
        self.assertEqual(medium.format, LibraryFormat.UNKNOWN)
        self.assertFalse(medium.is_usable)
        self.assertIn("Kopf ungueltig", medium.note)
        self.assertEqual(
            medium.database_path, str(self.root / LEGACY_DB_RELATIVE)
        )

    def test_device_library_plus_only_is_recognised_but_unsupported(self) -> None:
        _write(self.root / DEVICE_LIBRARY_PLUS_RELATIVE, b"\x01\x02\x03\x04" * 8)
        medium = detect_rekordbox(self.volume)
        self.assertEqual(
            medium.format, LibraryFormat.REKORDBOX_DEVICE_LIBRARY_PLUS
        )
        self.assertTrue(medium.is_rekordbox)
        self.assertFalse(medium.is_usable)
        self.assertIn("noch nicht implementiert", medium.note)

    def test_prefers_legacy_when_both_databases_present(self) -> None:
        _write(self.root / LEGACY_DB_RELATIVE, _build_pdb_header_bytes())
        _write(self.root / DEVICE_LIBRARY_PLUS_RELATIVE, b"\x00" * 32)
        medium = detect_rekordbox(self.volume)
        self.assertEqual(medium.format, LibraryFormat.REKORDBOX_LEGACY)
        self.assertIn("exportLibrary.db", medium.note)

    def test_unencrypted_device_library_plus_is_noted_distinctly(self) -> None:
        _write(
            self.root / DEVICE_LIBRARY_PLUS_RELATIVE,
            b"SQLite format 3\x00" + b"\x00" * 16,
        )
        medium = detect_rekordbox(self.volume)
        self.assertIn("unverschluesselt", medium.note)

    def test_empty_pdb_table_directory_is_noted(self) -> None:
        _write(self.root / LEGACY_DB_RELATIVE, _build_pdb_header_bytes(tables=()))
        medium = detect_rekordbox(self.volume)
        self.assertEqual(medium.format, LibraryFormat.REKORDBOX_LEGACY)
        self.assertIn("keine Tabellen", medium.note)


if __name__ == "__main__":
    unittest.main()
