"""Waveform-Darstellung: Analyse, Amplitude, Farbe, Kanten, Tempo.

Geprueft wird die Kette
``WaveformAnalyzer -> WaveformCache -> WaveformRenderer``:

* die Analyse legt Baender **und** Dynamik ab
* die Balkenhoehe kommt aus Effektiv- und Spitzenwert, logarithmisch gestaucht
* die Farbe kommt aus dem *Verhaeltnis* der Baender, nicht aus
  ``R=Bass, G=Mitten, B=Hoehen``
* die Balkenkanten sind weich
* der Zoom ist musikalisch
"""

from __future__ import annotations

import time
import unittest

import numpy as np

from virtual_cdj.audio.analysis import waveform as waveform_analysis
from virtual_cdj.cdj_ui.waveform_render import (
    BACKGROUND,
    PEAK_WEIGHT,
    RMS_WEIGHT,
    WaveformRenderer,
    compress,
    spectral_color,
)
from virtual_cdj.deck.display_state import WaveformMode
from virtual_cdj.deck.state import WaveformData

SAMPLE_RATE = 44100


def tone(frequency: float, seconds: float, amplitude: float = 0.8):
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


def level_from(
    low, mid, high, peak=None, rms=None, pps: float = 10.0
) -> WaveformData:
    return WaveformData(
        peaks_per_second=pps,
        low=np.asarray(low, dtype=np.float32),
        mid=np.asarray(mid, dtype=np.float32),
        high=np.asarray(high, dtype=np.float32),
        peak=np.asarray(peak, dtype=np.float32) if peak is not None else (),
        rms=np.asarray(rms, dtype=np.float32) if rms is not None else (),
    )


# --------------------------------------------------------------------------
# Analyse
# --------------------------------------------------------------------------


class AnalysisTests(unittest.TestCase):
    def test_bands_separate_bass_from_treble(self) -> None:
        bass = waveform_analysis.compute_levels(
            tone(60.0, 1.0), SAMPLE_RATE
        )["medium"]
        treble = waveform_analysis.compute_levels(
            tone(9000.0, 1.0), SAMPLE_RATE
        )["medium"]
        self.assertGreater(float(bass.low.mean()), float(bass.high.mean()) * 5)
        self.assertGreater(
            float(treble.high.mean()), float(treble.low.mean()) * 5
        )

    def test_band_edges_follow_the_specification(self) -> None:
        self.assertAlmostEqual(waveform_analysis.BAND_LOW_HZ, 250.0)
        self.assertAlmostEqual(waveform_analysis.BAND_HIGH_HZ, 2500.0)

    def test_dynamics_are_stored_next_to_the_bands(self) -> None:
        level = waveform_analysis.compute_levels(
            tone(200.0, 2.0), SAMPLE_RATE
        )["medium"]
        self.assertTrue(level.has_dynamics)
        self.assertEqual(level.peak.shape, level.low.shape)
        self.assertEqual(level.rms.shape, level.low.shape)
        self.assertTrue(np.all(level.peak <= 1.0 + 1e-6))
        self.assertTrue(np.all(level.rms <= level.peak + 1e-6))
        # Ein Sinus hat rund 0.707 des Spitzenwerts als Effektivwert.
        ratio = float(level.rms.mean() / max(level.peak.mean(), 1e-9))
        self.assertAlmostEqual(ratio, 0.707, delta=0.05)

    def test_silence_stays_silent(self) -> None:
        level = waveform_analysis.compute_levels(
            np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE
        )["medium"]
        self.assertEqual(float(level.peak.max()), 0.0)
        self.assertEqual(float(level.rms.max()), 0.0)

    def test_analysis_version_was_raised(self) -> None:
        """Neue Bandgrenzen und Dynamik machen alte Caches ungueltig."""
        from virtual_cdj.audio.analysis.analyzer import ANALYSIS_VERSION

        self.assertGreaterEqual(ANALYSIS_VERSION, 4)


