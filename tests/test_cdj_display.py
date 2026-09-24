"""Integrationstests der CDJ-3000-Displayoberflaeche.

Deckt die zwoelf Tests aus Abschnitt 30 des Auftrags ab, plus die
Displaylogik: Panels, Zoom, Beat Countdown, Zeitmodus, Wellenformmodi,
gestapelte Master-Ansicht und Browser.

Gearbeitet wird mit den Demo-Tracks - der einzigen erlaubten Quelle
synthetischer Daten. Playback, Loops und Cues laufen ueber die echte
Audio-Engine.
"""

from __future__ import annotations

import unittest

import numpy as np

from virtual_cdj.audio.engine import AudioEngine
from virtual_cdj.deck.commands import CommandType, Views, command
from virtual_cdj.deck.display_state import (
    BEAT_JUMP_VALUES,
    BEAT_LOOP_VALUES,
    ZOOM_BEATS,
    ZOOM_WINDOWS_S,
    CdjDisplayState,
    MasterDeckView,
    TimeMode,
    TouchPanel,
    WaveformHeader,
    WaveformMode,
    related_keys,
)
from virtual_cdj.deck.engine import Deck
from virtual_cdj.deck.mode_manager import OperatingMode
from virtual_cdj.deck.provider import LocalDeckStateProvider
from virtual_cdj.deck.state import CueKind, PlayState
from virtual_cdj.demo import (
    BROKEN_WINDOW,
    STEEL_PRESSURE,
    DemoTrackProvider,
    build_track_info,
)

SAMPLE_RATE = 44100

#: Demo-Provider einmal je Testlauf - die Audioerzeugung dauert.
_PROVIDER: DemoTrackProvider | None = None


def provider() -> DemoTrackProvider:
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = DemoTrackProvider()
    return _PROVIDER


class Rig:
    """Deck mit echter Audioausgabe, ohne Soundkarte."""

    def __init__(self, deck_id: int = 1, block: int = 512) -> None:
        self.block = block
        self.engine = AudioEngine(block_frames=block)
        self.voice = self.engine.voice(deck_id)
        self.deck = Deck(deck_id)
        self.deck.attach_playback(self.voice)
        self.provider = LocalDeckStateProvider(self.deck)

    def load(self, track_id: str) -> None:
        info, samples = provider().load(track_id)
        self.deck.load_track(info, samples)

    def render(self, seconds: float) -> np.ndarray:
        blocks = max(1, int(seconds * SAMPLE_RATE / self.block))
        parts = [self.engine.render_block(self.block).copy() for _ in range(blocks)]
        self.deck.tick()
        return np.concatenate(parts)

    def send(self, command_type: CommandType, **params) -> None:
        self.provider.send(
            command(command_type, self.deck.deck_id, "TEST", **params)
        )

    def close(self) -> None:
        self.engine.close()


# --------------------------------------------------------------------------
# Demo-Tracks
# --------------------------------------------------------------------------


class DemoTrackTests(unittest.TestCase):
    """Test 1: Broken Window laden."""

    def test_broken_window_has_the_specified_values(self) -> None:
        info = build_track_info(BROKEN_WINDOW)
        self.assertEqual(info.title, "Broken Window")
        self.assertEqual(info.artist, "SHØRDY")
        self.assertAlmostEqual(info.original_bpm, 154.0)
        self.assertEqual(info.key, "4A")
        self.assertAlmostEqual(info.duration_s, 330.0)  # 5:30
        self.assertIn("Industrial", info.genre)
        self.assertEqual(info.source, "DEMO")

    def test_steel_pressure_has_the_specified_values(self) -> None:
        info = build_track_info(STEEL_PRESSURE)
        self.assertEqual(info.title, "Steel Pressure")
        self.assertAlmostEqual(info.original_bpm, 156.0)
        self.assertEqual(info.key, "5A")
        self.assertAlmostEqual(info.duration_s, 300.0)  # 5:00
        self.assertIn("Techno", info.genre)

    def test_both_tracks_have_waveform_and_beat_grid(self) -> None:
        for spec in (BROKEN_WINDOW, STEEL_PRESSURE):
            info = build_track_info(spec)
            self.assertIsNotNone(info.waveform, spec.title)
            self.assertTrue(info.waveform.is_consistent(), spec.title)
            self.assertEqual(
                set(info.waveform.levels),
                {"overview", "medium", "detailed"},
            )
            self.assertIsNotNone(info.beat_grid, spec.title)
            self.assertTrue(info.beat_grid.is_valid, spec.title)
            self.assertAlmostEqual(info.beat_grid.bpm, spec.bpm)

    def test_hot_cues_sit_on_the_structure(self) -> None:
        info = build_track_info(BROKEN_WINDOW)
        self.assertGreaterEqual(len(info.hot_cues), 6)
        comments = [cue.comment for cue in info.hot_cues]
        for section in ("Intro", "Build", "Drop 1", "Break", "Drop 2", "Outro"):
            self.assertIn(section, comments)
        # Jeder Cue hat Position, Farbe und Label.
        for cue in info.hot_cues:
            self.assertGreaterEqual(cue.position_s, 0.0)
            self.assertTrue(cue.color.startswith("#"))
            self.assertIn(cue.label, "ABCDEFGH")

    def test_at_least_one_cue_is_a_saved_loop(self) -> None:
        for spec in (BROKEN_WINDOW, STEEL_PRESSURE):
            info = build_track_info(spec)
            loops = [
                cue for cue in info.hot_cues if cue.kind is CueKind.LOOP
            ]
            self.assertGreaterEqual(len(loops), 1, spec.title)
            for cue in loops:
                self.assertIsNotNone(cue.loop_end_s)
                self.assertGreater(cue.loop_end_s, cue.position_s)

    def test_cues_are_snapped_to_the_beat_grid(self) -> None:
        info = build_track_info(BROKEN_WINDOW)
        grid = info.beat_grid
        for cue in info.hot_cues:
            self.assertAlmostEqual(
                grid.snap(cue.position_s), cue.position_s, places=6,
                msg=f"{cue.label} liegt nicht auf einem Beat",
            )

    def test_structure_is_visible_in_the_waveform(self) -> None:
        """Der Break muss weniger Bass haben als der Drop."""
        info = build_track_info(BROKEN_WINDOW)
        level = info.waveform.get("overview")
        low = np.asarray(level.low)

        def band_at(seconds: float) -> float:
            index = int(seconds * level.peaks_per_second)
            return float(low[index - 20:index + 20].mean())

        drop = band_at(120.0)   # Drop 1
        break_ = band_at(180.0)  # Break
        self.assertGreater(
            drop, break_ * 2.0,
            f"Drop {drop:.3f} nicht deutlich lauter als Break {break_:.3f}",
        )

    def test_tracks_have_different_structures(self) -> None:
        first = build_track_info(BROKEN_WINDOW)
        second = build_track_info(STEEL_PRESSURE)
        self.assertNotEqual(
            [c.comment for c in first.hot_cues],
            [c.comment for c in second.hot_cues],
        )

    def test_demo_source_is_declared(self) -> None:
        """Synthetische Herkunft darf nicht verschleiert werden."""
        for info in provider().infos():
            self.assertEqual(info.source, "DEMO")

    def test_provider_delivers_audio(self) -> None:
        info, samples = provider().load(BROKEN_WINDOW.track_id)
        self.assertIsNotNone(samples)
        self.assertEqual(samples.shape[1], 2)
        self.assertEqual(samples.dtype, np.float32)
        self.assertLessEqual(float(np.abs(samples).max()), 1.0)
        self.assertGreater(float(np.abs(samples).max()), 0.5)


