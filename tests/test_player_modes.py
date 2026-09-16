"""Quantize, Slip und Jog Mode - die Testfaelle der Abschnitte 41-50.

Geprueft wird ausschliesslich ueber ``DeckCommand``: denselben Weg, den ein
Tastendruck am virtuellen Bedienfeld und spaeter die echte Hardware nehmen.
Keine Oberflaeche, kein Audiogeraet, keine erfundene Zwischenschicht.

Die Uhr wird gestellt, nicht abgewartet - jeder Zeitwert im Test ist damit
reproduzierbar.
"""

from __future__ import annotations

import unittest

from virtual_cdj.core import ids
from virtual_cdj.core.input_layer import InputLayer
from virtual_cdj.deck.commands import CommandType, command
from virtual_cdj.deck.engine import (
    JOG_BEND_SECONDS_PER_REV,
    Deck,
)
from virtual_cdj.deck.loop import LoopEngine
from virtual_cdj.deck.mapping import InputMapper
from virtual_cdj.deck.quantize import (
    DEFAULT_QUANTIZE_BEATS,
    QUANTIZE_BEAT_VALUES,
    beat_value_label,
    next_beat_value,
    normalise_beat_value,
    quantize_position,
)
from virtual_cdj.deck.slip import SlipEngine, SlipReason, SlipState
from virtual_cdj.deck.state import (
    BeatGrid,
    Direction,
    HotCue,
    JogMode,
    LoopExitReason,
    PadMode,
    PlayState,
    TrackInfo,
)
from virtual_cdj.jog import STEPS_PER_REV

BPM = 120.0
#: Ein Beat bei 120 BPM.
BEAT_S = 0.5
FRAME_S = 0.01


def make_track(**overrides) -> TrackInfo:
    defaults = dict(
        track_id="t1",
        title="Testtrack",
        duration_s=600.0,
        original_bpm=BPM,
        beat_grid=BeatGrid(first_beat_s=0.0, bpm=BPM, beats_per_bar=4),
    )
    defaults.update(overrides)
    return TrackInfo(**defaults)


class Rig:
    """Ein Deck mit gestellter Uhr und vollstaendiger Eingabekette."""

    def __init__(self, track: TrackInfo | None = None) -> None:
        self.now = 0.0
        self.deck = Deck(1, time_source=lambda: self.now)
        self.deck.load_track(track if track is not None else make_track())
        self.layer = InputLayer()
        self.mapper = InputMapper(1, self.deck.execute)
        self.layer.subscribe(self.mapper.handle_event)

    # -- Zustand ---------------------------------------------------------

    @property
    def state(self):
        return self.deck.state

    @property
    def loop(self):
        return self.deck.state.loop

    @property
    def position(self) -> float:
        return self.deck.state.position_s

    @property
    def background(self) -> float | None:
        return self.deck.state.slip_position_s

    # -- Bedienung -------------------------------------------------------

    def send(self, command_type: CommandType, **params) -> None:
        self.deck.execute(command(command_type, 1, "TEST", **params))

    def tap(self, control_id: str) -> None:
        """Ueber die echte Eingabekette: Input -> Mapper -> Kommando."""
        self.layer.press(control_id)
        self.layer.release(control_id)

    def seek(self, position_s: float) -> None:
        self.send(CommandType.SEEK, position_s=position_s)

    def play(self) -> None:
        if not self.state.is_playing:
            self.send(CommandType.PLAY_PAUSE)

    def pause(self) -> None:
        if self.state.is_playing:
            self.send(CommandType.PLAY_PAUSE)

    def touch(self, pressed: bool) -> None:
        self.send(CommandType.JOG_TOUCH, pressed=pressed)

    def turn(self, revolutions: float) -> None:
        self.send(
            CommandType.JOG_MOVE,
            delta=int(round(revolutions * STEPS_PER_REV)),
            ticks_per_rev=STEPS_PER_REV,
        )

    def beat_loop(self, beats: float) -> None:
        self.send(CommandType.BEAT_LOOP, beats=beats)

    def pad(self, index: int, pressed: bool = True) -> None:
        self.send(CommandType.PAD, index=index, pressed=pressed)

    def set_jog_mode(self, mode: JogMode) -> None:
        if self.state.jog_mode is not mode:
            self.send(CommandType.JOG_MODE_TOGGLE)

    def set_quantize(self, on: bool) -> None:
        if self.state.quantize is not on:
            self.send(CommandType.QUANTIZE_TOGGLE)

    def set_slip(self, on: bool) -> None:
        if self.state.slip is not on:
            self.send(CommandType.SLIP_TOGGLE)

    # -- Zeit ------------------------------------------------------------

    def run(self, seconds: float, step: float = FRAME_S) -> None:
        for _ in range(int(round(seconds / step))):
            self.now += step
            self.deck.tick()

    def scratch(self, seconds: float, step: float = FRAME_S) -> None:
        """Beruehrte Platte hin und her bewegen - ein echter Scratch.

        Die Bewegung ist bewusst unsymmetrisch, damit die hoerbare Position
        am Ende **nicht** zufaellig dort steht, wo sie ohne Scratch waere.
        """
        steps = int(round(seconds / step))
        for index in range(steps):
            self.now += step
            self.deck.tick()
            self.turn(0.25 if index % 3 else -0.5)


# --------------------------------------------------------------------------
# Abschnitt 41: QUANTIZE
# --------------------------------------------------------------------------


class QuantizeFunctionTests(unittest.TestCase):
    """Die zentrale Rasterfunktion allein - ohne Deck."""

    def setUp(self) -> None:
        self.grid = BeatGrid(first_beat_s=0.0, bpm=BPM, beats_per_bar=4)

    def test_without_a_grid_the_position_is_untouched(self) -> None:
        self.assertAlmostEqual(quantize_position(10.3, None, 1.0), 10.3)
        self.assertAlmostEqual(
            quantize_position(10.3, BeatGrid(), 1.0), 10.3
        )

    def test_one_beat_snaps_to_the_nearest_beat(self) -> None:
        self.assertAlmostEqual(quantize_position(10.1, self.grid, 1.0), 10.0)
        self.assertAlmostEqual(quantize_position(10.4, self.grid, 1.0), 10.5)

    def test_exactly_halfway_the_later_point_wins(self) -> None:
        """Dieselbe Regel wie bisher - ein Druck kurz vor dem Beat faellt
        nicht zurueck."""
        self.assertAlmostEqual(quantize_position(10.25, self.grid, 1.0), 10.5)

    def test_q6_every_supported_beat_value(self) -> None:
        # Abschnitt 41/Q6: 1/8, 1/4, 1/2 und 1 Beat.
        expected = {
            0.125: (10.125, 10.3125),
            0.25: (10.125, 10.25),
            0.5: (10.0, 10.25),
            1.0: (10.0, 10.5),
        }
        self.assertEqual(set(expected), set(QUANTIZE_BEAT_VALUES))
        for beats, (at_10_1, at_10_3) in expected.items():
            with self.subTest(beats=beats):
                self.assertAlmostEqual(
                    quantize_position(10.1, self.grid, beats), at_10_1
                )
                self.assertAlmostEqual(
                    quantize_position(10.3, self.grid, beats), at_10_3
                )

    def test_a_position_already_on_the_raster_does_not_move(self) -> None:
        for beats in QUANTIZE_BEAT_VALUES:
            with self.subTest(beats=beats):
                self.assertAlmostEqual(
                    quantize_position(10.0, self.grid, beats), 10.0
                )

    def test_before_the_first_beat_nothing_is_invented(self) -> None:
        grid = BeatGrid(first_beat_s=5.0, bpm=BPM)
        self.assertAlmostEqual(quantize_position(1.0, grid, 0.25), 1.0)

    def test_an_explicit_beat_list_is_used(self) -> None:
        grid = BeatGrid(beats_s=(0.0, 0.4, 1.0, 1.8), beats_per_bar=4)
        # Beat 1 liegt bei 0.4, Beat 2 bei 1.0 - halbe Beats also bei 0.7.
        self.assertAlmostEqual(quantize_position(0.68, grid, 0.5), 0.7)

    def test_beat_values_cycle_and_normalise(self) -> None:
        self.assertEqual(next_beat_value(0.125), 0.25)
        self.assertEqual(next_beat_value(1.0), 0.125)
        self.assertEqual(normalise_beat_value(0.3), 0.25)
        self.assertEqual(beat_value_label(0.125), "1/8")
        self.assertEqual(beat_value_label(1.0), "1")