class CacheRoundTripTests(unittest.TestCase):
    def test_dynamics_survive_the_cache(self) -> None:
        import tempfile
        from pathlib import Path

        from virtual_cdj.audio.analysis.analyzer import TrackAnalyzer
        from virtual_cdj.audio.cache import AnalysisCache, CacheKey
        from virtual_cdj.audio.format import AudioBuffer

        signal = (tone(80.0, 3.0) + tone(6000.0, 3.0) * 0.4).astype(np.float32)
        stereo = np.stack([signal, signal], axis=1)
        analysis = TrackAnalyzer().analyse(AudioBuffer(stereo, SAMPLE_RATE))

        with tempfile.TemporaryDirectory() as directory:
            cache = AnalysisCache(Path(directory))
            key = CacheKey(path="t.wav", size=1, mtime_ns=1)
            cache.store(key, analysis)
            restored = cache.load(key)

        self.assertIsNotNone(restored)
        before = analysis.waveform_levels["medium"]
        after = restored.waveform_levels["medium"]
        self.assertTrue(after.has_dynamics)
        # uint8-Ablage: rund 1/255 Genauigkeit.
        np.testing.assert_allclose(before.rms, after.rms, atol=0.01)
        np.testing.assert_allclose(before.peak, after.peak, atol=0.01)


# --------------------------------------------------------------------------
# Amplitude
# --------------------------------------------------------------------------


class AmplitudeTests(unittest.TestCase):
    def test_compression_is_monotonic_and_bounded(self) -> None:
        values = np.linspace(0.0, 1.0, 50)
        out = compress(values)
        self.assertAlmostEqual(float(out[0]), 0.0, places=9)
        self.assertAlmostEqual(float(out[-1]), 1.0, places=9)
        self.assertTrue(np.all(np.diff(out) > 0))

    def test_compression_lifts_quiet_passages(self) -> None:
        """Leises darf nicht verschwinden, Lautes nicht dauernd anliegen."""
        self.assertGreater(float(compress(np.array([0.1]))[0]), 0.25)
        self.assertLess(float(compress(np.array([0.1]))[0]), 0.5)

    def test_amplitude_mixes_rms_and_peak(self) -> None:
        columns = {
            "low": np.array([0.0], np.float32),
            "mid": np.array([0.0], np.float32),
            "high": np.array([0.0], np.float32),
            "rms": np.array([0.4], np.float32),
            "peak": np.array([0.9], np.float32),
        }
        expected = compress(
            np.array([RMS_WEIGHT * 0.4 + PEAK_WEIGHT * 0.9], np.float32)
        )
        got = WaveformRenderer.amplitude_of(columns)
        self.assertAlmostEqual(float(got[0]), float(expected[0]), places=6)

    def test_amplitude_falls_back_to_the_loudest_band(self) -> None:
        """Ohne Dynamikwerte wird nichts erfunden."""
        columns = {
            "low": np.array([0.3], np.float32),
            "mid": np.array([0.7], np.float32),
            "high": np.array([0.2], np.float32),
            "rms": np.zeros(1, np.float32),
            "peak": np.zeros(1, np.float32),
        }
        got = WaveformRenderer.amplitude_of(columns)
        self.assertAlmostEqual(
            float(got[0]), float(compress(np.array([0.7]))[0]), places=6
        )

    def test_transients_stay_visible(self) -> None:
        """Ein einzelner Kick darf nicht im Effektivwert untergehen."""
        quiet = WaveformRenderer.amplitude_of({
            "low": np.zeros(1, np.float32), "mid": np.zeros(1, np.float32),
            "high": np.zeros(1, np.float32),
            "rms": np.array([0.2], np.float32),
            "peak": np.array([0.25], np.float32),
        })
        kick = WaveformRenderer.amplitude_of({
            "low": np.zeros(1, np.float32), "mid": np.zeros(1, np.float32),
            "high": np.zeros(1, np.float32),
            "rms": np.array([0.2], np.float32),
            "peak": np.array([1.0], np.float32),
        })
        self.assertGreater(float(kick[0]), float(quiet[0]) * 1.3)