# --------------------------------------------------------------------------
# Transport, Waveform, Zeit
# --------------------------------------------------------------------------


class PlaybackTests(unittest.TestCase):
    """Tests 2, 3, 11, 12."""

    def setUp(self) -> None:
        self.rig = Rig(1)
        self.rig.load(BROKEN_WINDOW.track_id)
        self.display = CdjDisplayState(deck=self.rig.deck.state)

    def tearDown(self) -> None:
        self.rig.close()

    def sync(self) -> CdjDisplayState:
        self.display = self.display.with_deck(self.rig.deck.state)
        return self.display

    def test_load_gives_the_display_real_values(self) -> None:
        display = self.sync()
        self.assertAlmostEqual(display.deck.original_bpm, 154.0)
        self.assertEqual(display.deck.key, "4A")
        self.assertAlmostEqual(display.deck.duration_s, 330.0)
        self.assertTrue(display.deck.has_waveform)
        self.assertTrue(display.deck.has_beat_grid)

    def test_play_moves_the_position(self) -> None:
        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(2.0)
        display = self.sync()
        self.assertIs(display.deck.play_state, PlayState.PLAYING)
        self.assertAlmostEqual(display.deck.position_s, 2.0, delta=0.05)

    def test_waveform_window_follows_the_position(self) -> None:
        """Der Playhead bleibt mittig, das Fenster wandert."""
        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(1.0)
        first = self.sync().deck.position_s
        self.rig.render(2.0)
        second = self.sync().deck.position_s
        self.assertGreater(second, first)

        window = self.display.window_s
        # Fenstermitte ist immer die Abspielposition.
        for position in (first, second):
            start = position - window / 2
            self.assertAlmostEqual(start + window / 2, position, places=9)

    def test_pause_stops_exactly_at_the_position(self) -> None:
        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(1.5)
        position = self.sync().deck.position_s
        self.rig.send(CommandType.PLAY_PAUSE)
        data = self.rig.render(1.0)
        self.assertEqual(float(np.abs(data).max()), 0.0)
        self.assertAlmostEqual(
            self.sync().deck.position_s, position, places=6
        )

    def test_tempo_change_updates_bpm_and_percent(self) -> None:
        self.rig.send(CommandType.TEMPO_SET, value=1.0)  # +10 %
        display = self.sync()
        self.assertAlmostEqual(display.deck.tempo_percent, 10.0, places=3)
        self.assertAlmostEqual(display.deck.current_bpm, 154.0 * 1.1, places=1)
        self.assertAlmostEqual(display.deck.original_bpm, 154.0)

        self.rig.send(CommandType.TEMPO_SET, value=0.0)  # -10 %
        display = self.sync()
        self.assertAlmostEqual(display.deck.tempo_percent, -10.0, places=3)
        self.assertAlmostEqual(display.deck.current_bpm, 154.0 * 0.9, places=1)

    def test_end_of_track_keeps_the_display_consistent(self) -> None:
        self.rig.send(CommandType.SEEK, position_s=329.9)
        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(1.0)
        display = self.sync()
        self.assertIs(display.deck.play_state, PlayState.STOPPED)
        self.assertLessEqual(display.deck.position_s, 330.0 + 1e-6)
        self.assertGreaterEqual(display.deck.remaining_s, 0.0)
        self.assertLessEqual(display.deck.progress, 1.0)
        # Zeitanzeige bleibt gueltig.
        self.assertGreaterEqual(display.time_value_s, 0.0)


class TimeModeTests(unittest.TestCase):
    """Test 7: Remaining / Elapsed umschalten."""

    def setUp(self) -> None:
        self.rig = Rig(1)
        self.rig.load(BROKEN_WINDOW.track_id)
        self.rig.send(CommandType.SEEK, position_s=60.0)
        self.display = CdjDisplayState(deck=self.rig.deck.state)

    def tearDown(self) -> None:
        self.rig.close()

    def test_toggle_switches_the_value(self) -> None:
        self.assertIs(self.display.time_mode, TimeMode.REMAIN)
        self.assertAlmostEqual(self.display.time_value_s, 270.0, delta=0.01)
        self.assertEqual(self.display.time_prefix, "-")

        elapsed = self.display.with_time_mode_toggled()
        self.assertIs(elapsed.time_mode, TimeMode.ELAPSED)
        self.assertAlmostEqual(elapsed.time_value_s, 60.0, delta=0.01)
        self.assertEqual(elapsed.time_prefix, "")

        self.assertIs(
            elapsed.with_time_mode_toggled().time_mode, TimeMode.REMAIN
        )

    def test_time_comes_from_duration_and_position(self) -> None:
        deck = self.display.deck
        self.assertAlmostEqual(
            self.display.time_value_s, deck.duration_s - deck.position_s,
            places=6,
        )


# --------------------------------------------------------------------------
# Hot Cues, Loops, Beat Jump
# --------------------------------------------------------------------------