class QuantizeDeckTests(unittest.TestCase):
    """Q1-Q6 am Deck, ueber Kommandos."""

    def setUp(self) -> None:
        self.rig = Rig()
        # Genau zwischen zwei Beats: Beat 20 bei 10.0, Beat 21 bei 10.5.
        self.between = 10.2

    # -- Q1 --------------------------------------------------------------

    def test_q1_cue_without_quantize_lands_on_the_exact_position(self) -> None:
        self.rig.set_quantize(False)
        self.rig.seek(self.between)
        self.rig.send(CommandType.CUE, pressed=True)
        self.rig.send(CommandType.CUE, pressed=False)
        self.assertAlmostEqual(self.rig.state.cue_point_s, self.between)

    def test_q1b_hot_cue_without_quantize_is_exact(self) -> None:
        self.rig.set_quantize(False)
        self.rig.seek(self.between)
        self.rig.pad(0)
        cue = self.rig.state.track.hot_cue(0)
        self.assertIsNotNone(cue)
        self.assertAlmostEqual(cue.position_s, self.between)

    def test_q1c_loop_points_without_quantize_are_exact(self) -> None:
        self.rig.set_quantize(False)
        self.rig.seek(self.between)
        self.rig.send(CommandType.LOOP_IN)
        self.rig.seek(self.between + 1.1)
        self.rig.send(CommandType.LOOP_OUT)
        self.assertAlmostEqual(self.rig.loop.in_s, self.between)
        self.assertAlmostEqual(self.rig.loop.out_s, self.between + 1.1)

    # -- Q2 --------------------------------------------------------------

    def test_q2_cue_with_quantize_lands_on_the_grid(self) -> None:
        self.rig.set_quantize(True)
        self.rig.seek(self.between)
        self.rig.send(CommandType.CUE, pressed=True)
        self.rig.send(CommandType.CUE, pressed=False)
        self.assertAlmostEqual(self.rig.state.cue_point_s, 10.0)

    def test_q2b_hot_cue_with_quantize_lands_on_the_grid(self) -> None:
        self.rig.set_quantize(True)
        self.rig.seek(self.between)
        self.rig.pad(0)
        self.assertAlmostEqual(self.rig.state.track.hot_cue(0).position_s, 10.0)

    def test_q2c_the_beat_value_reaches_cue_and_hot_cue(self) -> None:
        """Cue und Hotcue benutzen dieselbe Rasterweite wie die Loops.

        Frueher rasteten sie ueber eine eigene Rundung fest auf ganze Beats;
        eine Einstellung von 1/8 wirkte dort nicht.
        """
        self.rig.set_quantize(True)
        self.rig.send(CommandType.QUANTIZE_BEATS, beats=0.125)
        self.rig.seek(10.1)
        self.rig.pad(0)
        self.assertAlmostEqual(
            self.rig.state.track.hot_cue(0).position_s, 10.125
        )

    # -- Q3 --------------------------------------------------------------

    def test_q3_loop_in_and_out_with_quantize_are_on_the_grid(self) -> None:
        self.rig.set_quantize(True)
        self.rig.seek(10.2)
        self.rig.send(CommandType.LOOP_IN)
        self.rig.seek(12.3)
        self.rig.send(CommandType.LOOP_OUT)
        loop = self.rig.loop
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.in_s, 10.0)
        self.assertAlmostEqual(loop.out_s, 12.5)
        for point in (loop.in_s, loop.out_s):
            self.assertAlmostEqual(point % BEAT_S, 0.0, places=9)

    def test_q3b_loop_points_follow_the_beat_value(self) -> None:
        self.rig.set_quantize(True)
        self.rig.send(CommandType.QUANTIZE_BEATS, beats=0.25)
        self.rig.seek(10.1)
        self.rig.send(CommandType.LOOP_IN)
        self.assertAlmostEqual(self.rig.loop.in_s, 10.125)

    # -- Q4 --------------------------------------------------------------

    def test_q4_a_four_beat_loop_is_exactly_four_beats(self) -> None:
        for quantize in (False, True):
            with self.subTest(quantize=quantize):
                rig = Rig()
                rig.set_quantize(quantize)
                rig.seek(10.2)
                rig.beat_loop(4.0)
                loop = rig.loop
                self.assertTrue(loop.active)
                self.assertAlmostEqual(loop.beats, 4.0)
                self.assertAlmostEqual(loop.length_s, 4 * BEAT_S, places=9)

    def test_q4b_the_end_is_derived_from_the_start(self) -> None:
        """Abschnitt 9: erst ``in`` rasten, ``out`` daraus ableiten.

        Wuerden beide Punkte einzeln gerastet, waere ein 4-Beat-Loop je nach
        Startposition mal 3.5, mal 4.5 Beats lang.
        """
        self.rig.set_quantize(True)
        self.rig.seek(10.3)
        self.rig.beat_loop(4.0)
        loop = self.rig.loop
        self.assertAlmostEqual(loop.in_s, 10.5)
        self.assertAlmostEqual(loop.out_s, 10.5 + 4 * BEAT_S)

    def test_q4c_every_pad_length_stays_exact(self) -> None:
        self.rig.set_quantize(True)
        self.rig.send(CommandType.PAD_MODE, mode=PadMode.BEAT_LOOP)
        for index, beats in enumerate((0.25, 0.5, 1.0, 2.0, 4.0, 8.0)):
            with self.subTest(beats=beats):
                rig = Rig()
                rig.set_quantize(True)
                rig.send(CommandType.PAD_MODE, mode=PadMode.BEAT_LOOP)
                rig.seek(10.3)
                rig.pad(index)
                self.assertAlmostEqual(rig.loop.beats, beats)
                self.assertAlmostEqual(
                    rig.loop.length_s, beats * BEAT_S, places=9
                )

    # -- Q5 --------------------------------------------------------------

    def test_q5_toggling_quantize_leaves_a_running_loop_alone(self) -> None:
        self.rig.set_quantize(False)
        self.rig.seek(10.3)
        self.rig.beat_loop(4.0)
        self.rig.play()
        before = self.rig.loop
        for _ in range(4):
            self.rig.send(CommandType.QUANTIZE_TOGGLE)
            self.rig.run(0.2)
            loop = self.rig.loop
            self.assertTrue(loop.active)
            self.assertAlmostEqual(loop.in_s, before.in_s, places=9)
            self.assertAlmostEqual(loop.out_s, before.out_s, places=9)
        self.assertIsNone(self.rig.loop.exit_reason)

    def test_q5b_changing_the_beat_value_leaves_a_running_loop_alone(
        self,
    ) -> None:
        self.rig.set_quantize(True)
        self.rig.seek(10.3)
        self.rig.beat_loop(4.0)
        self.rig.play()
        before = self.rig.loop
        for _ in range(len(QUANTIZE_BEAT_VALUES) + 1):
            self.rig.send(CommandType.QUANTIZE_BEATS)
            self.rig.run(0.2)
            self.assertTrue(self.rig.loop.active)
            self.assertAlmostEqual(self.rig.loop.in_s, before.in_s, places=9)
            self.assertAlmostEqual(self.rig.loop.out_s, before.out_s, places=9)

    # -- Q6 --------------------------------------------------------------

    def test_q6_the_beat_value_is_central_state(self) -> None:
        self.assertAlmostEqual(
            self.rig.state.quantize_beats, DEFAULT_QUANTIZE_BEATS
        )
        for beats in QUANTIZE_BEAT_VALUES:
            with self.subTest(beats=beats):
                self.rig.send(CommandType.QUANTIZE_BEATS, beats=beats)
                self.assertAlmostEqual(self.rig.state.quantize_beats, beats)

    def test_q6b_the_button_cycles_through_all_values(self) -> None:
        seen = [self.rig.state.quantize_beats]
        for _ in range(len(QUANTIZE_BEAT_VALUES) - 1):
            self.rig.send(CommandType.QUANTIZE_BEATS)
            seen.append(self.rig.state.quantize_beats)
        self.assertEqual(sorted(seen), sorted(QUANTIZE_BEAT_VALUES))
        self.rig.send(CommandType.QUANTIZE_BEATS)
        self.assertAlmostEqual(self.rig.state.quantize_beats, seen[0])

    def test_q6c_shift_and_quantize_reach_the_beat_value(self) -> None:
        """Ueber die echte Eingabekette, ohne Bildschirm."""
        before = self.rig.state.quantize_beats
        self.rig.layer.press(ids.SHIFT)
        self.rig.tap(ids.QUANTIZE)
        self.rig.layer.release(ids.SHIFT)
        self.assertNotAlmostEqual(self.rig.state.quantize_beats, before)
        # Ohne SHIFT bleibt es der normale Schalter.
        quantize_before = self.rig.state.quantize
        beats_before = self.rig.state.quantize_beats
        self.rig.tap(ids.QUANTIZE)
        self.assertIsNot(self.rig.state.quantize, quantize_before)
        self.assertAlmostEqual(self.rig.state.quantize_beats, beats_before)


