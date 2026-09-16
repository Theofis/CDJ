"""Waveform-Skalierung, SEARCH und die Pad-Modi.

Die Testfaelle aus den Abschnitten 26-28 der Vorgabe, mit ihrer Nummer im
Namen. Geprueft wird ueber die vorhandenen Bausteine - es gibt keine
zweite Waveform-, Audio-, Hot-Cue- oder Loop-Engine.
"""

from __future__ import annotations

import unittest

import numpy as np

from virtual_cdj.audio.analysis.waveform import compute_levels
from virtual_cdj.cdj_ui.waveform_render import (
    DISPLAY_HEADROOM,
    WaveformRenderer,
    display_scale,
    spectral_color,
)
from virtual_cdj.core import ids
from virtual_cdj.core.input_layer import InputLayer
from virtual_cdj.deck.commands import CommandType, command
from virtual_cdj.deck.display_state import WaveformMode
from virtual_cdj.deck.engine import SEARCH_JOG_SECONDS_PER_REV, SEARCH_SPEED, Deck
from virtual_cdj.deck.mapping import InputMapper
from virtual_cdj.deck.state import (
    BEAT_LOOP_LENGTHS,
    BeatGrid,
    PadMode,
    PlayState,
    TrackInfo,
    WaveformData,
    WaveformSet,
)
from virtual_cdj.jog import STEPS_PER_REV

SR = 48000
DURATION_S = 10.0


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ----------------------------------------------------------------------
# Hilfen: echte Analyse, echter Renderer
# ----------------------------------------------------------------------


def level_of(mono: np.ndarray, peaks_per_second: float = 150.0) -> WaveformData:
    """Die **vorhandene** Analyse benutzen, keine zweite FFT."""
    bands = compute_levels(
        mono.astype(np.float32), SR, {"detailed": peaks_per_second}
    )["detailed"]
    return WaveformData(
        peaks_per_second=bands.peaks_per_second,
        low=bands.low, mid=bands.mid, high=bands.high,
        peak=bands.peak, rms=bands.rms,
    )