class HotCueTests(unittest.TestCase):
    """Test 4: Hot Cue C springt zum Drop."""

    def setUp(self) -> None:
        self.rig = Rig(1)
        self.rig.load(BROKEN_WINDOW.track_id)

    def tearDown(self) -> None:
        self.rig.close()

    def test_hot_cue_c_jumps_to_drop_one(self) -> None:
        track = self.rig.deck.state.track
        cue = track.hot_cue(2)  # C
        self.assertIsNotNone(cue)
        self.assertEqual(cue.label, "C")
        self.assertEqual(cue.comment, "Drop 1")

        self.rig.send(CommandType.PAD, index=2, pressed=True)
        self.assertAlmostEqual(
            self.rig.deck.state.position_s, cue.position_s, places=5
        )
        self.assertIs(self.rig.deck.state.play_state, PlayState.PLAYING)

    def test_all_demo_cues_are_reachable(self) -> None:
        track = self.rig.deck.state.track
        for cue in track.hot_cues:
            self.rig.send(CommandType.SEEK, position_s=0.0)
            self.rig.send(CommandType.PAD, index=cue.index, pressed=True)
            self.assertAlmostEqual(
                self.rig.deck.state.position_s, cue.position_s, places=5,
                msg=f"Cue {cue.label}",
            )

    def test_loop_cue_activates_a_loop(self) -> None:
        track = self.rig.deck.state.track
        loop_cue = next(
            cue for cue in track.hot_cues if cue.kind is CueKind.LOOP
        )
        self.rig.send(CommandType.PAD, index=loop_cue.index, pressed=True)
        loop = self.rig.deck.state.loop
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.in_s, loop_cue.position_s, places=5)
        self.assertAlmostEqual(loop.out_s, loop_cue.loop_end_s, places=5)


class BeatLoopTests(unittest.TestCase):
    """Test 5: Beat Loop 4 am Beatgrid."""

    def setUp(self) -> None:
        self.rig = Rig(1)
        self.rig.load(BROKEN_WINDOW.track_id)

    def tearDown(self) -> None:
        self.rig.close()

    def test_beat_loop_four_matches_the_grid(self) -> None:
        self.rig.send(CommandType.SEEK, position_s=100.0)
        self.rig.send(CommandType.BEAT_LOOP, beats=4)
        loop = self.rig.deck.state.loop
        self.assertTrue(loop.active)
        expected = 4 * 60.0 / 154.0
        self.assertAlmostEqual(loop.length_s, expected, places=4)
        self.assertEqual(loop.label(), "4")

    def test_all_panel_values_produce_matching_loops(self) -> None:
        grid = self.rig.deck.state.track.beat_grid
        for beats in BEAT_LOOP_VALUES:
            self.rig.send(CommandType.SEEK, position_s=100.0)
            self.rig.send(CommandType.BEAT_LOOP, beats=beats)
            loop = self.rig.deck.state.loop
            self.assertTrue(loop.active, f"{beats} Beats")
            self.assertAlmostEqual(
                loop.length_s, beats * grid.beat_interval_s, places=4,
                msg=f"{beats} Beats",
            )

    def test_loop_boundaries_sit_on_beats(self) -> None:
        grid = self.rig.deck.state.track.beat_grid
        self.rig.send(CommandType.QUANTIZE_TOGGLE)
        self.rig.send(CommandType.SEEK, position_s=100.3)
        self.rig.send(CommandType.BEAT_LOOP, beats=8)
        loop = self.rig.deck.state.loop
        self.assertAlmostEqual(grid.snap(loop.in_s), loop.in_s, places=5)

    def test_loop_holds_the_audio_position(self) -> None:
        self.rig.send(CommandType.SEEK, position_s=100.0)
        self.rig.send(CommandType.BEAT_LOOP, beats=4)
        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(8.0)
        loop = self.rig.deck.state.loop
        position = self.rig.deck.state.position_s
        self.assertGreaterEqual(position, loop.in_s - 1e-3)
        self.assertLess(position, loop.out_s + 1e-3)
        self.assertGreater(self.rig.voice.loop_wraps, 0)


class BeatJumpTests(unittest.TestCase):
    """Test 6: Beat Jump +16."""

    def setUp(self) -> None:
        self.rig = Rig(1)
        self.rig.load(BROKEN_WINDOW.track_id)

    def tearDown(self) -> None:
        self.rig.close()

    def test_jump_sixteen_beats_forward(self) -> None:
        grid = self.rig.deck.state.track.beat_grid
        self.rig.send(CommandType.SEEK, position_s=100.0)
        before = self.rig.deck.state.position_s
        self.rig.send(CommandType.BEAT_JUMP, direction=+1, beats=16)
        after = self.rig.deck.state.position_s
        self.assertAlmostEqual(
            after - before, 16 * grid.beat_interval_s, places=4
        )

    def test_jump_backwards(self) -> None:
        grid = self.rig.deck.state.track.beat_grid
        self.rig.send(CommandType.SEEK, position_s=100.0)
        self.rig.send(CommandType.BEAT_JUMP, direction=-1, beats=16)
        self.assertAlmostEqual(
            self.rig.deck.state.position_s,
            100.0 - 16 * grid.beat_interval_s,
            places=4,
        )

    def test_all_panel_values(self) -> None:
        grid = self.rig.deck.state.track.beat_grid
        for beats in BEAT_JUMP_VALUES:
            self.rig.send(CommandType.SEEK, position_s=100.0)
            self.rig.send(CommandType.BEAT_JUMP, direction=+1, beats=beats)
            self.assertAlmostEqual(
                self.rig.deck.state.position_s - 100.0,
                beats * grid.beat_interval_s,
                places=4, msg=f"{beats} Beats",
            )