# --------------------------------------------------------------------------
# Farbe
# --------------------------------------------------------------------------


class ColorTests(unittest.TestCase):
    def colour(self, low: float, mid: float, high: float):
        rgb = spectral_color(
            np.array([low], np.float32),
            np.array([mid], np.float32),
            np.array([high], np.float32),
        )[0]
        return float(rgb[0]), float(rgb[1]), float(rgb[2])

    def test_bass_is_warm(self) -> None:
        r, g, b = self.colour(1.0, 0.05, 0.02)
        self.assertGreater(r, g)
        self.assertGreater(r, b)
        self.assertGreater(r, 0.8)

    def test_treble_is_blue(self) -> None:
        r, g, b = self.colour(0.02, 0.05, 1.0)
        self.assertGreater(b, r)
        self.assertGreater(b, 0.7)

    def test_mids_are_green_turquoise_not_pure_green(self) -> None:
        """Kein ``G=Mitten``: die Mitten haben einen Blauanteil."""
        r, g, b = self.colour(0.05, 1.0, 0.05)
        self.assertGreater(g, r)
        self.assertGreater(g, b)
        self.assertGreater(b, 0.15)

    def test_broadband_goes_towards_white(self) -> None:
        r, g, b = self.colour(1.0, 1.0, 1.0)
        for channel in (r, g, b):
            self.assertGreater(channel, 0.6)
        self.assertLess(max(r, g, b) - min(r, g, b), 0.35)

    def test_colour_is_not_the_naive_rgb_mapping(self) -> None:
        """Eine direkte Zuordnung waere hier eindeutig erkennbar."""
        r, g, b = self.colour(0.5, 0.0, 0.0)
        self.assertNotAlmostEqual(g, 0.0, places=3)

    def test_colour_ignores_loudness(self) -> None:
        """Dieselbe Mischung, halbe Lautstaerke: gleiche Farbe."""
        loud = self.colour(0.8, 0.4, 0.2)
        quiet = self.colour(0.4, 0.2, 0.1)
        for a, b in zip(loud, quiet):
            self.assertAlmostEqual(a, b, places=5)


# --------------------------------------------------------------------------
# Zeichnen
# --------------------------------------------------------------------------


class DrawingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = WaveformRenderer()

    def render(self, level, width=40, height=41, mode=WaveformMode.RGB):
        return self.renderer.render_array(
            level, 0.0, level.length / level.peaks_per_second,
            width, height, mode,
        ).copy()

    def test_silence_draws_only_background(self) -> None:
        level = level_from([0, 0, 0], [0, 0, 0], [0, 0, 0],
                           peak=[0, 0, 0], rms=[0, 0, 0])
        image = self.render(level)
        self.assertTrue(np.all(image == np.array(BACKGROUND, np.uint8)))

    def test_bars_grow_from_the_centre_line(self) -> None:
        level = level_from([1, 1], [0, 0], [0, 0], peak=[1, 1], rms=[1, 1])
        image = self.render(level, width=10, height=41)
        centre = 20
        column = image[:, 5, :]
        background = np.array(BACKGROUND, np.uint8)
        self.assertFalse(np.array_equal(column[centre], background))
        # Symmetrisch um die Mittellinie.
        for offset in (5, 10, 15):
            self.assertEqual(
                bool(np.array_equal(column[centre - offset], background)),
                bool(np.array_equal(column[centre + offset], background)),
            )

    def test_louder_columns_are_taller(self) -> None:
        level = level_from(
            [0.2, 0.6, 1.0], [0, 0, 0], [0, 0, 0],
            peak=[0.2, 0.6, 1.0], rms=[0.2, 0.6, 1.0],
        )
        image = self.render(level, width=3, height=61)
        background = np.array(BACKGROUND, np.uint8)

        def bar_height(column: int) -> int:
            pixels = image[:, column, :]
            return int(np.sum(~np.all(pixels == background, axis=1)))

        heights = [bar_height(i) for i in range(3)]
        self.assertLess(heights[0], heights[1])
        self.assertLess(heights[1], heights[2])

    def test_edges_are_antialiased(self) -> None:
        """Am Balkenrand liegt mindestens ein Zwischenwert."""
        level = level_from(
            [0.53], [0.0], [0.0], peak=[0.53], rms=[0.53]
        )
        image = self.render(level, width=4, height=81)
        column = image[:, 2, :].astype(int)
        background = np.array(BACKGROUND, int)
        interior = column.max(axis=1).max()
        partial = [
            row for row in column
            if not np.array_equal(row, background)
            and row.max() < interior - 8
        ]
        self.assertTrue(partial, "keine weiche Kante gefunden")

    def test_three_band_shows_all_three_colours(self) -> None:
        level = level_from(
            [0.9], [0.5], [0.25], peak=[0.9], rms=[0.7]
        )
        image = self.render(
            level, width=6, height=61, mode=WaveformMode.THREE_BAND
        )
        colours = {tuple(px) for px in image.reshape(-1, 3)}
        self.assertGreaterEqual(len(colours - {BACKGROUND}), 3)

    def test_blue_mode_is_single_coloured(self) -> None:
        level = level_from(
            [0.9, 0.1], [0.2, 0.8], [0.1, 0.9],
            peak=[0.9, 0.9], rms=[0.7, 0.7],
        )
        image = self.render(level, width=8, height=41, mode=WaveformMode.BLUE)
        pixels = image.reshape(-1, 3)
        drawn = pixels[~np.all(pixels == np.array(BACKGROUND, np.uint8), axis=1)]
        # Blau ist in jedem gezeichneten Pixel der stärkste Kanal.
        self.assertTrue(np.all(drawn[:, 2] >= drawn[:, 0]))

    def test_area_outside_the_track_stays_empty(self) -> None:
        level = level_from([1, 1], [0, 0], [0, 0], peak=[1, 1], rms=[1, 1])
        image = self.renderer.render_array(
            level, -1.0, 0.0, 20, 41, WaveformMode.RGB
        )
        self.assertTrue(np.all(image == np.array(BACKGROUND, np.uint8)))

    def test_columns_aggregate_energy_correctly(self) -> None:
        """Effektivwert wird energierichtig zusammengefasst, Peak als Maximum."""
        level = level_from(
            [0.0] * 4, [0.0] * 4, [0.0] * 4,
            peak=[0.1, 0.9, 0.2, 0.3], rms=[0.4, 0.4, 0.4, 0.4], pps=4.0,
        )
        columns = WaveformRenderer._columns(level, 0.0, 1.0, 1)
        self.assertAlmostEqual(float(columns["peak"][0]), 0.9, places=5)
        self.assertAlmostEqual(float(columns["rms"][0]), 0.4, places=5)


