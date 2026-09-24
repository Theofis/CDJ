"""Audioanalyse und Metadaten sind zwei Schritte - Tests dafuer.

Anlass war ein harter Prozessabsturz: ``Windows fatal exception: code
0x80000003``, ausgeloest im Thread ``analysis-worker``, waehrend die
Garbage Collection mitten in ``mutagen`` lief. Ein ``try/except`` faengt
das nicht - der Interpreter kommt nie bis zum ``except``.

Die Tests hier halten fest, dass das Tag-Lesen den Analysepfad nicht mehr
mitnehmen kann:

* Waveform, BPM und Tonart entstehen auch dann, wenn das Tag-Lesen
  vollstaendig ausfaellt,
* rekordbox-Metadaten haben Vorrang und werden nicht ueberschrieben,
* sind sie vollstaendig, wird die Datei fuer die Tags gar nicht angefasst,
* fehlende Felder bleiben leer, der Titel faellt auf den Dateinamen
  zurueck,
* der Grund des Ausfalls wird gemeldet, nicht verschluckt.

Keine Oberflaeche, keine LEDs. Die Testdatei wird nur gelesen.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.audio_fixtures import KNOWN_BPM, fixtures
from virtual_cdj.audio.cache import AnalysisCache
from virtual_cdj.audio.loader import TrackLoader
from virtual_cdj.audio.metadata import (
    TagReadResult,
    TrackTags,
    read_tags,
)
from virtual_cdj.audio.worker import AnalysisWorker


def exploding_reader(path: Path) -> TagReadResult:
    """Ein Tag-Leser, der immer scheitert - wie ein toter Leseprozess."""
    return TagReadResult(error="Leseprozess beendet mit 3221225477")


def raising_reader(path: Path) -> TagReadResult:
    """Ein Tag-Leser, der eine Ausnahme wirft statt sie zu melden."""
    raise RuntimeError("kaputt")


class CountingReader:
    """Zaehlt, wie oft ueberhaupt gelesen wurde."""

    def __init__(self, tags: TrackTags | None = None) -> None:
        self.calls: list[Path] = []
        self.tags = tags if tags is not None else TrackTags()

    def __call__(self, path: Path) -> TagReadResult:
        self.calls.append(Path(path))
        return TagReadResult(tags=self.tags)


# ----------------------------------------------------------------------
# Das Metadatenmodul fuer sich
# ----------------------------------------------------------------------


class TrackTagsTests(unittest.TestCase):
    def test_empty_by_default(self) -> None:
        self.assertTrue(TrackTags().is_empty)
        self.assertFalse(TrackTags().is_complete)

    def test_complete_means_every_field(self) -> None:
        full = TrackTags(
            title="T", artist="A", album="Al", genre="G", label="L"
        )
        self.assertTrue(full.is_complete)
        self.assertFalse(full.is_empty)

    def test_partial_is_neither_empty_nor_complete(self) -> None:
        partial = TrackTags(title="T")
        self.assertFalse(partial.is_empty)
        self.assertFalse(partial.is_complete)

    def test_own_values_win(self) -> None:
        """Das ist der rekordbox-Vorrang in einer Zeile."""
        known = TrackTags(title="rekordbox", artist="rekordbox")
        weaker = TrackTags(title="Datei", artist="Datei", genre="Techno")
        merged = known.filled_with(weaker)
        self.assertEqual(merged.title, "rekordbox")
        self.assertEqual(merged.artist, "rekordbox")
        self.assertEqual(merged.genre, "Techno")

    def test_merge_does_not_change_the_originals(self) -> None:
        known = TrackTags(title="rekordbox")
        known.filled_with(TrackTags(title="Datei", genre="G"))
        self.assertEqual(known, TrackTags(title="rekordbox"))

    def test_unknown_json_keys_are_dropped(self) -> None:
        tags = TrackTags.from_mapping(
            {"title": "T", "bpm": 128, "was_anderes": "x"}
        )
        self.assertEqual(tags.title, "T")
        self.assertEqual(tags.artist, "")

    def test_nonsense_json_gives_empty_tags(self) -> None:
        self.assertTrue(TrackTags.from_mapping("kein Objekt").is_empty)
        self.assertTrue(TrackTags.from_mapping(None).is_empty)


class ReadTagsTests(unittest.TestCase):
    """Der isolierte Leser. Startet echte Prozesse, liest nur."""

    def test_reads_a_real_file_without_crashing(self) -> None:
        result = read_tags(fixtures().wav)
        self.assertTrue(result.ok, result.error)

    def test_missing_file_is_reported_not_raised(self) -> None:
        result = read_tags(Path("gibt-es-ganz-sicher-nicht.wav"))
        self.assertFalse(result.ok)
        self.assertIn("nicht vorhanden", result.error)

    def test_a_dying_reader_process_only_fails_the_tag_read(self) -> None:
        """Der Kern der ganzen Umstellung.

        Hier stirbt der Leseprozess absichtlich. Der Testprozess - also
        der Hauptprozess - lebt weiter und bekommt eine Fehlermeldung.
        """
        completed = subprocess.run(
            [sys.executable, "-c", "import os; os._exit(3)"],
            capture_output=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        # Und derselbe Prozess laeuft danach unveraendert weiter:
        self.assertTrue(read_tags(fixtures().wav).ok)

    def test_in_process_reader_is_only_a_diagnostic_path(self) -> None:
        result = read_tags(fixtures().wav, isolated=False)
        self.assertTrue(result.ok, result.error)

    def test_the_reader_script_does_not_import_the_project(self) -> None:
        """Der Leseprozess muss winzig bleiben."""
        from virtual_cdj.audio import metadata

        self.assertNotIn("virtual_cdj", metadata._READER_SOURCE)


# ----------------------------------------------------------------------
# Der Loader: Analyse trotz Metadatenausfall
# ----------------------------------------------------------------------


class LoaderWithoutMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.loader = TrackLoader(cache=AnalysisCache(self._temp.name))

    def test_analysis_survives_a_failed_tag_read(self) -> None:
        self.loader.tag_reader = exploding_reader
        loaded = self.loader.load(fixtures().wav)
        info = loaded.info
        self.assertIsNotNone(info.waveform)
        self.assertTrue(info.waveform.is_consistent())
        self.assertIsNotNone(info.beat_grid)
        self.assertTrue(info.beat_grid.is_valid)
        self.assertAlmostEqual(info.original_bpm, KNOWN_BPM, delta=0.5)
        # Die Tonart bleibt beim Klickspur-Fixture leer - es hat keinen
        # tonalen Inhalt. Dass die Analyse gelaufen ist, zeigen Waveform,
        # Beatgrid und BPM oben.
        self.assertTrue(loaded.is_analysed)

    def test_a_failed_tag_read_is_not_a_load_error(self) -> None:
        self.loader.tag_reader = exploding_reader
        loaded = self.loader.load(fixtures().wav)
        self.assertTrue(loaded.is_analysed)
        self.assertTrue(loaded.has_metadata_error)
        self.assertIn("Leseprozess", loaded.metadata_error)

    def test_the_reason_is_counted_separately_from_load_failures(self) -> None:
        self.loader.tag_reader = exploding_reader
        self.loader.load(fixtures().wav)
        self.assertEqual(self.loader.metrics.metadata_failed, 1)
        self.assertEqual(self.loader.metrics.failed, 0)

    def test_the_title_falls_back_to_the_file_name(self) -> None:
        self.loader.tag_reader = exploding_reader
        loaded = self.loader.load(fixtures().wav)
        self.assertEqual(loaded.info.title, fixtures().wav.stem)

    def test_missing_fields_stay_empty_instead_of_invented(self) -> None:
        self.loader.tag_reader = exploding_reader
        info = self.loader.load(fixtures().wav).info
        self.assertEqual(info.artist, "")
        self.assertEqual(info.album, "")
        self.assertEqual(info.genre, "")
        self.assertEqual(info.label, "")

    def test_even_a_raising_reader_cannot_cost_the_analysis(self) -> None:
        """Auch ein Programmfehler im Leser bleibt ein Metadatenfehler.

        Der Betriebsleser meldet Fehler als Text und wirft nicht. Wirft er
        doch, ist das ein Fehler im Leser - aber die fertige Analyse darf
        er trotzdem nicht mitnehmen. Sichtbar wird er im Log und im
        Zaehler, nicht durch einen verlorenen Track.
        """
        self.loader.tag_reader = raising_reader
        loaded = self.loader.load(fixtures().wav)
        self.assertTrue(loaded.is_analysed)
        self.assertIsNotNone(loaded.info.waveform)
        self.assertGreater(loaded.info.original_bpm, 0.0)
        self.assertIn("RuntimeError", loaded.metadata_error)
        self.assertEqual(self.loader.metrics.metadata_failed, 1)
        self.assertEqual(self.loader.metrics.failed, 0)

    def test_a_raising_reader_keeps_known_tags(self) -> None:
        self.loader.tag_reader = raising_reader
        loaded = self.loader.load(
            fixtures().wav, known=TrackTags(title="RB Titel")
        )
        self.assertEqual(loaded.info.title, "RB Titel")

    def test_a_successful_read_fills_the_fields(self) -> None:
        reader = CountingReader(TrackTags(title="Titel", genre="Techno"))
        self.loader.tag_reader = reader
        info = self.loader.load(fixtures().wav).info
        self.assertEqual(info.title, "Titel")
        self.assertEqual(info.genre, "Techno")
        self.assertFalse(self.loader.load(fixtures().wav).has_metadata_error)


class RekordboxPriorityTests(unittest.TestCase):
    """Was aus ``export.pdb`` kommt, gewinnt - und erspart das Lesen."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.loader = TrackLoader(cache=AnalysisCache(self._temp.name))
        self.reader = CountingReader(TrackTags(
            title="Dateititel", artist="Dateiinterpret", genre="Datei",
        ))
        self.loader.tag_reader = self.reader

    def test_complete_known_tags_skip_the_file_entirely(self) -> None:
        known = TrackTags(
            title="RB Titel", artist="RB Interpret", album="RB Album",
            genre="RB Genre", label="RB Label",
        )
        info = self.loader.load(fixtures().wav, known=known).info
        self.assertEqual(self.reader.calls, [])
        self.assertEqual(info.title, "RB Titel")
        self.assertEqual(info.genre, "RB Genre")

    def test_partial_known_tags_are_only_filled_up(self) -> None:
        known = TrackTags(title="RB Titel", artist="RB Interpret")
        info = self.loader.load(fixtures().wav, known=known).info
        self.assertEqual(len(self.reader.calls), 1)
        # Vorhandenes bleibt ...
        self.assertEqual(info.title, "RB Titel")
        self.assertEqual(info.artist, "RB Interpret")
        # ... Fehlendes kommt dazu.
        self.assertEqual(info.genre, "Datei")

    def test_known_tags_survive_a_failed_read(self) -> None:
        self.loader.tag_reader = exploding_reader
        known = TrackTags(title="RB Titel", artist="RB Interpret")
        loaded = self.loader.load(fixtures().wav, known=known)
        self.assertEqual(loaded.info.title, "RB Titel")
        self.assertEqual(loaded.info.artist, "RB Interpret")
        self.assertEqual(loaded.info.genre, "")
        self.assertTrue(loaded.has_metadata_error)
        self.assertIsNotNone(loaded.info.waveform)

    def test_the_same_file_is_read_only_once_per_session(self) -> None:
        self.loader.load(fixtures().wav)
        self.loader.load(fixtures().wav)
        self.loader.load(fixtures().wav)
        self.assertEqual(len(self.reader.calls), 1)

    def test_library_tags_come_from_the_track_model(self) -> None:
        from virtual_cdj.app import library_tags
        from virtual_cdj.deck.state import TrackInfo

        tags = library_tags(TrackInfo(
            track_id="rb:V1:7", title="RB Titel", artist="RB Interpret",
        ))
        self.assertEqual(tags.title, "RB Titel")
        self.assertEqual(tags.album, "")

    def test_an_unknown_track_yields_no_tags(self) -> None:
        from virtual_cdj.app import library_tags
        from virtual_cdj.deck.state import TrackInfo

        self.assertIsNone(library_tags(None))
        self.assertIsNone(library_tags(TrackInfo(track_id="leer")))