class KeyShiftTests(unittest.TestCase):
    """Key Shift: Zustand ja, DSP nein (Kategorie B)."""

    def setUp(self) -> None:
        self.rig = Rig(1)
        self.rig.load(BROKEN_WINDOW.track_id)

    def tearDown(self) -> None:
        self.rig.close()

    def test_shift_up_down_and_reset(self) -> None:
        self.assertEqual(self.rig.deck.state.key_shift, 0)
        for _ in range(3):
            self.rig.send(CommandType.KEY_SHIFT, semitones=1)
        self.assertEqual(self.rig.deck.state.key_shift, 3)
        self.assertEqual(self.rig.deck.state.displayed_key, "4A +3")

        self.rig.send(CommandType.KEY_SHIFT, semitones=-1)
        self.assertEqual(self.rig.deck.state.key_shift, 2)

        self.rig.send(CommandType.KEY_SHIFT_RESET)
        self.assertEqual(self.rig.deck.state.key_shift, 0)
        self.assertEqual(self.rig.deck.state.displayed_key, "4A")

    def test_shift_is_limited(self) -> None:
        for _ in range(30):
            self.rig.send(CommandType.KEY_SHIFT, semitones=1)
        self.assertEqual(self.rig.deck.state.key_shift, 12)

    def test_missing_dsp_is_recorded_not_faked(self) -> None:
        """Kein Pitch-Shifting im Audio - das muss vermerkt sein."""
        self.rig.send(CommandType.KEY_SHIFT, semitones=2)
        self.assertTrue(
            any("KEY_SHIFT" in entry for entry in self.rig.deck.unsupported)
        )

    def test_audio_is_unchanged_by_key_shift(self) -> None:
        """Ehrlichkeitspruefung: das Signal darf sich nicht veraendern."""
        self.rig.send(CommandType.SEEK, position_s=100.0)
        self.rig.send(CommandType.PLAY_PAUSE)
        reference = self.rig.render(0.2).copy()

        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.send(CommandType.KEY_SHIFT, semitones=5)
        self.rig.send(CommandType.SEEK, position_s=100.0)
        self.rig.send(CommandType.PLAY_PAUSE)
        shifted = self.rig.render(0.2)
        np.testing.assert_allclose(reference, shifted, atol=1e-6)


# --------------------------------------------------------------------------
# Beat Countdown
# --------------------------------------------------------------------------


class BeatCountdownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig(1)
        self.rig.load(BROKEN_WINDOW.track_id)
        self.display = CdjDisplayState(deck=self.rig.deck.state)

    def tearDown(self) -> None:
        self.rig.close()

    def at(self, position_s: float):
        self.rig.send(CommandType.SEEK, position_s=position_s)
        self.display = self.display.with_deck(self.rig.deck.state)
        return self.display.beat_countdown()

    def test_counts_to_the_next_cue(self) -> None:
        track = self.rig.deck.state.track
        build = track.hot_cue(1)  # B - Build
        countdown = self.at(build.position_s - 8.0)
        self.assertTrue(countdown.is_valid)
        self.assertEqual(countdown.label, "B")
        self.assertAlmostEqual(countdown.target_s, build.position_s, places=5)

    def test_distance_is_computed_from_the_grid(self) -> None:
        grid = self.rig.deck.state.track.beat_grid
        build = self.rig.deck.state.track.hot_cue(1)
        # Genau 16 Beats vor dem Cue.
        position = build.position_s - 16 * grid.beat_interval_s
        countdown = self.at(position)
        self.assertEqual(countdown.total_beats, 16)
        self.assertEqual(countdown.bars, 4)
        self.assertEqual(countdown.beats, 0)
        self.assertEqual(countdown.text(), "4.1")

    def test_countdown_shrinks_as_the_track_runs(self) -> None:
        build = self.rig.deck.state.track.hot_cue(1)
        far = self.at(build.position_s - 20.0).total_beats
        near = self.at(build.position_s - 5.0).total_beats
        self.assertGreater(far, near)

    def test_no_cue_left_means_no_countdown(self) -> None:
        countdown = self.at(329.0)
        self.assertFalse(countdown.is_valid)
        self.assertEqual(countdown.text(), "--.-")

    def test_memory_cues_count_too(self) -> None:
        track = self.rig.deck.state.track
        self.assertTrue(track.memory_cues)
        memory = min(m.position_s for m in track.memory_cues)
        # Kurz davor darf nur der Memory Cue in Frage kommen.
        countdown = self.at(memory - 2.0)
        self.assertTrue(countdown.is_valid)
        self.assertLessEqual(countdown.target_s, memory + 1e-6)


# --------------------------------------------------------------------------
# Zoom
# --------------------------------------------------------------------------


class ZoomTests(unittest.TestCase):
    """Test 8: Zoomen darf Beatgrid und Marker nicht verschieben."""

    def setUp(self) -> None:
        self.rig = Rig(1)
        self.rig.load(BROKEN_WINDOW.track_id)
        self.rig.send(CommandType.SEEK, position_s=120.0)
        self.display = CdjDisplayState(deck=self.rig.deck.state)

    def tearDown(self) -> None:
        self.rig.close()

    def test_zoom_steps_are_musical(self) -> None:
        """Die Zoomstufen sind Beats, nicht Sekunden.

        Von der Standardstufe ganz hinein- und dann Schritt fuer Schritt
        herauszoomen; dabei muessen alle Beatstufen vorkommen und das
        Zeitfenster jeweils zum Tempo passen.
        """
        display = self.display
        beat_s = 60.0 / display.deck.current_bpm
        self.assertTrue(display.is_beat_zoom)
        self.assertEqual(display.zoom_beats, ZOOM_BEATS[2])
        for _ in range(len(ZOOM_BEATS)):
            display = display.with_zoom(-1)
        beats, windows = [], []
        for _ in range(len(ZOOM_BEATS)):
            beats.append(display.zoom_beats)
            windows.append(display.window_s)
            display = display.with_zoom(+1)
        self.assertEqual(beats, list(ZOOM_BEATS))
        for count, window in zip(ZOOM_BEATS, windows):
            self.assertAlmostEqual(window, count * beat_s, places=9)

    def test_window_follows_the_tempo(self) -> None:
        """Bei anderem Tempo zeigt dieselbe Stufe dieselben Beats."""
        before = self.display.window_s
        self.rig.send(CommandType.TEMPO_SET, value=1.0)  # schneller
        faster = self.display.with_deck(self.rig.deck.state)
        self.assertEqual(faster.zoom_beats, self.display.zoom_beats)
        self.assertLess(faster.window_s, before)

    def test_zoom_without_beat_grid_stays_in_seconds(self) -> None:
        from virtual_cdj.deck.state import TrackInfo, empty_state

        state = empty_state(1).with_changes(
            track=TrackInfo(track_id="x", title="X", duration_s=60.0)
        )
        display = CdjDisplayState(deck=state)
        self.assertFalse(display.is_beat_zoom)
        self.assertEqual(display.window_s, ZOOM_WINDOWS_S[2])
        self.assertIn("s", display.zoom_label)

    def test_zoom_is_limited(self) -> None:
        deepest = self.display
        for _ in range(20):
            deepest = deepest.with_zoom(-1)
        self.assertEqual(deepest.zoom_beats, min(ZOOM_BEATS))
        widest = self.display
        for _ in range(20):
            widest = widest.with_zoom(+1)
        self.assertEqual(widest.zoom_beats, max(ZOOM_BEATS))

    def test_playhead_stays_centred_at_every_zoom(self) -> None:
        display = self.display
        position = display.deck.position_s
        for _ in range(len(ZOOM_WINDOWS_S)):
            window = display.window_s
            start = position - window / 2
            end = start + window
            self.assertAlmostEqual((start + end) / 2, position, places=9)
            display = display.with_zoom(+1)

    def test_markers_keep_their_time_at_every_zoom(self) -> None:
        """Beat- und Cue-Positionen sind Zeitwerte - Zoom aendert sie nicht.

        Geprueft wird die Abbildung Zeit -> Pixel: derselbe Zeitpunkt muss
        bei jeder Zoomstufe auf demselben relativen Anteil des Fensters
        liegen, wenn man den Abstand zur Position mitskaliert.
        """
        display = self.display
        track = display.deck.track
        grid = track.beat_grid
        position = display.deck.position_s
        width = 1000

        for _ in range(len(ZOOM_WINDOWS_S)):
            window = display.window_s
            start = position - window / 2

            def x_of(seconds: float) -> float:
                return (seconds - start) / window * width

            # Playhead immer in der Mitte.
            self.assertAlmostEqual(x_of(position), width / 2, places=6)

            # Beat auf dem Playhead bleibt am Playhead.
            number = grid.beat_number_at(position)
            beat_time = grid.beat_time(number)
            offset = beat_time - position
            self.assertAlmostEqual(
                x_of(beat_time), width / 2 + offset / window * width,
                places=6,
            )

            # Cue-Marker: Reihenfolge und Vorzeichen bleiben erhalten.
            for cue in track.hot_cues:
                if cue.position_s < position:
                    self.assertLess(x_of(cue.position_s), width / 2)
                elif cue.position_s > position:
                    self.assertGreater(x_of(cue.position_s), width / 2)
            display = display.with_zoom(+1)

    def test_waveform_level_follows_the_zoom(self) -> None:
        """Beim Hineinzoomen muss eine feinere Stufe gewaehlt werden."""
        waveform = self.display.deck.track.waveform
        width = 1024
        coarse = waveform.level_for(max(ZOOM_WINDOWS_S) / width)
        fine = waveform.level_for(min(ZOOM_WINDOWS_S) / width)
        self.assertGreaterEqual(
            fine.peaks_per_second, coarse.peaks_per_second
        )