class QuantizeScopeTests(unittest.TestCase):
    """Abschnitt 5/34: Quantize rundet nicht einfach alles."""

    def setUp(self) -> None:
        self.rig = Rig()
        self.rig.set_quantize(True)

    def test_the_playback_position_is_never_forced_onto_the_grid(self) -> None:
        self.rig.seek(10.0)
        self.rig.play()
        self.rig.run(0.17)
        position = self.rig.position
        self.assertAlmostEqual(position, 10.17, places=6)
        self.assertNotAlmostEqual(position % BEAT_S, 0.0, places=6)

    def test_pitch_bend_is_not_quantized(self) -> None:
        self.rig.seek(10.0)
        self.rig.play()
        self.rig.turn(0.5)  # Rand, keine Beruehrung
        self.assertAlmostEqual(
            self.rig.position, 10.0 + 0.5 * JOG_BEND_SECONDS_PER_REV, places=6
        )

    def test_scratching_is_not_quantized(self) -> None:
        self.rig.seek(10.0)
        self.rig.play()
        self.rig.touch(True)
        self.rig.turn(0.3)
        self.assertAlmostEqual(self.rig.position, 10.0 + 0.3 * BEAT_S, places=6)

    def test_a_jog_loop_adjust_is_not_quantized(self) -> None:
        self.rig.seek(10.0)
        self.rig.beat_loop(4.0)
        self.rig.send(CommandType.LOOP_IN)  # IN-Adjust ein
        self.rig.turn(0.3)
        self.assertAlmostEqual(self.rig.loop.in_s, 10.0 + 0.3 * BEAT_S, places=6)

    def test_without_a_beat_grid_nothing_is_quantized(self) -> None:
        rig = Rig(make_track(beat_grid=None))
        rig.set_quantize(True)
        rig.seek(10.2)
        rig.pad(0)
        self.assertAlmostEqual(rig.state.track.hot_cue(0).position_s, 10.2)

    def test_the_loop_engine_uses_the_central_function(self) -> None:
        """Keine zweite Rundung in der Loop-Engine."""
        grid = BeatGrid(first_beat_s=0.0, bpm=BPM)
        engine = LoopEngine(grid=grid, quantize=True, beat_value=0.125)
        self.assertAlmostEqual(
            engine.position_for(10.1), quantize_position(10.1, grid, 0.125)
        )
        off = LoopEngine(grid=grid, quantize=False, beat_value=0.125)
        self.assertAlmostEqual(off.position_for(10.1), 10.1)


# --------------------------------------------------------------------------
# Abschnitt 42: VINYL MODE
# --------------------------------------------------------------------------


class VinylModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig()
        self.rig.seek(30.0)
        self.assertIs(self.rig.state.jog_mode, JogMode.VINYL)

    # -- V1 --------------------------------------------------------------

    def test_v1_the_outer_ring_bends(self) -> None:
        self.rig.play()
        self.rig.turn(1.0)
        moved = self.rig.position - 30.0
        self.assertAlmostEqual(moved, JOG_BEND_SECONDS_PER_REV, places=6)
        self.assertLess(moved, BEAT_S)
        self.assertFalse(self.rig.state.jog_touch)

    # -- V2 --------------------------------------------------------------

    def test_v2_touching_the_platter_stops_the_playback(self) -> None:
        self.rig.play()
        self.rig.run(0.5)
        running = self.rig.position
        self.assertAlmostEqual(running, 30.5, places=6)

        self.rig.touch(True)
        self.rig.run(1.0)
        # Hoerbar steht der Track - die Zeit vergeht, die Position nicht.
        self.assertAlmostEqual(self.rig.position, running, places=6)
        # Die PLAY-Anzeige bleibt an, so wie am Geraet.
        self.assertIs(self.rig.state.play_state, PlayState.PLAYING)

    def test_v2b_a_null_playback_port_is_told_to_stop(self) -> None:
        port = RecordingPort()
        self.rig.deck.playback = port
        self.rig.play()
        self.rig.touch(True)
        self.assertEqual(port.playing, False)
        self.rig.touch(False)
        self.assertEqual(port.playing, True)

    # -- V3 --------------------------------------------------------------

    def test_v3_touch_and_rotation_scratch(self) -> None:
        self.rig.play()
        self.rig.touch(True)
        self.rig.turn(1.0)
        # Eine Umdrehung ist ein Beat und folgt dem Tempo des Tracks.
        self.assertAlmostEqual(self.rig.position, 30.0 + BEAT_S, places=6)
        self.rig.turn(-2.0)
        self.assertAlmostEqual(self.rig.position, 30.0 - BEAT_S, places=6)

    # -- V4 --------------------------------------------------------------

    def test_v4_releasing_resumes_normal_playback(self) -> None:
        self.rig.play()
        self.rig.touch(True)
        self.rig.turn(1.0)
        self.rig.run(0.5)
        held = self.rig.position
        self.rig.touch(False)
        self.rig.run(0.5)
        self.assertFalse(self.rig.state.jog_touch)
        self.assertAlmostEqual(self.rig.position, held + 0.5, places=6)
        self.assertIs(self.rig.state.play_state, PlayState.PLAYING)

    # -- V5 --------------------------------------------------------------

    def test_v5_frame_search_while_paused(self) -> None:
        self.assertIsNot(self.rig.state.play_state, PlayState.PLAYING)
        self.rig.touch(True)
        self.rig.turn(0.1)
        small = self.rig.position - 30.0
        self.assertAlmostEqual(small, 0.1 * BEAT_S, places=6)
        self.rig.turn(1.0)
        self.assertAlmostEqual(
            self.rig.position, 30.0 + 1.1 * BEAT_S, places=6
        )

    def test_v5b_frame_search_moves_the_real_player_position(self) -> None:
        self.rig.touch(True)
        self.rig.turn(4.0)
        self.assertAlmostEqual(self.rig.position, 30.0 + 4 * BEAT_S, places=6)
        self.rig.run(0.5)  # pausiert: die Position bleibt stehen
        self.assertAlmostEqual(self.rig.position, 30.0 + 4 * BEAT_S, places=6)


class RecordingPort:
    """Minimale Ausgabe, die nur mitschreibt, was das Deck ihr sagt."""

    is_ready = False
    position_seconds = 0.0
    duration_seconds = 0.0
    reached_end = False

    def __init__(self) -> None:
        self.playing: bool | None = None
        self.speed: float | None = None

    def load(self, samples) -> None: ...
    def unload(self) -> None: ...
    def seek_seconds(self, seconds: float) -> None: ...
    def nudge_seconds(self, seconds: float) -> None: ...
    def set_loop(self, active, start_s, end_s) -> None: ...

    def set_playing(self, playing: bool) -> None:
        self.playing = playing

    def set_speed(self, speed: float) -> None:
        self.speed = speed


# --------------------------------------------------------------------------
# Abschnitt 43: CDJ MODE
# --------------------------------------------------------------------------


class CdjModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig()
        self.rig.seek(30.0)
        self.rig.set_jog_mode(JogMode.CDJ)
        self.assertIs(self.rig.state.jog_mode, JogMode.CDJ)

    # -- C1 --------------------------------------------------------------

    def test_c1_turning_the_jog_bends(self) -> None:
        self.rig.play()
        self.rig.turn(1.0)
        self.assertAlmostEqual(
            self.rig.position, 30.0 + JOG_BEND_SECONDS_PER_REV, places=6
        )

    # -- C2 --------------------------------------------------------------

    def test_c2_touch_and_rotation_do_not_scratch(self) -> None:
        self.rig.play()
        self.rig.touch(True)
        self.rig.turn(1.0)
        moved = self.rig.position - 30.0
        self.assertAlmostEqual(moved, JOG_BEND_SECONDS_PER_REV, places=6)
        self.assertNotAlmostEqual(moved, BEAT_S, places=3)

    # -- C3 --------------------------------------------------------------

    def test_c3_touching_does_not_stop_the_playback(self) -> None:
        self.rig.play()
        self.rig.touch(True)
        self.rig.run(1.0)
        self.assertTrue(self.rig.state.jog_touch)
        self.assertIs(self.rig.state.play_state, PlayState.PLAYING)
        self.assertAlmostEqual(self.rig.position, 31.0, places=6)

    def test_c3b_the_output_keeps_running_too(self) -> None:
        port = RecordingPort()
        self.rig.deck.playback = port
        self.rig.play()
        self.rig.touch(True)
        self.assertEqual(port.playing, True)

    # -- C4 --------------------------------------------------------------

    def test_c4_frame_search_while_paused(self) -> None:
        self.assertIsNot(self.rig.state.play_state, PlayState.PLAYING)
        self.rig.turn(1.0)
        self.assertAlmostEqual(self.rig.position, 30.0 + BEAT_S, places=6)

    def test_c4b_frame_search_works_touched_and_untouched(self) -> None:
        self.rig.touch(True)
        self.rig.turn(1.0)
        self.assertAlmostEqual(self.rig.position, 30.0 + BEAT_S, places=6)


class JogModeStateTests(unittest.TestCase):
    """Abschnitt 25/30/32/33: der Modus ist zentraler Zustand."""

    def setUp(self) -> None:
        self.rig = Rig()

    def test_the_button_toggles_both_ways(self) -> None:
        self.assertIs(self.rig.state.jog_mode, JogMode.VINYL)
        self.rig.tap(ids.JOG_MODE)
        self.assertIs(self.rig.state.jog_mode, JogMode.CDJ)
        self.rig.tap(ids.JOG_MODE)
        self.assertIs(self.rig.state.jog_mode, JogMode.VINYL)

    def test_touch_is_reported_independently_of_the_mode(self) -> None:
        """Abschnitt 30: die Eingabeseite meldet Beruehrung in **beiden**
        Modi; erst das Deck entscheidet, was daraus wird."""
        for mode in (JogMode.VINYL, JogMode.CDJ):
            with self.subTest(mode=mode):
                self.rig.set_jog_mode(mode)
                self.rig.touch(True)
                self.assertTrue(self.rig.state.jog_touch)
                self.rig.touch(False)
                self.assertFalse(self.rig.state.jog_touch)

    def test_32_switching_while_playing_changes_nothing_else(self) -> None:
        self.rig.seek(30.0)
        self.rig.beat_loop(4.0)
        self.rig.play()
        self.rig.run(0.3)
        before = self.rig.state
        for _ in range(4):
            self.rig.send(CommandType.JOG_MODE_TOGGLE)
            after = self.rig.state
            self.assertIs(after.play_state, PlayState.PLAYING)
            self.assertAlmostEqual(after.current_bpm, before.current_bpm)
            self.assertAlmostEqual(after.tempo_percent, before.tempo_percent)
            self.assertTrue(after.loop.active)
            self.assertAlmostEqual(after.loop.in_s, before.loop.in_s)
            self.assertAlmostEqual(after.loop.out_s, before.loop.out_s)
            self.rig.run(0.2)

    def test_33_a_loop_survives_vinyl_cdj_vinyl(self) -> None:
        self.rig.seek(30.0)
        self.rig.beat_loop(4.0)
        self.rig.play()
        self.rig.set_jog_mode(JogMode.CDJ)
        self.rig.run(0.5)
        self.rig.set_jog_mode(JogMode.VINYL)
        self.rig.run(0.5)
        self.assertTrue(self.rig.loop.active)
        self.assertIsNone(self.rig.loop.exit_reason)

    def test_vinyl_speed_adjust_is_kept_and_not_duplicated(self) -> None:
        self.rig.send(CommandType.VINYL_SPEED_ADJUST, value=0.8)
        self.assertAlmostEqual(self.rig.state.vinyl_speed_adjust, 0.8)
        self.rig.send(CommandType.JOG_MODE_TOGGLE)
        self.assertAlmostEqual(self.rig.state.vinyl_speed_adjust, 0.8)