class EndToEndTests(unittest.TestCase):
    """Vom Audiosignal bis zur Farbe - mit echter Analyse, ohne Demo-Daten."""

    @staticmethod
    def techno_bar(bpm: float = 150.0) -> np.ndarray:
        """Ein Takt mit Kick auf jedem Beat und Hi-Hat auf den Achteln."""
        beat = 60.0 / bpm
        count = int(SAMPLE_RATE * beat * 4)
        signal = np.zeros(count, dtype=np.float32)
        for index in range(4):
            start = int(index * beat * SAMPLE_RATE)
            length = int(0.12 * SAMPLE_RATE)
            envelope = np.exp(-np.arange(length) / (0.035 * SAMPLE_RATE))
            sweep = np.linspace(110.0, 45.0, length)
            signal[start:start + length] += (
                0.95 * envelope
                * np.sin(2 * np.pi * np.cumsum(sweep) / SAMPLE_RATE)
            ).astype(np.float32)
        for index in range(8):
            start = int(index * beat / 2 * SAMPLE_RATE)
            length = int(0.02 * SAMPLE_RATE)
            envelope = np.exp(-np.arange(length) / (0.004 * SAMPLE_RATE))
            noise = np.random.default_rng(index).uniform(-1.0, 1.0, length)
            signal[start:start + length] += (
                0.35 * envelope * noise
            ).astype(np.float32)
        return np.clip(signal, -1.0, 1.0)

    def test_kick_is_warm_and_hat_is_blue(self) -> None:
        bpm = 150.0
        level = waveform_analysis.compute_levels(
            self.techno_bar(bpm), SAMPLE_RATE
        )["detailed"]
        pps = level.peaks_per_second
        kick = int(0.01 * pps)
        hat = int(60.0 / bpm / 2 * pps) + 1

        # Die Analyse trennt die Anschlaege sauber.
        self.assertGreater(level.low[kick], level.high[kick] * 3)
        self.assertGreater(level.high[hat], level.low[hat] * 3)

        def colour(index: int):
            return spectral_color(
                np.array([level.low[index]]),
                np.array([level.mid[index]]),
                np.array([level.high[index]]),
            )[0]

        kick_rgb = colour(kick)
        hat_rgb = colour(hat)
        self.assertGreater(kick_rgb[0], kick_rgb[2] * 2)   # orange
        self.assertGreater(hat_rgb[2], hat_rgb[0] * 2)     # blau

    def test_kick_has_more_peak_than_rms(self) -> None:
        """Ein Transient traegt genau die Information, die den Peak braucht."""
        level = waveform_analysis.compute_levels(
            self.techno_bar(), SAMPLE_RATE
        )["detailed"]
        index = int(0.01 * level.peaks_per_second)
        self.assertGreater(level.peak[index], level.rms[index])
        self.assertGreater(level.rms[index], 0.2)


class PerformanceTests(unittest.TestCase):
    """Die Darstellung muss vier Decks bei 60 FPS tragen."""

    @staticmethod
    def measure(level, width=1024, height=220, mode=WaveformMode.RGB) -> float:
        renderer = WaveformRenderer()
        renderer.render_array(level, 10.0, 18.0, width, height, mode)
        runs = 30
        started = time.perf_counter()
        for index in range(runs):
            renderer.render_array(
                level, 10.0 + index * 0.01, 18.0 + index * 0.01,
                width, height, mode,
            )
        return (time.perf_counter() - started) / runs * 1000.0

    def test_real_track_render_is_fast(self) -> None:
        """Mit Musik: der Regelfall. Gemessen rund 3.3 ms."""
        from virtual_cdj.demo import BROKEN_WINDOW, build_track_info

        level = build_track_info(BROKEN_WINDOW).waveform.get("detailed")
        for mode in WaveformMode:
            average_ms = self.measure(level, mode=mode)
            self.assertLess(
                average_ms, 6.0,
                f"{mode.value} braucht {average_ms:.2f} ms je Bild",
            )

    def test_worst_case_render_stays_within_budget(self) -> None:
        """Kuenstlicher Vollausschlag in jeder Spalte - so laut ist Musik nie.

        Hier greift keine Beschraenkung auf die genutzten Zeilen, es ist also
        die obere Schranke. 16.7 ms sind das Budget fuer 60 FPS.
        """
        rng = np.random.default_rng(7)
        count = 320 * 60
        level = level_from(
            rng.uniform(0, 1, count), rng.uniform(0, 1, count),
            rng.uniform(0, 1, count),
            peak=rng.uniform(0.9, 1, count), rms=rng.uniform(0.7, 1, count),
            pps=320.0,
        )
        average_ms = self.measure(level)
        self.assertLess(
            average_ms, 9.0,
            f"Waveform braucht {average_ms:.2f} ms je Bild",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