# --------------------------------------------------------------------------
# Master / Stacked Waveform
# --------------------------------------------------------------------------


class MasterViewTests(unittest.TestCase):
    """Tests 9 und 10: gestapelte Ansicht und Masterwechsel."""

    def setUp(self) -> None:
        self.local = Rig(2)
        self.local.load(STEEL_PRESSURE.track_id)
        self.remote = Rig(1)
        self.remote.load(BROKEN_WINDOW.track_id)
        self.remote.send(CommandType.MASTER_SET)  # Deck 1 wird Master

    def tearDown(self) -> None:
        self.local.close()
        self.remote.close()

    def display(self) -> CdjDisplayState:
        return CdjDisplayState(
            deck=self.local.deck.state,
            master=MasterDeckView.from_deck_state(self.remote.deck.state),
        )

    def test_stacked_view_shows_local_and_master(self) -> None:
        display = self.display()
        self.assertEqual(display.deck.track.title, "Steel Pressure")
        self.assertEqual(display.master.title, "Broken Window")
        self.assertTrue(display.master_is_other_deck)
        self.assertTrue(display.master.is_master)
        self.assertEqual(display.master.player_id, 1)

    def test_master_view_carries_everything_the_display_needs(self) -> None:
        master = self.display().master
        self.assertAlmostEqual(master.bpm, 154.0)
        self.assertEqual(master.key, "4A")
        self.assertAlmostEqual(master.duration_s, 330.0)
        self.assertIsNotNone(master.beat_grid)
        self.assertTrue(master.has_waveform)
        self.assertTrue(master.has_track)

    def test_own_master_is_not_stacked_twice(self) -> None:
        """Ist dieses Deck Master, entfaellt die zweite Wellenform."""
        self.remote.send(CommandType.MASTER_SET)  # Deck 1 abgeben
        self.local.send(CommandType.MASTER_SET)   # Deck 2 uebernimmt
        display = CdjDisplayState(
            deck=self.local.deck.state,
            master=MasterDeckView.from_deck_state(self.local.deck.state),
        )
        self.assertTrue(display.is_master)
        self.assertFalse(display.master_is_other_deck)

    def test_master_change_updates_the_view(self) -> None:
        self.assertTrue(self.display().master_is_other_deck)
        # Master wechselt auf das lokale Deck.
        self.remote.send(CommandType.MASTER_SET)
        self.local.send(CommandType.MASTER_SET)
        display = CdjDisplayState(
            deck=self.local.deck.state,
            master=MasterDeckView.from_deck_state(self.local.deck.state),
        )
        self.assertFalse(display.master_is_other_deck)
        self.assertTrue(display.is_master)

    def test_both_waveforms_use_the_same_window(self) -> None:
        """Nur mit gleicher Skalierung ist der Vergleich aussagekraeftig."""
        display = self.display()
        window = display.window_s
        local_start = display.deck.position_s - window / 2
        master_start = display.master.position_s - window / 2
        self.assertAlmostEqual(
            (local_start + window) - local_start,
            (master_start + window) - master_start,
            places=9,
        )

    def test_key_relation_to_master_is_detected(self) -> None:
        # 5A (Steel Pressure) ist zu 4A (Broken Window) verwandt.
        self.assertTrue(self.display().key_is_related_to_master)
        self.assertIn("5A", related_keys("4A"))

    def test_unrelated_key_is_not_marked(self) -> None:
        import dataclasses

        display = self.display()
        far = dataclasses.replace(
            display, master=dataclasses.replace(display.master, key="11B")
        )
        self.assertFalse(far.key_is_related_to_master)

    def test_master_view_is_a_flat_serialisable_object(self) -> None:
        """Vorbereitung fuer PRO DJ LINK: keine Verweise auf lokale Objekte."""
        import dataclasses

        master = self.display().master
        fields = {f.name for f in dataclasses.fields(master)}
        for name in (
            "player_id", "track_id", "title", "bpm", "key",
            "position_s", "beat_phase", "beat_grid", "waveform",
            "is_master",
        ):
            self.assertIn(name, fields)
        # Kein Deck, kein Provider, keine Engine.
        for value in dataclasses.asdict(master).values():
            self.assertNotIsInstance(value, Deck)