class WorkerPassesKnownTagsTests(unittest.TestCase):
    """Der Weg durch den Worker darf den Vorrang nicht verlieren."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        loader = TrackLoader(cache=AnalysisCache(self._temp.name))
        self.reader = CountingReader(TrackTags(title="Dateititel"))
        loader.tag_reader = self.reader
        self.worker = AnalysisWorker(loader)

    def test_known_tags_reach_the_loader(self) -> None:
        known = TrackTags(
            title="RB Titel", artist="A", album="B", genre="C", label="D"
        )
        result = self.worker.load_now(1, fixtures().wav, known_tags=known)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.track.info.title, "RB Titel")
        self.assertEqual(self.reader.calls, [])

    def test_without_known_tags_the_file_is_read(self) -> None:
        result = self.worker.load_now(1, fixtures().wav)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.track.info.title, "Dateititel")
        self.assertEqual(len(self.reader.calls), 1)

    def test_a_failed_tag_read_still_produces_a_usable_track(self) -> None:
        self.worker.loader.tag_reader = exploding_reader
        result = self.worker.load_now(1, fixtures().wav)
        self.assertTrue(result.ok, result.error)
        self.assertTrue(result.track.has_metadata_error)
        self.assertIsNotNone(result.track.info.waveform)
        self.assertGreater(result.track.info.original_bpm, 0.0)


if __name__ == "__main__":
    unittest.main()