# --------------------------------------------------------------------------
# Abschnitte 11-24, 44-47: SLIP
# --------------------------------------------------------------------------


class SlipEngineTests(unittest.TestCase):
    """Die Slip-Rechnung allein - ohne Deck."""

    def setUp(self) -> None:
        self.engine = SlipEngine(duration_s=600.0)

    def test_an_empty_state_is_inactive(self) -> None:
        state = SlipState()
        self.assertFalse(state.active)
        self.assertIsNone(state.position_s)

    def test_begin_sets_the_background_once(self) -> None:
        first = self.engine.begin(SlipState(), SlipReason.LOOP, 30.0)
        self.assertTrue(first.active)
        self.assertAlmostEqual(first.position_s, 30.0)
        moved = self.engine.advance(first, 3.0)
        second = self.engine.begin(moved, SlipReason.SCRATCH, 12.0)
        # Die laufende Zeitachse wird nicht zurueckgesetzt.
        self.assertAlmostEqual(second.position_s, 33.0)
        self.assertEqual(
            second.reasons, {SlipReason.LOOP, SlipReason.SCRATCH}
        )

    def test_only_the_last_reason_jumps_back(self) -> None:
        state = self.engine.begin(SlipState(), SlipReason.LOOP, 30.0)
        state = self.engine.begin(state, SlipReason.SCRATCH, 30.0)
        state, resume = self.engine.end(state, SlipReason.SCRATCH)
        self.assertIsNone(resume)
        self.assertTrue(state.active)
        state, resume = self.engine.end(state, SlipReason.LOOP)
        self.assertAlmostEqual(resume, 30.0)
        self.assertFalse(state.active)

    def test_ending_a_reason_that_never_ran_does_nothing(self) -> None:
        state, resume = self.engine.end(SlipState(), SlipReason.PAUSE)
        self.assertIsNone(resume)
        self.assertEqual(state, SlipState())

    def test_the_background_never_leaves_the_track(self) -> None:
        engine = SlipEngine(duration_s=10.0)
        state = engine.begin(SlipState(), SlipReason.PAUSE, 9.0)
        self.assertAlmostEqual(engine.advance(state, 5.0).position_s, 10.0)

    def test_the_background_only_runs_forward(self) -> None:
        state = self.engine.begin(SlipState(), SlipReason.REVERSE, 30.0)
        self.assertAlmostEqual(self.engine.advance(state, -3.0).position_s, 30.0)

    def test_the_label_names_every_running_reason(self) -> None:
        state = self.engine.begin(SlipState(), SlipReason.LOOP, 1.0)
        state = self.engine.begin(state, SlipReason.SCRATCH, 1.0)
        self.assertEqual(state.label(), "LOOP+SCRATCH")


class SlipSwitchTests(unittest.TestCase):
    """Abschnitte 11/23: Schalter und laufende Aktion sind zweierlei."""

    def setUp(self) -> None:
        self.rig = Rig()

    def test_the_button_toggles(self) -> None:
        self.assertFalse(self.rig.state.slip)
        self.rig.tap(ids.SLIP)
        self.assertTrue(self.rig.state.slip)
        self.rig.tap(ids.SLIP)
        self.assertFalse(self.rig.state.slip)

    def test_enabled_alone_starts_no_operation(self) -> None:
        self.rig.seek(30.0)
        self.rig.play()
        self.rig.set_slip(True)
        self.rig.run(1.0)
        self.assertTrue(self.rig.state.slip)
        self.assertFalse(self.rig.state.slip_active)
        self.assertIsNone(self.rig.background)
        self.assertAlmostEqual(self.rig.position, 31.0, places=6)

    def test_a_stopped_deck_gets_no_background_timeline(self) -> None:
        self.rig.seek(30.0)
        self.rig.set_slip(True)
        self.rig.touch(True)
        self.assertFalse(self.rig.state.slip_active)

    def test_switching_slip_off_ends_the_operation_without_a_jump(self) -> None:
        self.rig.seek(30.0)
        self.rig.play()
        self.rig.set_slip(True)
        self.rig.touch(True)
        self.assertTrue(self.rig.state.slip_active)
        self.rig.run(2.0)
        audible = self.rig.position
        self.rig.set_slip(False)
        self.assertFalse(self.rig.state.slip_active)
        self.assertIsNone(self.rig.background)
        self.assertAlmostEqual(self.rig.position, audible, places=6)


class SlipScratchTests(unittest.TestCase):
    """Abschnitt 17/35/44: Vinyl + Slip + Scratch."""

    def setUp(self) -> None:
        self.rig = Rig()
        self.rig.seek(30.0)
        self.rig.play()
        self.rig.set_slip(True)

    def test_44_three_seconds_of_scratching(self) -> None:
        self.rig.touch(True)
        self.assertTrue(self.rig.state.slip_active)
        self.assertAlmostEqual(self.rig.background, 30.0, places=6)

        self.rig.scratch(3.0)
        # Die Hintergrundzeit ist um genau drei Sekunden gewandert.
        self.assertAlmostEqual(self.rig.background, 33.0, places=3)
        # Hoerbar steht die Wiedergabe woanders - sonst pruefte der Test
        # nichts.
        self.assertNotAlmostEqual(self.rig.position, 33.0, places=2)

        self.rig.touch(False)
        self.assertAlmostEqual(self.rig.position, 33.0, places=3)
        self.assertFalse(self.rig.state.slip_active)
        self.assertIsNone(self.rig.background)

    def test_after_the_jump_playback_continues_normally(self) -> None:
        self.rig.touch(True)
        self.rig.scratch(3.0)
        self.rig.touch(False)
        self.rig.run(1.0)
        self.assertAlmostEqual(self.rig.position, 34.0, places=3)

    def test_without_slip_the_scratch_position_is_kept(self) -> None:
        self.rig.set_slip(False)
        self.rig.touch(True)
        self.rig.scratch(3.0)
        scratched = self.rig.position
        self.rig.touch(False)
        self.assertAlmostEqual(self.rig.position, scratched, places=6)

    def test_36_in_cdj_mode_the_platter_starts_no_scratch_slip(self) -> None:
        """Abschnitt 36: kein Vinyl-Scratch, aber SLIP bleibt eingeschaltet."""
        self.rig.set_jog_mode(JogMode.CDJ)
        self.rig.touch(True)
        self.assertFalse(self.rig.state.slip_active)
        self.assertTrue(self.rig.state.slip)
        # Andere Slip-Aktionen gehen weiter.
        self.rig.beat_loop(4.0)
        self.assertTrue(self.rig.state.slip_active)

    def test_leaving_vinyl_mode_ends_a_running_scratch_slip(self) -> None:
        self.rig.touch(True)
        self.rig.run(2.0)
        self.rig.set_jog_mode(JogMode.CDJ)
        self.assertFalse(self.rig.state.slip_active)
        self.assertAlmostEqual(self.rig.position, 32.0, places=3)