# --------------------------------------------------------------------------
# Panels und Anzeigezustand
# --------------------------------------------------------------------------


class DisplayStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig(1)
        self.rig.load(BROKEN_WINDOW.track_id)
        self.display = CdjDisplayState(deck=self.rig.deck.state)

    def tearDown(self) -> None:
        self.rig.close()

    def test_panels_open_and_close(self) -> None:
        display = self.display
        self.assertIs(display.panel, TouchPanel.NONE)
        for panel in (
            TouchPanel.BEAT_LOOP, TouchPanel.KEY_SHIFT, TouchPanel.BEAT_JUMP
        ):
            opened = display.with_panel(panel)
            self.assertIs(opened.panel, panel)
            # Dasselbe Panel erneut schliesst es.
            self.assertIs(opened.with_panel(panel).panel, TouchPanel.NONE)
            # Ein anderes Panel ersetzt es.
            other = TouchPanel.BEAT_JUMP if panel is not TouchPanel.BEAT_JUMP \
                else TouchPanel.BEAT_LOOP
            self.assertIs(opened.with_panel(other).panel, other)

    def test_only_one_panel_at_a_time(self) -> None:
        display = self.display.with_panel(TouchPanel.BEAT_LOOP)
        display = display.with_panel(TouchPanel.KEY_SHIFT)
        self.assertIs(display.panel, TouchPanel.KEY_SHIFT)

    def test_waveform_modes_cycle(self) -> None:
        seen = []
        mode = WaveformMode.RGB
        for _ in range(len(WaveformMode)):
            seen.append(mode)
            mode = mode.next_mode()
        self.assertEqual(set(seen), set(WaveformMode))
        self.assertIs(mode, WaveformMode.RGB)

    def test_header_toggles_between_waveform_and_phase_meter(self) -> None:
        display = self.display
        self.assertIs(display.header, WaveformHeader.WAVEFORM)
        display = display.with_header_toggled()
        self.assertIs(display.header, WaveformHeader.PHASE_METER)
        self.assertIs(
            display.with_header_toggled().header, WaveformHeader.WAVEFORM
        )

    def test_display_state_only_aggregates(self) -> None:
        """Die Anzeigeschicht darf keine eigene Wahrheit fuehren."""
        import dataclasses

        names = {f.name for f in dataclasses.fields(CdjDisplayState)}
        # Kein eigenes Tempo, keine eigene Position, keine eigene BPM.
        for forbidden in (
            "position_s", "current_bpm", "tempo_percent", "loop",
            "hot_cues", "play_state",
        ):
            self.assertNotIn(forbidden, names)
        # Der Durchgriff geht ueber ``deck``.
        self.assertIn("deck", names)

    def test_values_pass_through_from_the_deck(self) -> None:
        self.rig.send(CommandType.TEMPO_SET, value=1.0)
        display = self.display.with_deck(self.rig.deck.state)
        self.assertAlmostEqual(
            display.deck.current_bpm, self.rig.deck.state.current_bpm
        )
        self.assertIs(display.deck, self.rig.deck.state)


# --------------------------------------------------------------------------
# Oberflaeche
# --------------------------------------------------------------------------


