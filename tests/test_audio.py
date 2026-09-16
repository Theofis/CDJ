"""Tests der Audioschicht: Dekoder, Format, Analyse, Cache, Wiedergabe."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from tests.audio_fixtures import (
    KNOWN_BPM,
    RAMP_DURATION_S,
    SAMPLE_RATE,
    fixtures,
    ramp_seconds,
    ramp_track,
    ramp_value,
)
from virtual_cdj.audio.analysis import (
    ANALYSIS_VERSION,
    TrackAnalyzer,
    compute_levels,
    fit_beat_line,
    fold_tempo,
)
from virtual_cdj.audio.cache import AnalysisCache, CacheKey
from virtual_cdj.audio.decoder import (
    DecodeError,
    SoundFileDecoder,
    UnsupportedFormat,
    decode_file,
    open_decoder,
    register_backend,
    supported_extensions,
)
from virtual_cdj.audio.engine import AudioEngine, DeckVoice
from virtual_cdj.audio.format import (
    ENGINE_SAMPLE_RATE,
    AudioBuffer,
    AudioFormatError,
    to_internal,
)
from virtual_cdj.audio.loader import TrackLoader
from virtual_cdj.audio.resample import resample
from virtual_cdj.audio.tempo_proc import (
    KeyLockTempoProcessor,
    SimpleResamplerTempoProcessor,
)
from virtual_cdj.audio.worker import AnalysisWorker

#: Analyse einmal je Testlauf - librosa braucht beim ersten Aufruf lange.
_ANALYSIS_CACHE: dict[str, object] = {}


def shared_analysis():
    if "analysis" not in _ANALYSIS_CACHE:
        buffer, _ = decode_file(fixtures().wav)
        _ANALYSIS_CACHE["buffer"] = buffer
        _ANALYSIS_CACHE["analysis"] = TrackAnalyzer().analyse(buffer)
    return _ANALYSIS_CACHE["analysis"]


def shared_buffer() -> AudioBuffer:
    shared_analysis()
    return _ANALYSIS_CACHE["buffer"]  # type: ignore[return-value]


# --------------------------------------------------------------------------
# Format
# --------------------------------------------------------------------------


class FormatTests(unittest.TestCase):
    def test_stereo_float32_is_enforced(self) -> None:
        good = AudioBuffer(np.zeros((10, 2), dtype=np.float32), 44100)
        self.assertEqual(good.frames, 10)
        with self.assertRaises(AudioFormatError):
            AudioBuffer(np.zeros((10, 3), dtype=np.float32), 44100)
        with self.assertRaises(AudioFormatError):
            AudioBuffer(np.zeros((10, 2), dtype=np.float64), 44100)
        with self.assertRaises(AudioFormatError):
            AudioBuffer(np.zeros((10, 2), dtype=np.float32), 0)

    def test_mono_is_expanded_to_stereo(self) -> None:
        buffer = to_internal(np.array([0.5, -0.5], dtype=np.float32), 44100)
        self.assertEqual(buffer.samples.shape, (2, 2))
        np.testing.assert_allclose(
            buffer.samples[:, 0], buffer.samples[:, 1]
        )

    def test_integer_input_is_scaled(self) -> None:
        data = np.array([[32767, -32768]], dtype=np.int16)
        buffer = to_internal(data, 44100)
        self.assertAlmostEqual(float(buffer.samples[0, 0]), 1.0, places=4)
        self.assertAlmostEqual(float(buffer.samples[0, 1]), -1.0, places=4)

    def test_multichannel_is_folded_to_stereo(self) -> None:
        data = np.ones((4, 6), dtype=np.float32)
        buffer = to_internal(data, 48000)
        self.assertEqual(buffer.samples.shape, (4, 2))

    def test_mono_sum_and_duration(self) -> None:
        buffer = AudioBuffer(np.ones((44100, 2), dtype=np.float32), 44100)
        self.assertAlmostEqual(buffer.duration_s, 1.0)
        self.assertEqual(buffer.mono().shape, (44100,))
        self.assertAlmostEqual(buffer.peak, 1.0)


# --------------------------------------------------------------------------
# Dekoder
# --------------------------------------------------------------------------


class DecoderTests(unittest.TestCase):
    def test_wav_flac_mp3_all_supported(self) -> None:
        extensions = supported_extensions()
        for suffix in (".wav", ".flac", ".mp3", ".aiff", ".ogg"):
            self.assertIn(suffix, extensions)

    def test_metadata_without_decoding(self) -> None:
        decoder = open_decoder(fixtures().wav)
        metadata = decoder.load_metadata()
        self.assertEqual(metadata.sample_rate, SAMPLE_RATE)
        self.assertEqual(metadata.channels, 2)
        self.assertAlmostEqual(metadata.duration_s, 12.0, places=3)
        self.assertEqual(metadata.format, "WAV")

    def test_all_formats_decode_to_the_same_audio(self) -> None:
        reference, _ = decode_file(fixtures().wav)
        for path in fixtures().all_files():
            buffer, metadata = decode_file(path)
            self.assertEqual(buffer.samples.dtype, np.float32)
            self.assertEqual(buffer.samples.shape[1], 2)
            self.assertAlmostEqual(
                buffer.duration_s, reference.duration_s, places=2,
                msg=f"{path.name} hat andere Laenge",
            )
            # MP3 ist verlustbehaftet: der Kodierer verschiebt den
            # Spitzenpegel bei scharfen Transienten um einige Prozent. Der
            # Pegel muss in derselben Groessenordnung liegen, nicht gleich
            # sein.
            self.assertAlmostEqual(
                buffer.peak, reference.peak, delta=0.12,
                msg=f"{path.name} hat anderen Pegel",
            )

    def test_decode_region(self) -> None:
        decoder = open_decoder(fixtures().wav)
        region = decoder.decode_region(SAMPLE_RATE, SAMPLE_RATE)
        self.assertEqual(region.frames, SAMPLE_RATE)
        whole = decoder.decode()
        np.testing.assert_allclose(
            region.samples,
            whole.samples[SAMPLE_RATE:2 * SAMPLE_RATE],
            atol=1e-6,
        )

    def test_region_beyond_end_is_clamped(self) -> None:
        decoder = open_decoder(fixtures().wav)
        total = decoder.load_metadata().frames
        region = decoder.decode_region(total - 100, 1000)
        self.assertEqual(region.frames, 100)

    def test_unsupported_extension_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.xyz"
            path.write_bytes(b"nonsense")
            with self.assertRaises(UnsupportedFormat):
                open_decoder(path)

    def test_missing_file_is_reported(self) -> None:
        with self.assertRaises(DecodeError):
            SoundFileDecoder("gibt-es-nicht.wav")

    def test_additional_backend_can_be_registered(self) -> None:
        """Die Erweiterbarkeit fuer AAC/M4A muss ohne Aenderung gehen."""
        seen: list[Path] = []

        class DummyDecoder(SoundFileDecoder):
            def __init__(self, path: Path) -> None:
                seen.append(Path(path))
                super().__init__(fixtures().wav)

        register_backend({".dummy"}, DummyDecoder)
        self.assertIn(".dummy", supported_extensions())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.dummy"
            path.write_bytes(b"x")
            decoder = open_decoder(path)
            self.assertIsInstance(decoder, DummyDecoder)
        self.assertEqual(len(seen), 1)


class ResampleTests(unittest.TestCase):
    def test_same_rate_is_untouched(self) -> None:
        buffer = AudioBuffer(np.ones((100, 2), dtype=np.float32), 44100)
        result = resample(buffer, 44100)
        self.assertIs(result.samples, buffer.samples)

    def test_rate_change_scales_length(self) -> None:
        buffer = AudioBuffer(
            np.zeros((48000, 2), dtype=np.float32), 48000
        )
        result = resample(buffer, 44100)
        self.assertEqual(result.sample_rate, 44100)
        self.assertAlmostEqual(result.duration_s, 1.0, places=2)
        self.assertAlmostEqual(result.frames, 44100, delta=200)

    def test_sine_survives_resampling(self) -> None:
        rate = 48000
        time_axis = np.arange(rate) / rate
        sine = np.sin(2 * np.pi * 440 * time_axis).astype(np.float32)
        buffer = AudioBuffer(np.stack([sine, sine], axis=1), rate)
        result = resample(buffer, 44100)
        # Frequenz muss erhalten bleiben.
        spectrum = np.abs(np.fft.rfft(result.samples[:, 0]))
        peak_bin = int(np.argmax(spectrum))
        frequency = peak_bin * 44100 / result.frames
        self.assertAlmostEqual(frequency, 440.0, delta=5.0)


# --------------------------------------------------------------------------
# Waveform
# --------------------------------------------------------------------------


class WaveformTests(unittest.TestCase):
    def test_levels_have_expected_resolutions(self) -> None:
        analysis = shared_analysis()
        for name in ("overview", "medium", "detailed"):
            peaks = analysis.level(name)
            self.assertIsNotNone(peaks, name)
            self.assertTrue(peaks.is_consistent(), name)
            self.assertAlmostEqual(
                peaks.duration_s, analysis.duration_s, delta=0.1
            )

        self.assertLess(
            analysis.level("overview").peaks_per_second,
            analysis.level("medium").peaks_per_second,
        )
        self.assertLess(
            analysis.level("medium").peaks_per_second,
            analysis.level("detailed").peaks_per_second,
        )

    def test_peaks_are_normalised(self) -> None:
        analysis = shared_analysis()
        for peaks in analysis.waveform_levels.values():
            for band in (peaks.low, peaks.mid, peaks.high):
                self.assertGreaterEqual(float(band.min()), 0.0)
                self.assertLessEqual(float(band.max()), 1.0)

    def test_bands_separate_frequencies(self) -> None:
        """Ein Bassignal darf nur im Tiefband auftauchen."""
        rate = 44100
        time_axis = np.arange(rate) / rate
        bass = np.sin(2 * np.pi * 60 * time_axis).astype(np.float32)
        levels = compute_levels(bass, rate, {"detailed": 100.0})
        peaks = levels["detailed"]
        self.assertGreater(float(peaks.low.max()), 0.5)
        self.assertLess(float(peaks.mid.max()), 0.2)
        self.assertLess(float(peaks.high.max()), 0.2)

        treble = np.sin(2 * np.pi * 8000 * time_axis).astype(np.float32)
        peaks = compute_levels(treble, rate, {"detailed": 100.0})["detailed"]
        self.assertGreater(float(peaks.high.max()), 0.5)
        self.assertLess(float(peaks.low.max()), 0.2)

    def test_silence_gives_empty_peaks(self) -> None:
        levels = compute_levels(
            np.zeros(44100, dtype=np.float32), 44100, {"overview": 20.0}
        )
        self.assertEqual(float(levels["overview"].low.max()), 0.0)

    def test_level_selection_by_zoom(self) -> None:
        analysis = shared_analysis()
        detailed = analysis.level("detailed")
        overview = analysis.level("overview")
        # Starker Zoom braucht die feine Stufe.
        self.assertEqual(analysis.level_for(0.001), detailed)
        # Ganzer Track auf 1000 Pixel kommt mit der groben Stufe aus.
        self.assertEqual(analysis.level_for(1.0), overview)


# --------------------------------------------------------------------------
# Tempo / Beatgrid
# --------------------------------------------------------------------------


class TempoAnalysisTests(unittest.TestCase):
    def test_octave_folding(self) -> None:
        self.assertAlmostEqual(fold_tempo(62.0), 124.0)
        self.assertAlmostEqual(fold_tempo(248.0), 124.0)
        self.assertAlmostEqual(fold_tempo(124.0), 124.0)
        self.assertAlmostEqual(fold_tempo(31.0), 124.0)
        self.assertEqual(fold_tempo(0.0), 0.0)

    def test_beat_line_fit_is_exact_for_clean_input(self) -> None:
        interval = 0.5
        beats = np.arange(50) * interval + 0.25
        fitted_interval, intercept, residual = fit_beat_line(beats)
        self.assertAlmostEqual(fitted_interval, interval, places=9)
        self.assertAlmostEqual(intercept, 0.25, places=9)
        self.assertAlmostEqual(residual, 0.0, places=9)

    def test_beat_line_fit_averages_out_quantisation(self) -> None:
        interval = 60.0 / 124.0
        exact = np.arange(40) * interval
        quantised = np.round(exact / 0.0116) * 0.0116
        fitted, _, residual = fit_beat_line(quantised)
        self.assertAlmostEqual(60.0 / fitted, 124.0, delta=0.2)
        self.assertGreater(residual, 0.0)

    def test_detects_known_bpm(self) -> None:
        analysis = shared_analysis()
        self.assertAlmostEqual(
            analysis.bpm, KNOWN_BPM, delta=0.5,
            msg=f"erkannt {analysis.bpm}, erwartet {KNOWN_BPM}",
        )

    def test_beats_are_evenly_spaced(self) -> None:
        analysis = shared_analysis()
        beats = analysis.tempo.beats_s
        self.assertGreater(beats.size, 10)
        deltas = np.diff(beats)
        self.assertAlmostEqual(
            float(np.median(deltas)), 60.0 / KNOWN_BPM, delta=0.02
        )
        self.assertLess(analysis.tempo.beat_residual_s, 0.02)

    def test_first_beat_is_inside_the_first_interval(self) -> None:
        analysis = shared_analysis()
        interval = 60.0 / analysis.bpm
        self.assertGreaterEqual(analysis.tempo.first_beat_s, 0.0)
        self.assertLess(analysis.tempo.first_beat_s, interval)

    def test_downbeat_confidence_is_reported_not_assumed(self) -> None:
        analysis = shared_analysis()
        tempo = analysis.tempo
        self.assertGreaterEqual(tempo.downbeat_confidence, 0.0)
        # Ohne ausreichenden Kontrast darf kein Downbeat behauptet werden.
        if not tempo.downbeat_is_reliable:
            self.assertEqual(tempo.first_downbeat_index, 0)

    def test_short_input_gives_no_tempo(self) -> None:
        from virtual_cdj.audio.analysis.tempo import analyse_tempo

        result = analyse_tempo(np.zeros(100, dtype=np.float32), 22050)
        self.assertEqual(result.bpm, 0.0)
        self.assertEqual(result.beats_s.size, 0)

    def test_analysis_records_raw_value_for_diagnosis(self) -> None:
        analysis = shared_analysis()
        self.assertGreater(analysis.tempo.raw_bpm, 0.0)


class KeyAnalysisTests(unittest.TestCase):
    def test_key_is_reported_with_confidence(self) -> None:
        analysis = shared_analysis()
        self.assertGreaterEqual(analysis.key.confidence, 0.0)
        if analysis.key.camelot:
            self.assertRegex(analysis.key.camelot, r"^\d{1,2}[AB]$")
            self.assertTrue(analysis.key.name)

    def test_no_key_claimed_for_noise(self) -> None:
        import warnings

        from virtual_cdj.audio.analysis.key import analyse_key

        noise = np.random.default_rng(0).normal(
            0, 0.2, 22050 * 3
        ).astype(np.float32)
        with warnings.catch_warnings():
            # librosa warnt bei Rauschen ueber leere Frequenzmengen - hier
            # ist genau das der Testfall.
            warnings.simplefilter("ignore", UserWarning)
            result = analyse_key(noise, 22050)
        if not result.is_reliable:
            self.assertEqual(result.camelot, "")


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.cache = AnalysisCache(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_key_depends_on_size_mtime_and_version(self) -> None:
        key = CacheKey.for_file(fixtures().wav)
        self.assertEqual(key.analysis_version, ANALYSIS_VERSION)
        other_version = CacheKey.for_file(fixtures().wav, ANALYSIS_VERSION + 1)
        self.assertNotEqual(key.digest, other_version.digest)

        import dataclasses

        bigger = dataclasses.replace(key, size=key.size + 1)
        self.assertNotEqual(key.digest, bigger.digest)
        newer = dataclasses.replace(key, mtime_ns=key.mtime_ns + 1)
        self.assertNotEqual(key.digest, newer.digest)

    def test_miss_then_store_then_hit(self) -> None:
        key = CacheKey.for_file(fixtures().wav)
        self.assertIsNone(self.cache.load(key))
        self.assertEqual(self.cache.stats.misses, 1)

        analysis = shared_analysis()
        path = self.cache.store(key, analysis)
        self.assertTrue(path.exists())
        self.assertEqual(self.cache.stats.writes, 1)
        # Keine uebrig gebliebene Temp-Datei.
        self.assertEqual(
            list(Path(self._temp.name).glob("*.tmp*")), []
        )

        loaded = self.cache.load(key)
        self.assertIsNotNone(loaded)
        self.assertEqual(self.cache.stats.hits, 1)
        self.assertEqual(self.cache.stats.errors, 0)

    def test_round_trip_preserves_analysis(self) -> None:
        key = CacheKey.for_file(fixtures().wav)
        original = shared_analysis()
        self.cache.store(key, original)
        loaded = self.cache.load(key)
        assert loaded is not None

        self.assertAlmostEqual(loaded.bpm, original.bpm, places=6)
        self.assertAlmostEqual(
            loaded.duration_s, original.duration_s, places=6
        )
        self.assertEqual(loaded.sample_rate, original.sample_rate)
        self.assertEqual(
            loaded.tempo.first_downbeat_index,
            original.tempo.first_downbeat_index,
        )
        self.assertAlmostEqual(
            loaded.tempo.first_beat_s, original.tempo.first_beat_s, places=6
        )
        self.assertEqual(loaded.key.camelot, original.key.camelot)
        np.testing.assert_allclose(
            loaded.tempo.beats_s, original.tempo.beats_s
        )
        self.assertEqual(
            set(loaded.waveform_levels), set(original.waveform_levels)
        )
        for name, peaks in original.waveform_levels.items():
            restored = loaded.waveform_levels[name]
            self.assertAlmostEqual(
                restored.peaks_per_second, peaks.peaks_per_second, places=6
            )
            # uint8-Quantisierung: 1/255 Toleranz.
            np.testing.assert_allclose(
                restored.low, peaks.low, atol=1.0 / 255
            )
            np.testing.assert_allclose(
                restored.high, peaks.high, atol=1.0 / 255
            )

    def test_version_change_forces_reanalysis(self) -> None:
        key = CacheKey.for_file(fixtures().wav)
        self.cache.store(key, shared_analysis())
        newer = CacheKey.for_file(fixtures().wav, ANALYSIS_VERSION + 1)
        self.assertIsNone(self.cache.load(newer))

    def test_corrupt_entry_is_discarded(self) -> None:
        key = CacheKey.for_file(fixtures().wav)
        target = self.cache.path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"kein npz")
        self.assertIsNone(self.cache.load(key))
        self.assertEqual(self.cache.stats.errors, 1)
        self.assertFalse(target.exists())

    def test_clear_and_size(self) -> None:
        key = CacheKey.for_file(fixtures().wav)
        self.cache.store(key, shared_analysis())
        self.assertGreater(self.cache.size_bytes(), 0)
        self.assertEqual(self.cache.clear(), 1)
        self.assertEqual(self.cache.size_bytes(), 0)


class LoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.loader = TrackLoader(cache=AnalysisCache(self._temp.name))

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_load_produces_real_track_info(self) -> None:
        loaded = self.loader.load(fixtures().wav)
        info = loaded.info
        self.assertFalse(loaded.from_cache)
        self.assertAlmostEqual(info.duration_s, 12.0, places=2)
        self.assertAlmostEqual(info.original_bpm, KNOWN_BPM, delta=0.5)
        self.assertTrue(info.title)
        self.assertIsNotNone(info.waveform)
        self.assertTrue(info.waveform.is_consistent())
        self.assertIsNotNone(info.beat_grid)
        self.assertTrue(info.beat_grid.is_valid)
        self.assertEqual(info.analysis_version, ANALYSIS_VERSION)
        self.assertEqual(loaded.buffer.sample_rate, ENGINE_SAMPLE_RATE)

    def test_second_load_uses_cache(self) -> None:
        first = self.loader.load(fixtures().wav)
        second = self.loader.load(fixtures().wav)
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertAlmostEqual(
            first.info.original_bpm, second.info.original_bpm, places=6
        )
        self.assertEqual(self.loader.metrics.cached, 1)
        self.assertEqual(self.loader.metrics.analysed, 1)

    def test_beat_grid_uses_constant_tempo_from_fit(self) -> None:
        info = self.loader.load(fixtures().wav).info
        grid = info.beat_grid
        assert grid is not None
        # Gleichmaessiges Raster, keine gequantelte Liste.
        self.assertEqual(grid.beats_s, ())
        self.assertGreater(grid.bpm, 0)

    def test_track_id_is_stable_for_the_same_file(self) -> None:
        a = self.loader.load(fixtures().wav).info.track_id
        b = self.loader.load(fixtures().wav).info.track_id
        self.assertEqual(a, b)
        c = self.loader.load(fixtures().flac).info.track_id
        self.assertNotEqual(a, c)


class WorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.worker = AnalysisWorker(
            TrackLoader(cache=AnalysisCache(self._temp.name))
        )

    def tearDown(self) -> None:
        self.worker.stop()
        self._temp.cleanup()

    def test_loads_in_background_without_blocking(self) -> None:
        import time

        self.worker.start()
        self.worker.request(1, fixtures().wav)
        deadline = time.monotonic() + 60.0
        result = None
        while result is None and time.monotonic() < deadline:
            result = self.worker.poll()
            time.sleep(0.02)
        self.assertIsNotNone(result, "Worker hat nicht geantwortet")
        self.assertTrue(result.ok)
        self.assertEqual(result.request.deck_id, 1)
        self.assertAlmostEqual(
            result.track.info.original_bpm, KNOWN_BPM, delta=0.5
        )

    def test_error_is_reported_not_raised(self) -> None:
        result = self.worker.load_now(1, "gibt-es-nicht.wav")
        self.assertFalse(result.ok)
        self.assertIn("DecodeError", result.error)
        self.assertTrue(result.traceback_text)
        self.assertEqual(self.worker.loader.metrics.failed, 1)


# --------------------------------------------------------------------------
# Tempo-Prozessor
# --------------------------------------------------------------------------


class TempoProcessorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.processor = SimpleResamplerTempoProcessor(max_frames=1024)
        self.source = ramp_track(2.0)

    def render(self, position: float, frames: int, speed: float):
        out = np.zeros((frames, 2), dtype=np.float32)
        new_position = self.processor.render(
            self.source, position, frames, speed, out
        )
        return out, new_position

    def test_normal_speed_copies_samples(self) -> None:
        out, position = self.render(0.0, 64, 1.0)
        self.assertAlmostEqual(position, 64.0)
        np.testing.assert_allclose(
            out[:, 0], self.source[:64, 0], atol=1e-6
        )

    def test_double_speed_advances_twice(self) -> None:
        out, position = self.render(0.0, 64, 2.0)
        self.assertAlmostEqual(position, 128.0)
        np.testing.assert_allclose(
            out[:, 0], self.source[0:128:2, 0], atol=1e-6
        )

    def test_half_speed_interpolates(self) -> None:
        out, position = self.render(0.0, 64, 0.5)
        self.assertAlmostEqual(position, 32.0)
        # Zwischenwerte muessen zwischen den Nachbarn liegen.
        self.assertTrue(np.all(np.diff(out[:, 0]) > 0))

    def test_reverse_speed(self) -> None:
        out, position = self.render(1000.0, 64, -1.0)
        self.assertAlmostEqual(position, 936.0)
        self.assertTrue(np.all(np.diff(out[:, 0]) < 0))

    def test_fractional_position_is_interpolated(self) -> None:
        out, _ = self.render(10.5, 1, 1.0)
        expected = (self.source[10, 0] + self.source[11, 0]) / 2
        self.assertAlmostEqual(float(out[0, 0]), float(expected), places=6)

    def test_empty_source_gives_silence(self) -> None:
        out = np.ones((8, 2), dtype=np.float32)
        position = self.processor.render(
            np.zeros((0, 2), dtype=np.float32), 0.0, 8, 1.0, out
        )
        self.assertEqual(position, 0.0)
        self.assertEqual(float(np.abs(out).max()), 0.0)

    def test_larger_block_grows_buffers(self) -> None:
        out, _ = self.render(0.0, 2048, 1.0)
        self.assertEqual(out.shape, (2048, 2))

    def test_key_lock_is_declared_but_not_implemented(self) -> None:
        processor = KeyLockTempoProcessor()
        self.assertFalse(processor.changes_pitch)
        with self.assertRaises(NotImplementedError):
            processor.render(
                self.source, 0.0, 8, 1.0,
                np.zeros((8, 2), dtype=np.float32),
            )


# --------------------------------------------------------------------------
# Stimme und Engine
# --------------------------------------------------------------------------


class VoiceTests(unittest.TestCase):
    """Position wird am Rampensignal exakt abgelesen."""

    def setUp(self) -> None:
        self.source = ramp_track(10.0)
        self.voice = DeckVoice(1, SAMPLE_RATE)
        self.voice.load(self.source)
        self.out = np.zeros((512, 2), dtype=np.float32)

    def render(self, blocks: int = 1) -> np.ndarray:
        collected = []
        for _ in range(blocks):
            self.out[:] = 0.0
            self.voice.render(self.out, 512)
            collected.append(self.out[:, 0].copy())
        return np.concatenate(collected)

    def test_ready_immediately_after_load(self) -> None:
        self.assertTrue(self.voice.is_ready)
        self.assertAlmostEqual(self.voice.duration_seconds, 10.0, places=3)
        self.assertGreater(self.voice.loaded_bytes, 0)

    def test_silence_while_stopped(self) -> None:
        data = self.render(2)
        self.assertEqual(float(np.abs(data).max()), 0.0)

    def test_playing_produces_track_audio(self) -> None:
        self.voice.set_playing(True)
        data = self.render(4)
        # Rampensignal: Wert == Zeit. Muss bei 0 beginnen und steigen.
        self.assertAlmostEqual(float(data[0]), 0.0, places=5)
        self.assertTrue(np.all(np.diff(data) > 0))
        expected = 4 * 512 / SAMPLE_RATE
        self.assertAlmostEqual(
            self.voice.position_seconds, expected, places=5
        )

    def test_seek_moves_position_exactly(self) -> None:
        self.voice.seek_seconds(3.5)
        self.voice.set_playing(True)
        data = self.render(1)
        self.assertAlmostEqual(
            ramp_seconds(data[0]), 3.5, places=3
        )
        self.assertAlmostEqual(self.voice.position_seconds, 3.5 + 512 / SAMPLE_RATE, places=5)

    def test_nudge_is_relative(self) -> None:
        self.voice.seek_seconds(2.0)
        self.voice.nudge_seconds(0.25)
        self.voice.set_playing(True)
        data = self.render(1)
        self.assertAlmostEqual(
            ramp_seconds(data[0]), 2.25, places=3
        )

    def test_speed_changes_advance_rate(self) -> None:
        self.voice.set_playing(True)
        self.voice.set_speed(2.0)
        self.render(2)
        self.assertAlmostEqual(
            self.voice.position_seconds, 2 * 2 * 512 / SAMPLE_RATE, places=5
        )

    def test_reverse_playback(self) -> None:
        self.voice.seek_seconds(5.0)
        self.voice.set_speed(-1.0)
        self.voice.set_playing(True)
        data = self.render(2)
        self.assertTrue(np.all(np.diff(data) < 0))
        self.assertLess(self.voice.position_seconds, 5.0)

    def test_stops_at_end_of_track(self) -> None:
        self.voice.seek_seconds(9.99)
        self.voice.set_playing(True)
        self.render(20)
        self.assertTrue(self.voice.reached_end)
        self.assertAlmostEqual(
            self.voice.position_seconds, 10.0, delta=0.01
        )

    def test_zero_speed_is_silent(self) -> None:
        self.voice.set_playing(True)
        self.voice.set_speed(0.0)
        data = self.render(2)
        self.assertEqual(float(np.abs(data).max()), 0.0)

    def test_gain_is_applied(self) -> None:
        self.voice.seek_seconds(1.0)
        self.voice.set_gain(0.5)
        self.voice.set_playing(True)
        data = self.render(1)
        # Rampenwert bei 1.0 s ist 0.1, mit Gain 0.5 also 0.05.
        self.assertAlmostEqual(
            float(data[0]), ramp_value(1.0) * 0.5, places=5
        )


class VoiceLoopTests(unittest.TestCase):
    """Loops muessen sample-genau schliessen, nicht per GUI-Tick."""

    def setUp(self) -> None:
        self.source = ramp_track(10.0)

    def voice_with_loop(
        self, start: float, end: float, speed: float = 1.0
    ) -> DeckVoice:
        voice = DeckVoice(1, SAMPLE_RATE)
        voice.load(self.source)
        voice.seek_seconds(start)
        voice.set_speed(speed)
        voice.set_loop(True, start, end)
        voice.set_playing(True)
        return voice

    def collect(self, voice: DeckVoice, blocks: int) -> np.ndarray:
        """Ausgangssignal in Sekunden - das Rampensignal ist normiert."""
        out = np.zeros((512, 2), dtype=np.float32)
        parts = []
        for _ in range(blocks):
            out[:] = 0.0
            voice.render(out, 512)
            parts.append(out[:, 0].copy())
        return np.concatenate(parts) * RAMP_DURATION_S

    def test_all_beat_loop_lengths_stay_inside(self) -> None:
        bpm = 124.0
        beat = 60.0 / bpm
        for beats in (0.25, 0.5, 1, 2, 4, 8, 16, 32):
            start = 1.0
            end = start + beats * beat
            if end > 9.5:
                continue
            voice = self.voice_with_loop(start, end)
            data = self.collect(voice, 300)
            outside = np.count_nonzero(
                (data < start - 1e-6) | (data > end + 1e-6)
            )
            self.assertEqual(
                outside, 0, f"{beats} Beats: {outside} Samples ausserhalb"
            )
            self.assertTrue(
                start <= voice.position_seconds < end,
                f"{beats} Beats: Position {voice.position_seconds}",
            )

    def test_no_drift_over_many_wraps(self) -> None:
        start, end = 1.0, 1.5
        voice = self.voice_with_loop(start, end)
        data = self.collect(voice, 2000)
        self.assertLessEqual(float(data.max()), end + 1e-6)
        self.assertGreaterEqual(float(data.min()), start - 1e-6)
        expected_wraps = (2000 * 512 / SAMPLE_RATE) / (end - start)
        self.assertAlmostEqual(voice.loop_wraps, expected_wraps, delta=2)

    def test_wrap_carries_the_overshoot(self) -> None:
        """Nach dem Umlauf darf kein Sample verloren gehen."""
        start, end = 1.0, 1.0 + 700 / SAMPLE_RATE  # kuerzer als ein Block
        voice = self.voice_with_loop(start, end)
        out = np.zeros((512, 2), dtype=np.float32)
        voice.render(out, 512)
        voice.render(out, 512)
        # 1024 Samples bei Looplaenge 700 -> Position exakt vorhersagbar.
        expected = start + (1024 % 700) / SAMPLE_RATE
        self.assertAlmostEqual(
            voice.position_seconds, expected, places=6
        )

    def test_reverse_loop_wraps_at_start(self) -> None:
        start, end = 2.0, 3.0
        voice = DeckVoice(1, SAMPLE_RATE)
        voice.load(self.source)
        voice.seek_seconds(2.1)
        voice.set_speed(-1.0)
        voice.set_loop(True, start, end)
        voice.set_playing(True)
        data = self.collect(voice, 100)
        self.assertGreaterEqual(float(data.min()), start - 1e-6)
        self.assertLessEqual(float(data.max()), end + 1e-6)
        self.assertGreater(voice.loop_wraps, 0)

    def test_loop_can_be_released(self) -> None:
        voice = self.voice_with_loop(1.0, 1.5)
        self.collect(voice, 100)
        voice.set_loop(False, None, None)
        data = self.collect(voice, 200)
        self.assertGreater(float(data.max()), 1.5)

    def test_too_short_loop_is_stretched_not_dropped(self) -> None:
        """Die Ausgabe schaltet einen Loop nie von sich aus ab.

        Frueher wurde ein Loop unterhalb von ``MIN_LOOP_FRAMES``
        stillschweigend verworfen. ``DeckState.loop.active`` blieb dabei
        ``True``: die Anzeige zeigte einen Loop, der Ton lief hindurch -
        das sah aus wie ein Loop, der von selbst aufhoert. Jetzt wird die
        Laenge auf das Minimum gestreckt und der Loop laeuft.
        """
        from virtual_cdj.audio.engine import MIN_LOOP_FRAMES

        voice = DeckVoice(1, SAMPLE_RATE)
        voice.load(self.source)
        voice.seek_seconds(1.0)
        voice.set_loop(True, 1.0, 1.0 + 4 / SAMPLE_RATE)
        voice.set_playing(True)
        self.collect(voice, 50)

        self.assertTrue(voice._loop_active)  # noqa: SLF001
        self.assertGreater(voice.loop_wraps, 0)
        limit = 1.0 + MIN_LOOP_FRAMES / SAMPLE_RATE
        self.assertGreaterEqual(voice.position_frames / SAMPLE_RATE, 1.0)
        self.assertLessEqual(voice.position_frames / SAMPLE_RATE, limit)


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = AudioEngine(block_frames=256)

    def tearDown(self) -> None:
        self.engine.close()

    def test_voices_are_created_per_deck(self) -> None:
        first = self.engine.voice(1)
        self.assertIs(self.engine.voice(1), first)
        self.assertIsNot(self.engine.voice(2), first)
        self.assertEqual(set(self.engine.voices), {1, 2})

    def test_render_block_sums_voices(self) -> None:
        constant = np.full((SAMPLE_RATE, 2), 0.25, dtype=np.float32)
        for deck_id in (1, 2, 3):
            voice = self.engine.voice(deck_id)
            voice.load(constant)
            voice.set_playing(True)
        out = self.engine.render_block(256)
        self.assertAlmostEqual(float(out[0, 0]), 0.75, places=5)
        self.assertEqual(self.engine.metrics.active_voices, 3)

    def test_output_is_clipped(self) -> None:
        loud = np.full((SAMPLE_RATE, 2), 0.9, dtype=np.float32)
        for deck_id in (1, 2, 3, 4):
            voice = self.engine.voice(deck_id)
            voice.load(loud)
            voice.set_playing(True)
        out = self.engine.render_block(256)
        self.assertLessEqual(float(out.max()), 1.0)

    def test_four_decks_render_without_error(self) -> None:
        source = ramp_track(5.0)
        for deck_id in (1, 2, 3, 4):
            voice = self.engine.voice(deck_id)
            voice.load(source)
            voice.seek_seconds(deck_id * 0.5)
            voice.set_playing(True)
        for _ in range(200):
            self.engine.render_block(256)
        positions = [
            self.engine.voice(deck_id).position_seconds
            for deck_id in (1, 2, 3, 4)
        ]
        # Jedes Deck haelt seine eigene Position.
        self.assertEqual(len(set(round(p, 4) for p in positions)), 4)
        self.assertEqual(self.engine.metrics.active_voices, 4)

    def test_metrics_are_measured(self) -> None:
        voice = self.engine.voice(1)
        voice.load(ramp_track(2.0))
        voice.set_playing(True)
        for _ in range(50):
            self.engine.render_block(256)
        metrics = self.engine.metrics
        self.assertEqual(metrics.callbacks, 50)
        self.assertGreater(metrics.average_callback_ms, 0.0)
        self.assertGreater(metrics.peak_callback_ms, 0.0)
        self.assertGreater(metrics.load, 0.0)
        self.assertLess(
            metrics.load, 1.0, "Callback braucht mehr Zeit als verfuegbar"
        )
        self.assertIn("underruns", metrics.summary())

    def test_memory_is_reported(self) -> None:
        self.engine.voice(1).load(ramp_track(10.0))
        megabytes = self.engine.memory_mb()
        expected = 10 * SAMPLE_RATE * 2 * 4 / (1024 * 1024)
        self.assertAlmostEqual(megabytes, expected, delta=0.5)

    def test_no_allocation_growth_in_steady_state(self) -> None:
        """Ein Dauerlauf darf den Speicher nicht wachsen lassen."""
        import gc

        voice = self.engine.voice(1)
        voice.load(ramp_track(5.0))
        voice.set_playing(True)
        out = np.zeros((256, 2), dtype=np.float32)
        for _ in range(50):
            self.engine.render_block(256, out)
        gc.collect()
        before = len(gc.get_objects())
        for _ in range(200):
            self.engine.render_block(256, out)
        gc.collect()
        after = len(gc.get_objects())
        self.assertLess(
            after - before, 200,
            f"Objektzahl waechst um {after - before} in 200 Bloecken",
        )


if __name__ == "__main__":
    unittest.main()