class SlipPauseTests(unittest.TestCase):
    """Abschnitt 16/46: Vinyl + Slip + Pause."""

    def setUp(self) -> None:
        self.rig = Rig()
        self.rig.seek(30.0)
        self.rig.play()
        self.rig.set_slip(True)

    def test_46_a_five_second_pause(self) -> None:
        self.rig.pause()
        self.assertIs(self.rig.state.play_state, PlayState.PAUSED)
        self.assertTrue(self.rig.state.slip_active)
        self.rig.run(5.0)
        # Hoerbar steht der Track.
        self.assertAlmostEqual(self.rig.position, 30.0, places=6)
        # Im Hintergrund laeuft die Zeit.
        self.assertAlmostEqual(self.rig.background, 35.0, places=3)

        self.rig.play()
        self.assertAlmostEqual(self.rig.position, 35.0, places=3)
        self.assertFalse(self.rig.state.slip_active)
        self.rig.run(1.0)
        self.assertAlmostEqual(self.rig.position, 36.0, places=3)

    def test_without_slip_the_pause_keeps_the_position(self) -> None:
        self.rig.set_slip(False)
        self.rig.pause()
        self.rig.run(5.0)
        self.rig.play()
        self.assertAlmostEqual(self.rig.position, 30.0, places=6)

    def test_slip_pause_also_works_in_cdj_mode(self) -> None:
        self.rig.set_jog_mode(JogMode.CDJ)
        self.rig.pause()
        self.rig.run(5.0)
        self.rig.play()
        self.assertAlmostEqual(self.rig.position, 35.0, places=3)


class SlipLoopTests(unittest.TestCase):
    """Abschnitt 18/19/45: Slip + Loop und Slip + Beatloop."""

    def setUp(self) -> None:
        self.rig = Rig()
        self.rig.seek(10.0)
        self.rig.play()
        self.rig.set_slip(True)

    def test_45_a_four_beat_loop_over_several_repetitions(self) -> None:
        self.rig.beat_loop(4.0)
        loop = self.rig.loop
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.length_s, 2.0, places=9)
        self.assertTrue(self.rig.state.slip_active)
        self.assertAlmostEqual(self.rig.background, 10.0, places=6)

        self.rig.run(5.0)          # zweieinhalb Durchlaeufe
        self.assertTrue(self.rig.loop.active)
        # Hoerbar laeuft der Loop weiter.
        self.assertAlmostEqual(self.rig.position, 11.0, places=3)
        # Im Hintergrund waere der Track bei 15 s.
        self.assertAlmostEqual(self.rig.background, 15.0, places=3)

        self.rig.send(CommandType.RELOOP_EXIT)
        self.assertFalse(self.rig.loop.active)
        self.assertAlmostEqual(self.rig.position, 15.0, places=3)
        self.assertFalse(self.rig.state.slip_active)

    def test_the_loop_itself_stays_stable_during_slip(self) -> None:
        self.rig.beat_loop(4.0)
        before = self.rig.loop
        self.rig.run(20.0)
        loop = self.rig.loop
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.in_s, before.in_s, places=9)
        self.assertAlmostEqual(loop.out_s, before.out_s, places=9)
        self.assertGreaterEqual(self.rig.position, loop.in_s - 1e-6)
        self.assertLess(self.rig.position, loop.out_s + 1e-6)

    def test_19_beat_loop_pads_work_with_slip(self) -> None:
        self.rig.send(CommandType.PAD_MODE, mode=PadMode.BEAT_LOOP)
        self.rig.pad(4)                       # Pad E = 4 Beats
        self.assertTrue(self.rig.loop.active)
        self.assertTrue(self.rig.state.slip_active)
        self.rig.run(5.0)
        self.assertAlmostEqual(self.rig.background, 15.0, places=3)
        self.rig.pad(4)                       # dasselbe Pad verlaesst
        self.assertFalse(self.rig.loop.active)
        self.assertIs(self.rig.loop.exit_reason, LoopExitReason.BEAT_LOOP_PAD)
        self.assertAlmostEqual(self.rig.position, 15.0, places=3)

    def test_switching_slip_on_during_a_running_loop(self) -> None:
        rig = Rig()
        rig.seek(10.0)
        rig.play()
        rig.beat_loop(4.0)
        rig.run(1.0)
        self.assertFalse(rig.state.slip_active)
        rig.set_slip(True)
        self.assertTrue(rig.state.slip_active)
        start = rig.background
        rig.run(4.0)
        self.assertAlmostEqual(rig.background, start + 4.0, places=3)

    def test_scratching_inside_a_slip_loop_does_not_break_the_loop(
        self,
    ) -> None:
        """Abschnitt 18: der Loop muss waehrend Slip stabil bleiben."""
        self.rig.beat_loop(4.0)
        self.rig.touch(True)
        self.rig.scratch(2.0)
        self.rig.touch(False)
        # Der Loop laeuft noch - das Loslassen war nicht der letzte Grund.
        self.assertTrue(self.rig.loop.active)
        self.assertTrue(self.rig.state.slip_active)
        self.rig.run(1.0)
        self.assertTrue(self.rig.loop.active)