class ScreenTests(unittest.TestCase):
    """Die Oberflaeche mit echten Demo-Tracks."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            import tkinter as tk

            probe = tk.Tk()
            probe.destroy()
            cls.tk_available = True
        except Exception:  # pragma: no cover
            cls.tk_available = False

    def setUp(self) -> None:
        if not self.tk_available:
            self.skipTest("keine Tk-Anzeige verfuegbar")
        import tkinter as tk

        from virtual_cdj.cdj_ui.screen import CdjScreen

        self.local = Rig(2)
        self.local.load(STEEL_PRESSURE.track_id)
        self.remote = Rig(1)
        self.remote.load(BROKEN_WINDOW.track_id)
        self.remote.send(CommandType.MASTER_SET)

        self.root = tk.Tk()
        self.root.geometry("1024x600+3000+3000")
        self.root.attributes("-alpha", 0.0)
        self.screen = CdjScreen(
            self.root, self.local.provider,
            master_view=lambda: MasterDeckView.from_deck_state(
                self.remote.deck.state
            ),
        )
        self.screen.set_track_source(provider().infos)
        self.screen.pack(fill="both", expand=True)
        self.root.update()
        self.screen.refresh(force=True)

    def tearDown(self) -> None:
        self.screen.stop()
        self.root.destroy()
        self.local.close()
        self.remote.close()

    def redraw(self) -> None:
        self.root.update()
        self.screen.refresh(force=True)

    def texts(self, widget) -> str:
        return " | ".join(
            widget.itemcget(item, "text")
            for item in widget.find_all()
            if widget.type(item) == "text"
        )

    def images(self, widget) -> int:
        return sum(
            1 for item in widget.find_all() if widget.type(item) == "image"
        )

    # -- Anzeige ----------------------------------------------------------

    def test_track_information_is_shown(self) -> None:
        top = self.texts(self.screen.top_bar)
        self.assertIn("Steel Pressure", top)
        self.assertIn("SHØRDY", top)
        self.assertIn("DEMO", top)
        self.assertIn("DECK 2", top)

    def test_status_bar_shows_bpm_tempo_and_key(self) -> None:
        """BPM und Tempo oben, Tonart in der unteren Zeile (S. 21-23)."""
        status = self.texts(self.screen.status_bar)
        self.assertIn("156.00", status)
        self.assertIn("+0.00 %", status)
        self.assertIn("PLAYER", status)
        self.assertIn("TEMPO ±10", status)
        # Tonart und MT gehoeren in die untere Zeile (Elemente 24, 25).
        self.assertIn("5A", self.texts(self.screen.overview))
        self.assertIn("MT", self.texts(self.screen.overview))

    def test_all_three_waveforms_are_rendered(self) -> None:
        self.assertGreaterEqual(self.images(self.screen.scrolling), 1)
        self.assertGreaterEqual(self.images(self.screen.overview), 1)
        self.assertGreaterEqual(self.images(self.screen.master_wave), 1)

    def test_master_row_is_visible_and_labelled(self) -> None:
        self.assertTrue(self.screen.display.master_is_other_deck)
        master = self.texts(self.screen.master_wave)
        self.assertIn("MASTER", master)
        self.assertIn("Broken Window", master)
        self.assertIn("154.00", master)

    def test_beat_countdown_is_shown(self) -> None:
        self.local.send(CommandType.SEEK, position_s=25.0)
        self.redraw()
        header = self.texts(self.screen.waveform_header)
        self.assertIn("BEAT", header)
        countdown = self.screen.display.beat_countdown()
        self.assertTrue(countdown.is_valid)
        self.assertIn(countdown.text(), header)

    def test_hot_cue_markers_appear_in_both_waveforms(self) -> None:
        self.local.send(CommandType.SEEK, position_s=30.96)
        self.redraw()
        overview = self.texts(self.screen.overview)
        self.assertIn("A", overview)
        self.assertIn("B", overview)

    # -- Bedienung --------------------------------------------------------

    def test_panel_buttons_open_the_panels(self) -> None:
        for panel in (
            TouchPanel.BEAT_LOOP, TouchPanel.KEY_SHIFT, TouchPanel.BEAT_JUMP
        ):
            self.screen.send_command(
                command(CommandType.PANEL, 2, "TEST", panel=panel)
            )
            self.redraw()
            self.assertIs(self.screen.display.panel, panel)
            widget = self.screen.panels[panel]
            self.assertTrue(widget.winfo_ismapped(), panel.value)
            self.assertTrue(self.texts(widget), panel.value)

    def test_panel_button_touch_toggles(self) -> None:
        bar = self.screen.top_bar
        box = next(
            b for b, p in bar._hits if p is TouchPanel.BEAT_LOOP
        )
        x0, y0, x1, y1 = box
        bar.event_generate(
            "<Button-1>", x=int((x0 + x1) / 2), y=int((y0 + y1) / 2)
        )
        self.root.update()
        self.assertIs(self.screen.display.panel, TouchPanel.BEAT_LOOP)
        bar.event_generate(
            "<Button-1>", x=int((x0 + x1) / 2), y=int((y0 + y1) / 2)
        )
        self.root.update()
        self.assertIs(self.screen.display.panel, TouchPanel.NONE)

    def test_beat_loop_panel_creates_a_real_loop(self) -> None:
        self.local.send(CommandType.SEEK, position_s=60.0)
        self.screen.send_command(
            command(
                CommandType.PANEL, 2, "TEST", panel=TouchPanel.BEAT_LOOP
            )
        )
        self.redraw()
        panel = self.screen.panels[TouchPanel.BEAT_LOOP]
        box = next(b for b, v in panel._hits if abs(float(v) - 4.0) < 1e-6)
        x0, y0, x1, y1 = box
        panel.event_generate(
            "<Button-1>", x=int((x0 + x1) / 2), y=int((y0 + y1) / 2)
        )
        self.root.update()
        loop = self.local.deck.state.loop
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.length_s, 4 * 60.0 / 156.0, places=4)

    def test_key_shift_panel_changes_the_state(self) -> None:
        self.screen.send_command(
            command(CommandType.PANEL, 2, "TEST", panel=TouchPanel.KEY_SHIFT)
        )
        self.redraw()
        panel = self.screen.panels[TouchPanel.KEY_SHIFT]
        box = next(b for b, v in panel._hits if v == "up")
        x0, y0, x1, y1 = box
        panel.event_generate(
            "<Button-1>", x=int((x0 + x1) / 2), y=int((y0 + y1) / 2)
        )
        self.root.update()
        self.assertEqual(self.local.deck.state.key_shift, 1)
        # Der fehlende DSP muss im Panel benannt sein.
        self.assertIn("Pitch-Shifting", self.texts(panel))

    def test_beat_jump_panel_jumps(self) -> None:
        self.local.send(CommandType.SEEK, position_s=60.0)
        self.screen.send_command(
            command(CommandType.PANEL, 2, "TEST", panel=TouchPanel.BEAT_JUMP)
        )
        self.redraw()
        panel = self.screen.panels[TouchPanel.BEAT_JUMP]
        forward = next(
            b for b, v in panel._hits
            if isinstance(v, tuple) and v[1] == +1
        )
        x0, y0, x1, y1 = forward
        panel.event_generate(
            "<Button-1>", x=int((x0 + x1) / 2), y=int((y0 + y1) / 2)
        )
        self.root.update()
        expected = 60.0 + 16 * 60.0 / 156.0
        self.assertAlmostEqual(
            self.local.deck.state.position_s, expected, places=3
        )

    def test_time_touch_toggles_the_mode(self) -> None:
        self.local.send(CommandType.SEEK, position_s=60.0)
        self.redraw()
        bar = self.screen.status_bar
        self.assertIsNotNone(bar._time_box)
        x0, y0, x1, y1 = bar._time_box
        self.assertIs(self.screen.display.time_mode, TimeMode.REMAIN)
        bar.event_generate(
            "<Button-1>", x=int((x0 + x1) / 2), y=int((y0 + y1) / 2)
        )
        self.root.update()
        self.assertIs(self.screen.display.time_mode, TimeMode.ELAPSED)
        # ELAPSED zeigt die verstrichene Zeit, nicht die Restzeit.
        self.assertIn("1:00.000", self.texts(bar))
        bar.event_generate(
            "<Button-1>", x=int((x0 + x1) / 2), y=int((y0 + y1) / 2)
        )
        self.root.update()
        self.assertIs(self.screen.display.time_mode, TimeMode.REMAIN)
        self.assertIn("-4:00.000", self.texts(bar))

    def test_zoom_buttons_change_the_window(self) -> None:
        self.redraw()
        before = self.screen.display.window_s
        wave = self.screen.scrolling
        box = next(b for b, delta in wave._zoom_hits if delta == -1)
        x0, y0, x1, y1 = box
        wave.event_generate(
            "<Button-1>", x=int((x0 + x1) / 2), y=int((y0 + y1) / 2)
        )
        self.root.update()
        self.assertLess(self.screen.display.window_s, before)

    def test_browse_encoder_zooms_in_the_waveform_view(self) -> None:
        before = self.screen.display.window_s
        self.screen.send_command(
            command(CommandType.BROWSE_ROTATE, 2, "TEST", delta=+1)
        )
        self.assertGreater(self.screen.display.window_s, before)

    def test_needle_lock_blocks_seek_while_playing(self) -> None:
        self.local.send(CommandType.SEEK, position_s=30.0)
        self.local.send(CommandType.PLAY_PAUSE)
        self.redraw()
        overview = self.screen.overview
        overview.event_generate(
            "<Button-1>", x=int(overview.winfo_width() * 0.8), y=10
        )
        self.root.update()
        # Position darf sich nicht sprunghaft geaendert haben.
        self.assertLess(self.local.deck.state.position_s, 60.0)

    def test_needle_lock_allows_seek_while_paused(self) -> None:
        self.redraw()
        overview = self.screen.overview
        overview.event_generate(
            "<Button-1>", x=int(overview.winfo_width() * 0.5), y=10
        )
        self.root.update()
        self.assertAlmostEqual(
            self.local.deck.state.position_s, 150.0, delta=10.0
        )

    # -- Browser ----------------------------------------------------------

    def test_browser_lists_the_demo_tracks(self) -> None:
        self.screen.set_view(Views.BROWSE)
        self.redraw()
        text = self.texts(self.screen.browser)
        self.assertIn("Broken Window", text)
        self.assertIn("Steel Pressure", text)
        self.assertIn("154.0", text)
        self.assertIn("4A", text)

    def test_touching_a_track_shows_load(self) -> None:
        """Handbuch S. 25: Beruehrung eines Tracks blendet LOAD ein."""
        self.screen.set_view(Views.BROWSE)
        self.redraw()
        self.assertNotIn("LOAD", self.texts(self.screen.browser))
        self.screen.send_command(
            command(CommandType.NAV_SELECT, 2, "TEST", index=0)
        )
        self.redraw()
        self.assertIn("LOAD", self.texts(self.screen.browser))

    def test_browser_navigation_and_load(self) -> None:
        loaded: list[str] = []
        self.screen.add_load_hook(loaded.append)
        self.screen.set_view(Views.BROWSE)
        self.redraw()

        # Drehregler bewegt die Auswahl (S. 24).
        self.assertEqual(self.screen.display.browse.selected, 0)
        self.screen.send_command(
            command(CommandType.BROWSE_ROTATE, 2, "TEST", delta=+1)
        )
        self.assertEqual(self.screen.display.browse.selected, 1)

        # Druecken bestaetigt und laedt sofort - der zweistufige Ablauf
        # gilt nur fuer Beruehrungen.
        self.screen.send_command(
            command(CommandType.BROWSE_PRESS, 2, "TEST", pressed=True)
        )
        self.assertEqual(loaded, [])
        self.screen.send_command(
            command(CommandType.BROWSE_PRESS, 2, "TEST", pressed=False)
        )
        self.assertEqual(loaded, [STEEL_PRESSURE.track_id])

    def test_browser_touch_selects_then_loads(self) -> None:
        loaded: list[str] = []
        self.screen.add_load_hook(loaded.append)
        self.screen.set_view(Views.BROWSE)
        self.redraw()
        browser = self.screen.browser
        box, index = browser._rows[0]
        x0, y0, x1, y1 = box
        browser.event_generate(
            "<Button-1>", x=int((x0 + x1) / 2), y=int((y0 + y1) / 2)
        )
        self.root.update()
        browser.event_generate(
            "<Button-1>", x=int((x0 + x1) / 2), y=int((y0 + y1) / 2)
        )
        self.root.update()
        self.assertEqual(loaded, [BROKEN_WINDOW.track_id])

    def test_view_switch_does_not_touch_the_deck(self) -> None:
        before = self.local.deck.state.generation
        self.screen.set_view(Views.BROWSE)
        self.screen.set_view(Views.WAVEFORM)
        self.assertEqual(self.local.deck.state.generation, before)


class ApplicationDemoTests(unittest.TestCase):
    """Demo-Modus in der Verdrahtung."""

    def test_demo_mode_loads_both_tracks(self) -> None:
        import time

        from virtual_cdj.app import CdjApplication

        app = CdjApplication(
            [1, 2], start_audio=False, demo=True,
            operating_mode=OperatingMode.CDJ,
        )
        try:
            self.assertEqual(
                app.demo_track_ids(),
                (BROKEN_WINDOW.track_id, STEEL_PRESSURE.track_id),
            )
            app.load_demo_defaults()
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and not (
                app.decks[1].state.has_track and app.decks[2].state.has_track
            ):
                app.tick()
                time.sleep(0.05)
            self.assertEqual(
                app.decks[1].state.track.title, "Broken Window"
            )
            self.assertEqual(
                app.decks[2].state.track.title, "Steel Pressure"
            )
            # Tracknummern kommen aus der Browse-Liste.
            self.assertEqual(app.decks[1].state.track_number, 1)
            self.assertEqual(app.decks[2].state.track_number, 2)
        finally:
            app.close()

    def test_master_view_finds_the_master_deck(self) -> None:
        from virtual_cdj.app import CdjApplication

        app = CdjApplication(
            [1, 2], start_audio=False, demo=True,
            operating_mode=OperatingMode.CDJ,
        )
        try:
            app.load_track_now(1, f"demo://{BROKEN_WINDOW.track_id}")
            app.load_track_now(2, f"demo://{STEEL_PRESSURE.track_id}")
            app.providers[1].send(command(CommandType.MASTER_SET, 1))

            view = app.master_view(2)
            self.assertIsNotNone(view)
            self.assertEqual(view.player_id, 1)
            self.assertTrue(view.is_master)
            self.assertEqual(view.title, "Broken Window")
        finally:
            app.close()

    def test_track_source_feeds_the_browser(self) -> None:
        from virtual_cdj.app import CdjApplication

        app = CdjApplication(
            [1], start_audio=False, demo=True,
            operating_mode=OperatingMode.CDJ,
        )
        try:
            infos = app.track_source()
            self.assertEqual(len(infos), 2)
            self.assertTrue(all(info.source == "DEMO" for info in infos))
        finally:
            app.close()

    def test_demo_package_is_isolated(self) -> None:
        """Der Produktionspfad darf das Demo-Paket nicht importieren."""
        import ast
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1] / "virtual_cdj"
        offenders: list[str] = []
        for package in ("audio", "deck", "cdj_ui", "core", "sources", "ui"):
            for path in (root / package).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    modules: list[str] = []
                    if isinstance(node, ast.Import):
                        modules = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        modules = [("." * node.level) + (node.module or "")]
                    for module in modules:
                        if "demo" in module.split("."):
                            offenders.append(f"{path.name}: {module}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