def tone(frequency: float, amplitude: float = 1.0) -> np.ndarray:
    t = np.arange(int(SR * DURATION_S), dtype=np.float32) / SR
    return (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


# ----------------------------------------------------------------------
# Abschnitt 26: Waveform
# ----------------------------------------------------------------------


class WaveformScaleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = WaveformRenderer()

    def heights(self, level: WaveformData, start=0.0, end=DURATION_S, width=800):
        columns = self.renderer._columns(level, start, end, width)  # noqa: SLF001
        return self.renderer.amplitude_of(columns, display_scale(level))

    # -- TEST 1 --------------------------------------------------------

    def test_1_strong_peaks_are_never_cut_off(self) -> None:
        loud = tone(60.0, 0.9) + tone(3000.0, 0.3)
        # Einzelne Transienten obendrauf - der Fall, der frueher anschlug.
        loud[SR * 2] = 1.0
        loud[SR * 7] = -1.0
        amplitude = self.heights(level_of(loud))
        self.assertLessEqual(float(amplitude.max()), 1.0)
        self.assertEqual(int((amplitude > 1.0).sum()), 0)

    # -- TEST 2 --------------------------------------------------------

    def test_2_a_quiet_track_still_fills_the_height(self) -> None:
        """Frueher erreichte ein leiser Track nur 67 % der Flaeche."""
        quiet = tone(200.0, 0.05)
        quiet[SR * 5] = 0.9
        amplitude = self.heights(level_of(quiet))
        self.assertGreater(float(amplitude.max()), 0.95)

    def test_2b_every_track_reaches_nearly_the_top(self) -> None:
        for label, mono in (
            ("laut", tone(60.0, 0.9) + tone(3000.0, 0.3)),
            ("leise", tone(200.0, 0.02)),
            ("mittel", tone(440.0, 0.4)),
        ):
            with self.subTest(label):
                amplitude = self.heights(level_of(mono))
                self.assertGreater(float(amplitude.max()), 0.9)
                self.assertLessEqual(float(amplitude.max()), 1.0)

    # -- TEST 3 --------------------------------------------------------

    def test_3_scrolling_does_not_change_the_scale(self) -> None:
        """Abschnitt 2: keine abschnittsweise Normierung, kein Pumpen.

        Ein leiser Track mit **einer** lauten Stelle: nur das Fenster, das
        sie enthaelt, darf hoch ausschlagen. Wuerde je Ausschnitt normiert,
        waere jedes Fenster gleich hoch.
        """
        quiet = tone(200.0, 0.05)
        quiet[SR * 5] = 0.9
        level = level_of(quiet)
        scale = display_scale(level)

        windows = [
            float(self.heights(level, start, start + 1.0, 400).max())
            for start in (0.0, 2.0, 4.9, 6.0, 8.0)
        ]
        loud = max(windows)
        quiet_windows = [w for w in windows if w < loud * 0.8]
        self.assertEqual(len(quiet_windows), 4)
        # Die leisen Fenster sind untereinander gleich hoch.
        self.assertAlmostEqual(min(quiet_windows), max(quiet_windows), places=5)
        # Und der Massstab aendert sich dabei nicht.
        self.assertEqual(display_scale(level), scale)

    def test_3b_the_scale_is_computed_once_per_level(self) -> None:
        """Abschnitt 9: nicht in jedem Bild neu rechnen."""
        level = level_of(tone(440.0, 0.4))
        first = display_scale(level)
        # Der gemerkte Wert wird zurueckgegeben, nicht neu gerechnet.
        self.assertIs(display_scale(level), first)
        self.assertTrue(hasattr(level, "_display_scale"))

    def test_the_scale_keeps_the_headroom(self) -> None:
        level = level_of(tone(60.0, 0.9))
        columns = self.renderer._columns(level, 0.0, DURATION_S, 2000)  # noqa: SLF001
        raw = self.renderer.amplitude_of(columns, 1.0)
        scaled = self.renderer.amplitude_of(columns, display_scale(level))
        self.assertGreater(float(scaled.max()), float(raw.max()))
        self.assertLessEqual(float(scaled.max()), 1.0)

    def test_silence_does_not_divide_by_zero(self) -> None:
        level = level_of(np.zeros(SR * 2, dtype=np.float32))
        self.assertEqual(display_scale(level), 1.0)

    # -- TEST 4/5/6: RGB ------------------------------------------------

    def colour_of(self, mono: np.ndarray) -> np.ndarray:
        level = level_of(mono)
        columns = self.renderer._columns(level, 0.0, DURATION_S, 400)  # noqa: SLF001
        colour = spectral_color(
            columns["low"], columns["mid"], columns["high"]
        )
        return colour.mean(axis=0)

    def test_4_a_bass_track_is_clearly_red(self) -> None:
        red, green, blue = self.colour_of(tone(60.0, 0.8))
        self.assertGreater(red, green)
        self.assertGreater(red, blue)

    def test_5_a_treble_section_is_clearly_blue(self) -> None:
        red, green, blue = self.colour_of(tone(9000.0, 0.8))
        self.assertGreater(blue, red)
        self.assertGreater(blue, green)

    def test_5b_mids_are_clearly_green(self) -> None:
        red, green, blue = self.colour_of(tone(900.0, 0.8))
        self.assertGreater(green, red)

    def test_6_mixed_content_mixes_the_colours(self) -> None:
        """Abschnitt 7: keine drei festen Balkenfarben, echte Mischung."""
        bass_only = self.colour_of(tone(60.0, 0.8))
        bass_and_treble = self.colour_of(tone(60.0, 0.8) + tone(9000.0, 0.8))
        # Bass + Hoehen hat mehr Blau als Bass allein, bleibt aber rotlastig.
        self.assertGreater(bass_and_treble[2], bass_only[2])
        self.assertGreater(bass_and_treble[0], bass_and_treble[1])

    def test_6b_broadband_content_moves_towards_white(self) -> None:
        broad = tone(60.0, 0.6) + tone(900.0, 0.6) + tone(9000.0, 0.6)
        red, green, blue = self.colour_of(broad)
        spread = max(red, green, blue) - min(red, green, blue)
        self.assertLess(spread, 0.35)
        self.assertGreater(min(red, green, blue), 0.4)

    # -- Abschnitt 4: keine harte Begrenzung ----------------------------

    def test_three_band_mode_clips_nothing(self) -> None:
        """Frueher wurden bei einem basslastigen Track 100 % der Spalten
        oben abgeschnitten - der Bassbalken war ein volles Rechteck."""
        level = level_of(tone(60.0, 0.9) + tone(3000.0, 0.3))
        height = 120
        rgb = self.renderer.render_array(
            level, 0.0, DURATION_S, 400, height, mode=WaveformMode.THREE_BAND
        )
        # Die oberste Zeile darf nicht durchgehend gefuellt sein.
        from virtual_cdj.cdj_ui.waveform_render import BACKGROUND

        top_row = rgb[0]
        background = np.array(BACKGROUND, dtype=rgb.dtype)
        filled = int(np.any(top_row != background, axis=1).sum())
        self.assertLess(filled, 400, "oberste Zeile durchgehend gefuellt")

    def test_all_modes_stay_inside_the_image(self) -> None:
        level = level_of(tone(60.0, 0.9) + tone(9000.0, 0.5))
        for mode in WaveformMode:
            with self.subTest(mode.value):
                rgb = self.renderer.render_array(
                    level, 0.0, DURATION_S, 300, 80, mode=mode
                )
                self.assertEqual(rgb.shape, (80, 300, 3))


# ----------------------------------------------------------------------
# Abschnitt 27: SEARCH
# ----------------------------------------------------------------------


def make_track(duration_s: float = 300.0) -> TrackInfo:
    return TrackInfo(
        track_id="t", title="T", duration_s=duration_s, original_bpm=120.0,
        beat_grid=BeatGrid(first_beat_s=0.0, bpm=120.0),
    )


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.deck = Deck(1, time_source=self.clock)
        self.deck.load_track(make_track())

    def run_deck(self, seconds: float, step: float = 0.016) -> None:
        for _ in range(int(seconds / step)):
            self.clock.advance(step)
            self.deck.tick()

    def search(self, direction: int, pressed: bool) -> None:
        self.deck.execute(
            command(
                CommandType.SEARCH, 1, direction=direction, pressed=pressed
            )
        )

    def seek(self, position_s: float) -> None:
        self.deck.execute(command(CommandType.SEEK, 1, position_s=position_s))

    def play(self) -> None:
        self.deck.execute(command(CommandType.PLAY_PAUSE, 1))

    # -- TEST 7 --------------------------------------------------------

    def test_7_holding_forward_while_playing_fast_forwards(self) -> None:
        """Frueher wirkungslos: der Track lief einfach normal weiter."""
        self.seek(30.0)
        self.play()
        self.search(+1, True)
        self.run_deck(2.0)
        moved = self.deck.state.position_s - 30.0
        self.assertAlmostEqual(moved, 2.0 * SEARCH_SPEED, delta=0.2)

    # -- TEST 8 --------------------------------------------------------

    def test_8_releasing_stops_the_search_at_once(self) -> None:
        self.seek(30.0)
        self.search(+1, True)
        self.run_deck(1.0)
        during = self.deck.state.position_s
        self.search(+1, False)
        self.run_deck(1.0)
        self.assertAlmostEqual(self.deck.state.position_s, during, places=6)

    def test_8b_after_the_search_normal_playback_continues(self) -> None:
        self.seek(30.0)
        self.play()
        self.search(+1, True)
        self.run_deck(1.0)
        self.search(+1, False)
        here = self.deck.state.position_s
        self.run_deck(1.0)
        moved = self.deck.state.position_s - here
        self.assertAlmostEqual(moved, 1.0, delta=0.1)
        self.assertIs(self.deck.state.play_state, PlayState.PLAYING)

    # -- TEST 9 --------------------------------------------------------

    def test_9_holding_backward_rewinds(self) -> None:
        self.seek(100.0)
        self.play()
        self.search(-1, True)
        self.run_deck(2.0)
        moved = 100.0 - self.deck.state.position_s
        self.assertAlmostEqual(moved, 2.0 * SEARCH_SPEED, delta=0.2)

    # -- TEST 10 -------------------------------------------------------

    def test_10_search_while_paused_moves_the_position(self) -> None:
        self.seek(30.0)
        self.assertIsNot(self.deck.state.play_state, PlayState.PLAYING)
        self.search(+1, True)
        self.run_deck(0.5)
        self.search(+1, False)
        moved = self.deck.state.position_s - 30.0
        self.assertAlmostEqual(moved, 0.5 * SEARCH_SPEED, delta=0.2)

    def test_10b_paused_and_playing_search_at_the_same_rate(self) -> None:
        self.seek(30.0)
        self.search(+1, True)
        self.run_deck(1.0)
        self.search(+1, False)
        paused = self.deck.state.position_s - 30.0

        other = Deck(2, time_source=self.clock)
        other.load_track(make_track())
        other.execute(command(CommandType.SEEK, 2, position_s=30.0))
        other.execute(command(CommandType.PLAY_PAUSE, 2))
        other.execute(command(CommandType.SEARCH, 2, direction=+1, pressed=True))
        for _ in range(int(1.0 / 0.016)):
            self.clock.advance(0.016)
            other.tick()
        playing = other.state.position_s - 30.0
        self.assertAlmostEqual(paused, playing, delta=0.2)

    # -- TEST 11 -------------------------------------------------------

    def test_11_searching_back_at_the_start_stays_at_zero(self) -> None:
        self.seek(1.0)
        self.search(-1, True)
        self.run_deck(5.0)
        self.assertGreaterEqual(self.deck.state.position_s, 0.0)
        self.assertAlmostEqual(self.deck.state.position_s, 0.0, places=6)

    # -- TEST 12 -------------------------------------------------------

    def test_12_searching_forward_at_the_end_stays_inside(self) -> None:
        self.seek(295.0)
        self.search(+1, True)
        self.run_deck(5.0)
        self.assertLessEqual(self.deck.state.position_s, 300.0)

    # -- TEST 13 -------------------------------------------------------

    def test_13_search_and_jog_searches_fast(self) -> None:
        self.seek(100.0)
        self.search(+1, True)
        self.deck.execute(
            command(
                CommandType.JOG_MOVE, 1,
                delta=STEPS_PER_REV, ticks_per_rev=STEPS_PER_REV,
                touched=True,
            )
        )
        moved = self.deck.state.position_s - 100.0
        self.assertAlmostEqual(moved, SEARCH_JOG_SECONDS_PER_REV, delta=0.5)

    def test_13b_the_jog_direction_decides(self) -> None:
        self.seek(100.0)
        self.search(+1, True)
        self.deck.execute(
            command(
                CommandType.JOG_MOVE, 1,
                delta=-STEPS_PER_REV, ticks_per_rev=STEPS_PER_REV,
            )
        )
        self.assertLess(self.deck.state.position_s, 100.0)

    def test_13c_without_search_the_jog_behaves_as_before(self) -> None:
        """Abschnitt 29: die schnelle Suche darf den Jog nicht umwidmen."""
        self.seek(100.0)
        self.deck.execute(command(CommandType.JOG_TOUCH, 1, pressed=True))
        self.deck.execute(
            command(
                CommandType.JOG_MOVE, 1,
                delta=STEPS_PER_REV, ticks_per_rev=STEPS_PER_REV,
                touched=True,
            )
        )
        # Scratch: eine Umdrehung ist ein Beat (0.5 s bei 120 BPM) - nicht
        # die 30 Sekunden der Suche.
        self.assertAlmostEqual(self.deck.state.position_s, 100.5, delta=0.05)

    def test_search_holds_a_state_not_a_queue(self) -> None:
        """Abschnitt 12: Wiederholungen bauen nichts auf."""
        self.seek(30.0)
        for _ in range(50):
            self.search(+1, True)
        self.run_deck(1.0)
        moved = self.deck.state.position_s - 30.0
        self.assertAlmostEqual(moved, 1.0 * SEARCH_SPEED, delta=0.2)


# ----------------------------------------------------------------------
# Abschnitt 28: Pad-Modi
# ----------------------------------------------------------------------


class PadModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.layer = InputLayer()
        self.deck = Deck(1, time_source=self.clock)
        self.deck.load_track(make_track())
        self.mapper = InputMapper(1, self.deck.execute)
        self.layer.subscribe(self.mapper.handle_event)

    def tap(self, control_id: str) -> None:
        self.layer.press(control_id)
        self.layer.release(control_id)

    def pad(self, index: int) -> None:
        self.tap(ids.PADS[index])

    # -- TEST 14 -------------------------------------------------------

    def test_14_the_hot_cue_button_selects_the_hot_cue_mode(self) -> None:
        self.tap(ids.BEAT_JUMP)
        self.tap(ids.HOT_CUE)
        self.assertIs(self.deck.state.pad_mode, PadMode.HOT_CUE)

    # -- TEST 15/16 ----------------------------------------------------

    def test_15_an_empty_pad_sets_a_hot_cue(self) -> None:
        self.tap(ids.HOT_CUE)
        self.deck.execute(command(CommandType.SEEK, 1, position_s=12.0))
        self.pad(0)
        cue = self.deck.state.track.hot_cue(0)
        self.assertIsNotNone(cue)
        self.assertAlmostEqual(cue.position_s, 12.0, delta=0.6)

    def test_16_a_filled_pad_recalls_the_hot_cue(self) -> None:
        self.tap(ids.HOT_CUE)
        self.deck.execute(command(CommandType.SEEK, 1, position_s=12.0))
        self.pad(0)
        set_at = self.deck.state.track.hot_cue(0).position_s
        self.deck.execute(command(CommandType.SEEK, 1, position_s=80.0))
        self.pad(0)
        self.assertAlmostEqual(self.deck.state.position_s, set_at, places=6)
        # Und der Hotcue bleibt, wo er war - es wird nicht neu gesetzt.
        self.assertAlmostEqual(
            self.deck.state.track.hot_cue(0).position_s, set_at, places=6
        )

    # -- TEST 17 -------------------------------------------------------

    def test_17_the_beat_loop_button_selects_the_beat_loop_mode(self) -> None:
        self.tap(ids.HOT_CUE)
        self.tap(ids.BEAT_JUMP)
        self.assertIs(self.deck.state.pad_mode, PadMode.BEAT_LOOP)

    def test_the_modes_are_mutually_exclusive(self) -> None:
        """Abschnitt 20: es gibt genau einen Modus, nicht zwei Schalter."""
        for control_id, mode in (
            (ids.HOT_CUE, PadMode.HOT_CUE),
            (ids.BEAT_JUMP, PadMode.BEAT_LOOP),
            (ids.HOT_CUE, PadMode.HOT_CUE),
        ):
            self.tap(control_id)
            self.assertIs(self.deck.state.pad_mode, mode)

    # -- TEST 18/19/20 -------------------------------------------------

    def test_18_pad_e_makes_a_four_beat_loop(self) -> None:
        self.tap(ids.BEAT_JUMP)
        self.deck.execute(command(CommandType.SEEK, 1, position_s=10.0))
        self.pad(4)
        self.assertTrue(self.deck.state.loop.active)
        self.assertEqual(self.deck.state.loop.beats, 4.0)

    def test_19_pad_a_makes_a_quarter_beat_loop(self) -> None:
        self.tap(ids.BEAT_JUMP)
        self.pad(0)
        self.assertEqual(self.deck.state.loop.beats, 0.25)

    def test_20_pad_h_makes_a_thirty_two_beat_loop(self) -> None:
        self.tap(ids.BEAT_JUMP)
        self.pad(7)
        self.assertEqual(self.deck.state.loop.beats, 32.0)

    def test_every_pad_matches_the_central_table(self) -> None:
        """Abschnitt 18: die Laengen stehen zentral, nicht im Pad-Code."""
        self.tap(ids.BEAT_JUMP)
        for index, beats in enumerate(BEAT_LOOP_LENGTHS):
            with self.subTest(index=index):
                self.deck.execute(
                    command(CommandType.RELOOP_EXIT, 1)
                ) if self.deck.state.loop.active else None
                self.deck.execute(command(CommandType.SEEK, 1, position_s=10.0))
                self.pad(index)
                self.assertEqual(self.deck.state.loop.beats, beats)

    # -- TEST 21 -------------------------------------------------------

    def test_21_switching_modes_switches_the_pad_function(self) -> None:
        self.tap(ids.BEAT_JUMP)
        self.deck.execute(command(CommandType.SEEK, 1, position_s=10.0))
        self.pad(4)
        self.assertTrue(self.deck.state.loop.active)

        self.tap(ids.HOT_CUE)
        self.deck.execute(command(CommandType.SEEK, 1, position_s=40.0))
        self.pad(4)
        # Jetzt ein Hotcue, kein neuer Loop.
        self.assertIsNotNone(self.deck.state.track.hot_cue(4))

    def test_21b_no_stale_state_survives_the_switch(self) -> None:
        self.tap(ids.HOT_CUE)
        self.deck.execute(command(CommandType.SEEK, 1, position_s=10.0))
        self.pad(2)
        self.tap(ids.BEAT_JUMP)
        self.pad(2)
        self.assertTrue(self.deck.state.loop.active)
        self.assertEqual(self.deck.state.loop.beats, 1.0)
        # Der Hotcue ist dabei nicht verlorengegangen (Abschnitt 25).
        self.assertIsNotNone(self.deck.state.track.hot_cue(2))

    # -- Abschnitt 24: die runden Beat-Loop-Taster bleiben --------------

    def test_the_round_beat_loop_buttons_still_work(self) -> None:
        self.tap(ids.BEAT_LOOP_4)
        self.assertEqual(self.deck.state.loop.beats, 4.0)
        self.tap(ids.BEAT_LOOP_8)
        self.assertEqual(self.deck.state.loop.beats, 8.0)

    def test_the_round_buttons_work_in_hot_cue_mode_too(self) -> None:
        self.tap(ids.HOT_CUE)
        self.tap(ids.BEAT_LOOP_4)
        self.assertEqual(self.deck.state.loop.beats, 4.0)

    # -- Abschnitt 17: Quantize wird beachtet ---------------------------

    def test_quantize_snaps_a_new_hot_cue(self) -> None:
        self.tap(ids.HOT_CUE)
        self.deck.execute(command(CommandType.QUANTIZE_TOGGLE, 1))
        self.assertTrue(self.deck.state.quantize)
        self.deck.execute(command(CommandType.SEEK, 1, position_s=10.2))
        self.pad(3)
        # 120 BPM: Beats alle 0.5 s.
        self.assertAlmostEqual(
            self.deck.state.track.hot_cue(3).position_s, 10.0, places=6
        )


# ----------------------------------------------------------------------
# Abschnitt 21: der Modus ist sichtbar
# ----------------------------------------------------------------------


class PadModeLedTests(unittest.TestCase):
    """Eine Quelle fuer Bildschirm und Hardware-LED."""

    def setUp(self) -> None:
        from virtual_cdj.app import CdjApplication

        self.app = CdjApplication([1], start_audio=False)
        self.deck = self.app.decks[1]
        self.deck.load_track(make_track())

    def tearDown(self) -> None:
        self.app.close()

    def leds(self) -> tuple[bool, bool]:
        state = self.app.input_layer.state
        return (state.led(ids.HOT_CUE), state.led(ids.BEAT_JUMP))

    def test_exactly_one_mode_led_is_on(self) -> None:
        self.deck.execute(
            command(CommandType.PAD_MODE, 1, mode=PadMode.HOT_CUE)
        )
        self.app.tick()
        self.assertEqual(self.leds(), (True, False))

        self.deck.execute(
            command(CommandType.PAD_MODE, 1, mode=PadMode.BEAT_LOOP)
        )
        self.app.tick()
        self.assertEqual(self.leds(), (False, True))

    def test_the_led_follows_the_deck_state_not_the_button(self) -> None:
        """Abschnitt 21: keine getrennte Zustandshaltung fuer die Anzeige."""
        self.deck.execute(
            command(CommandType.PAD_MODE, 1, mode=PadMode.BEAT_LOOP)
        )
        self.app.tick()
        on_hot_cue, on_beat_loop = self.leds()
        self.assertIs(on_beat_loop, self.deck.state.pad_mode is PadMode.BEAT_LOOP)
        self.assertIs(on_hot_cue, self.deck.state.pad_mode is PadMode.HOT_CUE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