class SlipHotCueTests(unittest.TestCase):
    """Abschnitt 20/38: Slip + Hot Cue."""

    def setUp(self) -> None:
        track = make_track(
            hot_cues=(HotCue(index=0, position_s=5.0, color=""),)
        )
        self.rig = Rig(track)
        self.rig.seek(30.0)
        self.rig.play()
        self.rig.set_slip(True)

    def test_20_holding_a_hot_cue_plays_temporarily(self) -> None:
        self.rig.pad(0, pressed=True)
        self.assertTrue(self.rig.state.slip_active)
        self.assertAlmostEqual(self.rig.position, 5.0, places=6)
        self.assertAlmostEqual(self.rig.background, 30.0, places=6)

        self.rig.run(2.0)
        self.assertAlmostEqual(self.rig.position, 7.0, places=3)
        self.assertAlmostEqual(self.rig.background, 32.0, places=3)

        self.rig.pad(0, pressed=False)
        self.assertAlmostEqual(self.rig.position, 32.0, places=3)
        self.assertFalse(self.rig.state.slip_active)

    def test_without_slip_the_hot_cue_simply_plays_on(self) -> None:
        self.rig.set_slip(False)
        self.rig.pad(0, pressed=True)
        self.rig.run(2.0)
        self.rig.pad(0, pressed=False)
        self.assertAlmostEqual(self.rig.position, 7.0, places=3)

    def test_38_a_hot_cue_still_leaves_a_normal_loop(self) -> None:
        """Der normale Loop-Zustand und die temporaere Slip-Wiedergabe
        bleiben auseinander: der Loop endet, die Slip-Aktion laeuft."""
        self.rig.seek(10.0)
        self.rig.beat_loop(4.0)
        self.assertTrue(self.rig.loop.active)
        self.rig.pad(0, pressed=True)
        self.assertFalse(self.rig.loop.active)
        self.assertIs(self.rig.loop.exit_reason, LoopExitReason.JUMPED_OUT)
        self.assertTrue(self.rig.state.slip_active)
        self.rig.run(2.0)
        self.rig.pad(0, pressed=False)
        self.assertAlmostEqual(self.rig.position, 12.0, places=3)


class SlipReverseTests(unittest.TestCase):
    """Abschnitt 21/47: Slip + Reverse."""

    def setUp(self) -> None:
        self.rig = Rig()
        self.rig.seek(30.0)
        self.rig.play()

    def test_47_three_seconds_of_reverse(self) -> None:
        self.rig.set_slip(True)
        self.rig.send(CommandType.DIRECTION, position=Direction.REV)
        self.assertTrue(self.rig.state.slip_active)
        self.rig.run(3.0)
        # Hoerbar rueckwaerts.
        self.assertAlmostEqual(self.rig.position, 27.0, places=3)
        # Hintergrund normal vorwaerts.
        self.assertAlmostEqual(self.rig.background, 33.0, places=3)

        self.rig.send(CommandType.DIRECTION, position=Direction.FWD)
        self.assertAlmostEqual(self.rig.position, 33.0, places=3)
        self.assertFalse(self.rig.state.slip_active)

    def test_without_slip_reverse_keeps_its_position(self) -> None:
        self.rig.send(CommandType.DIRECTION, position=Direction.REV)
        self.rig.run(3.0)
        self.rig.send(CommandType.DIRECTION, position=Direction.FWD)
        self.assertAlmostEqual(self.rig.position, 27.0, places=3)

    def test_slip_rev_is_a_slip_action_without_the_slip_button(self) -> None:
        """Die Stellung SLIP REV des Hebels ist die Slip-Stellung."""
        self.assertFalse(self.rig.state.slip)
        self.rig.send(CommandType.DIRECTION, position=Direction.SLIP_REV)
        self.assertTrue(self.rig.state.slip_active)
        self.rig.run(3.0)
        self.rig.send(CommandType.DIRECTION, position=Direction.FWD)
        self.assertAlmostEqual(self.rig.position, 33.0, places=3)


class SlipScopeTests(unittest.TestCase):
    """Abschnitt 22/39: was Slip **nicht** betrifft."""

    def setUp(self) -> None:
        self.rig = Rig()
        self.rig.seek(30.0)
        self.rig.play()
        self.rig.set_slip(True)

    def test_22_track_search_is_no_slip_action(self) -> None:
        self.rig.touch(True)
        self.rig.run(2.0)
        self.assertTrue(self.rig.state.slip_active)
        self.rig.send(CommandType.TRACK_SEARCH, direction=+1)
        self.assertFalse(self.rig.state.slip_active)
        self.assertIsNone(self.rig.background)

    def test_22b_an_explicit_seek_is_not_taken_back(self) -> None:
        self.rig.touch(True)
        self.rig.run(2.0)
        self.rig.seek(120.0)
        self.assertAlmostEqual(self.rig.position, 120.0, places=6)
        self.rig.touch(False)
        self.assertAlmostEqual(self.rig.position, 120.0, places=6)

    def test_22c_cue_is_not_taken_back(self) -> None:
        # Cue-Punkt bei 30 s setzen: pausiert, an der Stelle, CUE druecken.
        self.rig.pause()
        self.rig.send(CommandType.CUE, pressed=True)
        self.rig.send(CommandType.CUE, pressed=False)
        self.assertAlmostEqual(self.rig.state.cue_point_s, 30.0, places=6)

        self.rig.seek(60.0)
        self.rig.play()
        self.rig.touch(True)
        self.rig.run(2.0)
        self.assertTrue(self.rig.state.slip_active)

        # CUE waehrend der Wiedergabe: zurueck zum Cue-Punkt, Pause.
        self.rig.send(CommandType.CUE, pressed=True)
        self.assertAlmostEqual(self.rig.position, 30.0, places=6)
        self.assertFalse(self.rig.state.slip_active)
        self.rig.send(CommandType.CUE, pressed=False)
        self.rig.touch(False)
        self.assertAlmostEqual(self.rig.position, 30.0, places=6)

    def test_39_loading_a_track_resets_the_temporary_state(self) -> None:
        self.rig.touch(True)
        self.rig.run(2.0)
        self.assertTrue(self.rig.state.slip_active)

        self.rig.deck.load_track(make_track(track_id="t2"))
        state = self.rig.state
        self.assertFalse(state.slip_active)
        self.assertIsNone(state.slip_position_s)
        # Einstellungen bleiben.
        self.assertTrue(state.slip)
        self.assertIs(state.jog_mode, JogMode.VINYL)

    def test_39b_settings_survive_a_track_change(self) -> None:
        self.rig.set_quantize(True)
        self.rig.send(CommandType.QUANTIZE_BEATS, beats=0.25)
        self.rig.set_jog_mode(JogMode.CDJ)
        self.rig.deck.load_track(make_track(track_id="t3"))
        state = self.rig.state
        self.assertTrue(state.quantize)
        self.assertAlmostEqual(state.quantize_beats, 0.25)
        self.assertIs(state.jog_mode, JogMode.CDJ)
        self.assertTrue(state.slip)

    def test_39c_eject_resets_the_temporary_state(self) -> None:
        self.rig.touch(True)
        self.rig.run(2.0)
        self.rig.deck.eject()
        self.assertFalse(self.rig.state.slip_active)
        self.assertIsNone(self.rig.state.slip_position_s)

    def test_37_search_inside_a_loop_is_not_damaged(self) -> None:
        """Abschnitt 37: keine neue Sonderlogik - SEARCH bleibt, wie es war.

        Die spaetere Loop-V&V verlangt, dass SEARCH innerhalb des Loops
        bleibt. Hier wird nur festgehalten, dass Slip und Quantize daran
        nichts aendern.
        """
        self.rig.seek(10.0)
        self.rig.beat_loop(4.0)
        self.rig.set_quantize(True)
        self.rig.send(CommandType.SEARCH, direction=+1, pressed=True)
        self.rig.run(1.0)
        self.rig.send(CommandType.SEARCH, direction=+1, pressed=False)
        loop = self.rig.loop
        self.assertTrue(loop.active)
        self.assertGreaterEqual(self.rig.position, loop.in_s - 1e-6)
        self.assertLess(self.rig.position, loop.out_s + 1e-6)


# --------------------------------------------------------------------------
# Abschnitt 48: Kombinationen
# --------------------------------------------------------------------------


class CombinationTests(unittest.TestCase):
    """Alle fuenf geforderten Kombinationen, je ein vollstaendiger Ablauf."""

    COMBINATIONS = (
        (True, False, JogMode.VINYL),
        (False, True, JogMode.VINYL),
        (True, True, JogMode.VINYL),
        (True, True, JogMode.CDJ),
        (False, False, JogMode.CDJ),
    )

    def _session(
        self, quantize: bool, slip: bool, jog_mode: JogMode
    ) -> None:
        rig = Rig()
        rig.set_quantize(quantize)
        rig.set_slip(slip)
        rig.set_jog_mode(jog_mode)

        rig.seek(10.3)
        rig.play()
        rig.run(0.4)

        # Loop setzen und laufen lassen.
        rig.beat_loop(4.0)
        self.assertTrue(rig.loop.active)
        self.assertAlmostEqual(rig.loop.length_s, 4 * BEAT_S, places=9)
        if quantize:
            self.assertAlmostEqual(rig.loop.in_s % BEAT_S, 0.0, places=9)
        rig.run(3.0)
        self.assertTrue(rig.loop.active)

        # Jogwheel bedienen.
        rig.touch(True)
        rig.run(0.5)
        rig.turn(0.5)
        rig.touch(False)
        self.assertTrue(rig.loop.active)

        # Hotcue setzen, Loop verlassen, weiterlaufen.
        rig.pad(0)
        rig.send(CommandType.RELOOP_EXIT)
        rig.run(1.0)

        self.assertIs(rig.state.play_state, PlayState.PLAYING)
        self.assertIs(rig.state.quantize, quantize)
        self.assertIs(rig.state.slip, slip)
        self.assertIs(rig.state.jog_mode, jog_mode)
        # Nach dem Ende aller Aktionen bleibt keine Zeitachse stehen.
        self.assertFalse(rig.state.slip_active)

    def test_48_all_five_combinations(self) -> None:
        for quantize, slip, jog_mode in self.COMBINATIONS:
            with self.subTest(
                quantize=quantize, slip=slip, jog=jog_mode.value
            ):
                self._session(quantize, slip, jog_mode)


# --------------------------------------------------------------------------
# Abschnitt 49: der Loop darf nicht beschaedigt werden
# --------------------------------------------------------------------------


class LoopIsNotDamagedTests(unittest.TestCase):
    def test_49_pure_mode_changes_never_end_a_loop(self) -> None:
        rig = Rig()
        rig.seek(10.0)
        rig.beat_loop(4.0)
        rig.play()
        before = rig.loop

        switches = (
            ("Quantize an", lambda: rig.tap(ids.QUANTIZE)),
            ("Beat Value", lambda: rig.send(CommandType.QUANTIZE_BEATS)),
            ("Slip an", lambda: rig.tap(ids.SLIP)),
            ("Jog Mode CDJ", lambda: rig.tap(ids.JOG_MODE)),
            ("Quantize aus", lambda: rig.tap(ids.QUANTIZE)),
            ("Jog Mode VINYL", lambda: rig.tap(ids.JOG_MODE)),
            ("Slip aus", lambda: rig.tap(ids.SLIP)),
            ("Slip an", lambda: rig.tap(ids.SLIP)),
            ("Quantize an", lambda: rig.tap(ids.QUANTIZE)),
            ("Jog Mode CDJ", lambda: rig.tap(ids.JOG_MODE)),
        )
        for label, action in switches:
            with self.subTest(label):
                action()
                rig.run(0.2)
                loop = rig.loop
                self.assertTrue(
                    loop.active,
                    f"{label} hat den Loop beendet, Grund="
                    f"{loop.exit_reason.value if loop.exit_reason else '-'}",
                )
                self.assertAlmostEqual(loop.in_s, before.in_s, places=9)
                self.assertAlmostEqual(loop.out_s, before.out_s, places=9)

    def test_49b_the_loop_survives_a_hundred_repetitions_with_slip(
        self,
    ) -> None:
        rig = Rig()
        rig.seek(10.0)
        rig.play()
        rig.set_slip(True)
        rig.beat_loop(4.0)
        before = rig.loop
        rig.run(100 * before.length_s, step=0.02)
        loop = rig.loop
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.in_s, before.in_s, places=9)
        self.assertAlmostEqual(loop.out_s, before.out_s, places=9)


# --------------------------------------------------------------------------
# Abschnitt 50: Debug-Zustand
# --------------------------------------------------------------------------


class DebugStateTests(unittest.TestCase):
    """Alles, was die Entwicklungsanzeige zeigt, ist echter Deck-Zustand."""

    def test_50_every_debug_value_comes_from_the_deck_state(self) -> None:
        rig = Rig()
        rig.seek(10.0)
        rig.play()
        rig.set_quantize(True)
        rig.send(CommandType.QUANTIZE_BEATS, beats=0.25)
        rig.set_slip(True)
        rig.beat_loop(4.0)
        rig.touch(True)
        rig.run(0.5)

        state = rig.state
        self.assertTrue(state.quantize)
        self.assertAlmostEqual(state.quantize_beats, 0.25)
        self.assertTrue(state.slip)
        self.assertTrue(state.slip_active)
        self.assertIsNotNone(state.slip_position_s)
        self.assertIs(state.jog_mode, JogMode.VINYL)
        self.assertTrue(state.jog_touch)
        self.assertGreaterEqual(state.position_s, 0.0)
        self.assertTrue(state.loop.active)
        self.assertIn("LOOP", state.slip_state.label())
        self.assertIn("SCRATCH", state.slip_state.label())

    def test_50b_the_gui_only_sends_commands(self) -> None:
        """Abschnitt 40: die drei Schalter sind reine Kommandos.

        Der Weg ist fuer Bildschirm und Hardware derselbe: Control-ID ->
        Kommando -> Deck. Es gibt keinen zweiten Weg, den Zustand zu setzen.
        """
        sent = []
        layer = InputLayer()
        layer.subscribe(InputMapper(1, sent.append).handle_event)
        for control_id, expected in (
            (ids.QUANTIZE, CommandType.QUANTIZE_TOGGLE),
            (ids.SLIP, CommandType.SLIP_TOGGLE),
            (ids.JOG_MODE, CommandType.JOG_MODE_TOGGLE),
        ):
            with self.subTest(control_id):
                sent.clear()
                layer.press(control_id)
                layer.release(control_id)
                self.assertEqual([cmd.type for cmd in sent], [expected])
                self.assertEqual(sent[0].deck_id, 1)


if __name__ == "__main__":
    unittest.main()
