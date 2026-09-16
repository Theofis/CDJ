"""Tests der Deck-Schicht: Zustand, Engine, Mapping, Provider."""

from __future__ import annotations

import unittest

from virtual_cdj.core import ids
from virtual_cdj.core.input_layer import InputLayer
from virtual_cdj.core.model import Source
from virtual_cdj.deck.commands import CommandType, DeckCommand, Views, command
from virtual_cdj.deck.engine import Deck
from virtual_cdj.deck.mapping import InputMapper
from virtual_cdj.jog import STEPS_PER_REV
from virtual_cdj.deck.provider import (
    LocalDeckStateProvider,
    NetworkDeckStateProvider,
)
from virtual_cdj.deck.state import (
    AudioStatus,
    BeatGrid,
    CueKind,
    Direction,
    HotCue,
    JogMode,
    LoopAdjust,
    PadMode,
    PlayState,
    TrackInfo,
    WaveformData,
    empty_state,
)


def make_track(**overrides) -> TrackInfo:
    """Ein Track mit vollstaendigen, gueltigen Analysedaten."""
    defaults = dict(
        track_id="t1",
        title="Testtrack",
        artist="Tester",
        duration_s=120.0,
        original_bpm=120.0,
        key="8A",
        beat_grid=BeatGrid(first_beat_s=0.0, bpm=120.0, beats_per_bar=4),
    )
    defaults.update(overrides)
    return TrackInfo(**defaults)


class ClockedDeck:
    """Deck mit steuerbarer Uhr."""

    def __init__(self, deck_id: int = 1) -> None:
        self.now = 0.0
        self.deck = Deck(deck_id, time_source=lambda: self.now)

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self.deck.tick()


# --------------------------------------------------------------------------


class BeatGridTests(unittest.TestCase):
    def setUp(self) -> None:
        self.grid = BeatGrid(first_beat_s=0.0, bpm=120.0, beats_per_bar=4)

    def test_beat_interval(self) -> None:
        self.assertAlmostEqual(self.grid.beat_interval_s, 0.5)

    def test_bar_and_beat(self) -> None:
        self.assertEqual(self.grid.bar_and_beat_at(0.0), (1, 1))
        self.assertEqual(self.grid.bar_and_beat_at(0.5), (1, 2))
        self.assertEqual(self.grid.bar_and_beat_at(1.5), (1, 4))
        self.assertEqual(self.grid.bar_and_beat_at(2.0), (2, 1))

    def test_phase(self) -> None:
        self.assertAlmostEqual(self.grid.phase_at(0.0), 0.0)
        self.assertAlmostEqual(self.grid.phase_at(0.25), 0.5)

    def test_snap(self) -> None:
        self.assertAlmostEqual(self.grid.snap(0.24), 0.0)
        self.assertAlmostEqual(self.grid.snap(0.26), 0.5)

    def test_seconds_for_beats(self) -> None:
        self.assertAlmostEqual(self.grid.seconds_for_beats(4), 2.0)

    def test_explicit_beat_list_wins(self) -> None:
        grid = BeatGrid(beats_s=(0.0, 0.4, 1.0, 1.8), beats_per_bar=4)
        self.assertTrue(grid.is_valid)
        self.assertEqual(grid.beat_number_at(0.5), 1)
        self.assertEqual(grid.bar_and_beat_at(1.0), (1, 3))

    def test_invalid_grid_reports_itself(self) -> None:
        grid = BeatGrid()
        self.assertFalse(grid.is_valid)
        self.assertEqual(grid.bar_and_beat_at(5.0), (0, 0))


class EmptyStateTests(unittest.TestCase):
    def test_empty_deck_is_honest(self) -> None:
        state = empty_state(3)
        self.assertEqual(state.deck_id, 3)
        self.assertIsNone(state.track)
        self.assertFalse(state.has_track)
        self.assertFalse(state.has_waveform)
        self.assertFalse(state.has_beat_grid)
        self.assertIs(state.play_state, PlayState.EMPTY)
        self.assertIs(state.audio_status, AudioStatus.NO_BACKEND)
        self.assertEqual(state.current_bpm, 0.0)
        self.assertEqual((state.bar, state.beat), (0, 0))

    def test_waveform_consistency_is_checked(self) -> None:
        good = WaveformData(50.0, [0.1] * 10, [0.2] * 10, [0.3] * 10)
        self.assertTrue(good.is_consistent())
        self.assertAlmostEqual(good.duration_s, 0.2)
        bad = WaveformData(50.0, [0.1] * 10, [0.2] * 9, [0.3] * 10)
        self.assertFalse(bad.is_consistent())


class TransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.c = ClockedDeck()
        self.deck = self.c.deck
        self.deck.load_track(make_track())

    def test_load_sets_real_values(self) -> None:
        state = self.deck.state
        self.assertTrue(state.has_track)
        self.assertTrue(state.has_beat_grid)
        self.assertFalse(state.has_waveform)  # keine Analyse vorhanden
        self.assertEqual(state.duration_s, 120.0)
        self.assertEqual(state.original_bpm, 120.0)
        self.assertEqual(state.current_bpm, 120.0)
        self.assertEqual(state.key, "8A")
        self.assertIs(state.play_state, PlayState.STOPPED)

    def test_position_advances_only_while_playing(self) -> None:
        self.c.advance(1.0)
        self.assertEqual(self.deck.state.position_s, 0.0)

        self.deck.execute(command(CommandType.PLAY_PAUSE, 1))
        self.c.advance(2.0)
        self.assertAlmostEqual(self.deck.state.position_s, 2.0, places=6)

        self.deck.execute(command(CommandType.PLAY_PAUSE, 1))
        self.c.advance(5.0)
        self.assertAlmostEqual(self.deck.state.position_s, 2.0, places=6)

    def test_tempo_changes_speed_and_bpm_but_not_original(self) -> None:
        self.deck.execute(command(CommandType.TEMPO_SET, 1, value=1.0))
        state = self.deck.state
        self.assertAlmostEqual(state.tempo_percent, 10.0)
        self.assertAlmostEqual(state.current_bpm, 132.0)
        self.assertEqual(state.original_bpm, 120.0)

        self.deck.execute(command(CommandType.PLAY_PAUSE, 1))
        self.c.advance(1.0)
        self.assertAlmostEqual(self.deck.state.position_s, 1.1, places=6)

    def test_tempo_range_cycles(self) -> None:
        seen = []
        for _ in range(5):
            seen.append(self.deck.state.tempo_range)
            self.deck.execute(command(CommandType.TEMPO_RANGE_CYCLE, 1))
        self.assertEqual(seen, [10.0, 16.0, None, 6.0, 10.0])

    def test_tempo_reset_forces_original_speed(self) -> None:
        self.deck.execute(command(CommandType.TEMPO_SET, 1, value=1.0))
        self.deck.execute(command(CommandType.TEMPO_RESET_TOGGLE, 1))
        self.assertTrue(self.deck.state.tempo_reset)
        self.assertAlmostEqual(self.deck.state.current_bpm, 120.0)
        self.deck.execute(command(CommandType.PLAY_PAUSE, 1))
        self.c.advance(1.0)
        self.assertAlmostEqual(self.deck.state.position_s, 1.0, places=6)

    def test_beat_and_bar_come_from_beat_grid(self) -> None:
        self.deck.execute(command(CommandType.PLAY_PAUSE, 1))
        self.c.advance(2.25)
        state = self.deck.state
        self.assertEqual((state.bar, state.beat), (2, 1))
        self.assertAlmostEqual(state.beat_phase, 0.5, places=6)

    def test_no_beat_without_beat_grid(self) -> None:
        deck = Deck(2)
        deck.load_track(make_track(beat_grid=None))
        self.assertFalse(deck.state.has_beat_grid)
        self.assertEqual((deck.state.bar, deck.state.beat), (0, 0))

    def test_stops_at_end_of_track(self) -> None:
        self.deck.execute(command(CommandType.PLAY_PAUSE, 1))
        self.c.advance(500.0)
        self.assertAlmostEqual(self.deck.state.position_s, 120.0)
        self.assertIs(self.deck.state.play_state, PlayState.STOPPED)

    def test_seek(self) -> None:
        self.deck.execute(command(CommandType.SEEK, 1, position_s=42.0))
        self.assertAlmostEqual(self.deck.state.position_s, 42.0)

    def test_reverse_direction_plays_backwards(self) -> None:
        self.deck.execute(command(CommandType.SEEK, 1, position_s=10.0))
        self.deck.execute(
            command(CommandType.DIRECTION, 1, position=Direction.REV)
        )
        self.deck.execute(command(CommandType.PLAY_PAUSE, 1))
        self.c.advance(2.0)
        self.assertAlmostEqual(self.deck.state.position_s, 8.0, places=6)

    def test_generation_increases_on_change(self) -> None:
        before = self.deck.state.generation
        self.deck.execute(command(CommandType.QUANTIZE_TOGGLE, 1))
        self.assertGreater(self.deck.state.generation, before)


class CueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.c = ClockedDeck()
        self.deck = self.c.deck
        self.deck.load_track(make_track())

    def test_cue_while_playing_returns_to_cue_point(self) -> None:
        self.deck.execute(command(CommandType.PLAY_PAUSE, 1))
        self.c.advance(5.0)
        self.deck.execute(command(CommandType.CUE, 1, pressed=True))
        self.assertAlmostEqual(self.deck.state.position_s, 0.0)
        self.assertIs(self.deck.state.play_state, PlayState.PAUSED)

    def test_cue_sets_new_point_when_paused_elsewhere(self) -> None:
        self.deck.execute(command(CommandType.SEEK, 1, position_s=8.0))
        self.deck.execute(command(CommandType.CUE, 1, pressed=True))
        self.assertAlmostEqual(self.deck.state.cue_point_s, 8.0)

    def test_cue_point_sampler_plays_while_held(self) -> None:
        self.deck.execute(command(CommandType.CUE, 1, pressed=True))
        self.assertIs(self.deck.state.play_state, PlayState.CUEING)
        self.c.advance(1.0)
        self.assertAlmostEqual(self.deck.state.position_s, 1.0, places=6)
        self.deck.execute(command(CommandType.CUE, 1, pressed=False))
        self.assertIs(self.deck.state.play_state, PlayState.PAUSED)
        self.assertAlmostEqual(self.deck.state.position_s, 0.0)


class HotCueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deck = Deck(1)
        self.deck.load_track(
            make_track(
                hot_cues=(
                    HotCue(index=0, position_s=10.0, color="#ff0000"),
                    HotCue(
                        index=1, position_s=20.0, color="#00ff00",
                        kind=CueKind.LOOP, loop_end_s=22.0,
                    ),
                )
            )
        )

    def test_pad_jumps_to_existing_hot_cue(self) -> None:
        self.deck.execute(
            command(CommandType.PAD, 1, index=0, pressed=True)
        )
        self.assertAlmostEqual(self.deck.state.position_s, 10.0)
        self.assertIs(self.deck.state.play_state, PlayState.PLAYING)

    def test_loop_hot_cue_activates_loop(self) -> None:
        self.deck.execute(
            command(CommandType.PAD, 1, index=1, pressed=True)
        )
        loop = self.deck.state.loop
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.in_s, 20.0)
        self.assertAlmostEqual(loop.out_s, 22.0)

    def test_empty_pad_sets_hot_cue_at_position(self) -> None:
        self.deck.execute(command(CommandType.SEEK, 1, position_s=33.0))
        self.deck.execute(
            command(CommandType.PAD, 1, index=4, pressed=True)
        )
        cue = self.deck.state.track.hot_cues
        added = [c for c in cue if c.index == 4]
        self.assertEqual(len(added), 1)
        self.assertAlmostEqual(added[0].position_s, 33.0)

    def test_delete_modifier_removes_hot_cue(self) -> None:
        self.deck.execute(command(CommandType.DELETE, 1, pressed=True))
        self.deck.execute(
            command(CommandType.PAD, 1, index=0, pressed=True)
        )
        self.deck.execute(command(CommandType.DELETE, 1, pressed=False))
        indexes = [c.index for c in self.deck.state.track.hot_cues]
        self.assertNotIn(0, indexes)

    def test_pads_use_real_cue_data_not_gui_state(self) -> None:
        track = self.deck.state.track
        self.assertEqual(track.hot_cue(0).color, "#ff0000")
        self.assertEqual(track.hot_cue(0).label, "A")
        self.assertIsNone(track.hot_cue(7))

    def test_other_pad_mode_defers_instead_of_inventing(self) -> None:
        self.deck.execute(
            command(CommandType.PAD_MODE, 1, mode=PadMode.BEAT_JUMP)
        )
        self.deck.execute(
            command(CommandType.PAD, 1, index=0, pressed=True)
        )
        self.assertEqual(
            self.deck.deferred_pad_events, [(0, PadMode.BEAT_JUMP)]
        )


class LoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.c = ClockedDeck()
        self.deck = self.c.deck
        self.deck.load_track(make_track())

    def test_manual_loop_in_out(self) -> None:
        self.deck.execute(command(CommandType.SEEK, 1, position_s=4.0))
        self.deck.execute(command(CommandType.LOOP_IN, 1))
        self.assertFalse(self.deck.state.loop.active)
        self.deck.execute(command(CommandType.SEEK, 1, position_s=6.0))
        self.deck.execute(command(CommandType.LOOP_OUT, 1))
        loop = self.deck.state.loop
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.length_s, 2.0)

    def test_beat_loop_uses_beat_grid(self) -> None:
        self.deck.execute(command(CommandType.SEEK, 1, position_s=1.0))
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=4))
        loop = self.deck.state.loop
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.length_s, 2.0)  # 4 Beats bei 120 BPM
        self.assertEqual(loop.label(), "4")

    def test_beat_loop_without_grid_is_refused(self) -> None:
        deck = Deck(2)
        deck.load_track(make_track(beat_grid=None))
        deck.execute(command(CommandType.BEAT_LOOP, 2, beats=4))
        self.assertFalse(deck.state.loop.active)
        self.assertIn("BEAT_LOOP (kein Beatgrid)", deck.unsupported)

    def test_cue_loop_call_changes_the_length_of_a_running_loop(self) -> None:
        """CALL < / CALL > bei laufendem Loop: halbieren und verdoppeln.

        Am CDJ-3000 verkuerzen und verlaengern diese Tasten den laufenden
        Loop - dieselbe Wirkung wie die Beat-Loop-Tasten, nur relativ statt
        auf eine feste Laenge.
        """
        self.deck.execute(command(CommandType.SEEK, 1, position_s=1.0))
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=8))
        start = self.deck.state.loop.in_s

        self.deck.execute(command(CommandType.CUE_LOOP_CALL, 1, direction=-1))
        loop = self.deck.state.loop
        self.assertTrue(loop.active)
        self.assertEqual(loop.beats, 4.0)
        # Der Anfang bleibt stehen - es wird nur das Ende verschoben.
        self.assertAlmostEqual(loop.in_s, start)

        self.deck.execute(command(CommandType.CUE_LOOP_CALL, 1, direction=+1))
        self.deck.execute(command(CommandType.CUE_LOOP_CALL, 1, direction=+1))
        self.assertEqual(self.deck.state.loop.beats, 16.0)
        self.assertAlmostEqual(self.deck.state.loop.in_s, start)

    def test_cue_loop_call_without_a_loop_makes_four_or_eight_beats(self) -> None:
        self.deck.execute(command(CommandType.SEEK, 1, position_s=1.0))
        self.deck.execute(command(CommandType.CUE_LOOP_CALL, 1, direction=-1))
        self.assertEqual(self.deck.state.loop.beats, 4.0)

        self.deck.execute(command(CommandType.RELOOP_EXIT, 1))
        self.deck.execute(command(CommandType.CUE_LOOP_CALL, 1, direction=+1))
        self.assertEqual(self.deck.state.loop.beats, 8.0)

    def test_beat_loop_buttons_set_an_absolute_length(self) -> None:
        """Der Unterschied zu CALL: 4/8 BEAT setzen, CALL skaliert."""
        self.deck.execute(command(CommandType.SEEK, 1, position_s=1.0))
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=8))
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=4))
        self.assertEqual(self.deck.state.loop.beats, 4.0)

    def test_playback_wraps_inside_loop(self) -> None:
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=4))
        self.deck.execute(command(CommandType.PLAY_PAUSE, 1))
        self.c.advance(10.0)
        loop = self.deck.state.loop
        self.assertGreaterEqual(self.deck.state.position_s, loop.in_s)
        self.assertLess(self.deck.state.position_s, loop.out_s)

    def test_halve_and_double(self) -> None:
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=4))
        self.deck.execute(command(CommandType.LOOP_HALVE, 1))
        self.assertAlmostEqual(self.deck.state.loop.length_s, 1.0)
        self.assertEqual(self.deck.state.loop.label(), "2")
        self.deck.execute(command(CommandType.LOOP_DOUBLE, 1))
        self.deck.execute(command(CommandType.LOOP_DOUBLE, 1))
        self.assertAlmostEqual(self.deck.state.loop.length_s, 4.0)
        self.assertEqual(self.deck.state.loop.label(), "8")

    def test_reloop_exit_toggles(self) -> None:
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=4))
        self.deck.execute(command(CommandType.RELOOP_EXIT, 1))
        self.assertFalse(self.deck.state.loop.active)
        self.assertTrue(self.deck.state.loop.is_set)
        self.deck.execute(command(CommandType.RELOOP_EXIT, 1))
        self.assertTrue(self.deck.state.loop.active)

    def test_fractional_loop_label(self) -> None:
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=0.25))
        self.assertEqual(self.deck.state.loop.label(), "1/4")

    def test_beat_loop_during_a_loop_keeps_the_start(self) -> None:
        """Laenge aendern, Anfang steht - egal wo im Loop man gerade ist.

        Der Abspielkopf wird dafuer **innerhalb** des Loops bewegt. Ein
        Sprung nach draussen verlaesst den Loop (siehe
        ``test_seeking_out_of_a_loop_leaves_it``); danach gaebe es keinen
        Anfang mehr, der stehen bleiben koennte.
        """
        self.deck.execute(command(CommandType.SEEK, 1, position_s=4.0))
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=4))
        self.deck.execute(command(CommandType.SEEK, 1, position_s=5.5))
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=8))
        loop = self.deck.state.loop
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.in_s, 4.0)
        self.assertAlmostEqual(loop.length_s, 4.0)  # 8 Beats bei 120 BPM

    def test_seeking_out_of_a_loop_leaves_it(self) -> None:
        """Ein bewusster Sprung nach draussen beendet den Loop.

        Vorher zog die Loop-Grenze die Position im naechsten Bild wieder
        zurueck: der Loop war eine Sackgasse, und jeder Sprung sah aus, als
        haenge das Geraet.
        """
        self.deck.execute(command(CommandType.SEEK, 1, position_s=4.0))
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=4))
        self.deck.execute(command(CommandType.SEEK, 1, position_s=20.0))
        self.assertFalse(self.deck.state.loop.active)
        self.assertAlmostEqual(self.deck.state.position_s, 20.0)
        # Der Loop bleibt als "letzter Loop" fuer RELOOP erhalten.
        self.assertTrue(self.deck.state.loop.has_last)

    def test_the_position_stays_after_a_seek_out_of_a_loop(self) -> None:
        self.deck.execute(command(CommandType.SEEK, 1, position_s=4.0))
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=4))
        self.deck.execute(command(CommandType.SEEK, 1, position_s=20.0))
        self.deck.execute(command(CommandType.PLAY_PAUSE, 1))
        self.c.advance(0.5)
        self.assertGreater(self.deck.state.position_s, 20.0)

    def test_seeking_inside_the_loop_keeps_it(self) -> None:
        self.deck.execute(command(CommandType.SEEK, 1, position_s=4.0))
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=4))
        self.deck.execute(command(CommandType.SEEK, 1, position_s=5.0))
        self.assertTrue(self.deck.state.loop.active)

    def test_the_jog_still_works_after_the_loop_is_gone(self) -> None:
        """Ein Adjust-Modus ohne Loop darf das Jogwheel nicht stillegen.

        Im Adjust-Modus geht die Jog-Bewegung an den Loop-Punkt statt an
        die Wiedergabe. Gaebe es dann keinen Loop mehr, verschwaende die
        Bewegung spurlos - das Rad dreht, und nichts passiert.
        """
        import dataclasses

        from virtual_cdj.deck.state import LoopAdjust

        self.deck.execute(command(CommandType.SEEK, 1, position_s=4.0))
        before = self.deck.state.position_s
        # Zustand von Hand herstellen: Adjust gesetzt, aber kein Loop.
        self.deck._update(  # noqa: SLF001
            loop=dataclasses.replace(
                self.deck.state.loop, adjust=LoopAdjust.IN, active=False
            )
        )
        self.deck.execute(
            command(
                CommandType.JOG_MOVE, 1,
                delta=800, ticks_per_rev=800, touched=True,
            )
        )
        self.assertNotAlmostEqual(self.deck.state.position_s, before)


class LoopAdjustTests(unittest.TestCase):
    """Loop-Punkte mit dem Jogwheel verschieben: 1 Umdrehung = 1 Beat."""

    def setUp(self) -> None:
        self.deck = Deck(1)
        self.deck.load_track(make_track())
        self.deck.execute(command(CommandType.SEEK, 1, position_s=10.0))
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=4))

    def turn(self, revolutions: float) -> None:
        self.deck.execute(
            command(
                CommandType.JOG_MOVE, 1,
                delta=int(revolutions * 360), ticks_per_rev=360,
            )
        )

    def test_loop_in_button_arms_the_in_adjust(self) -> None:
        self.deck.execute(command(CommandType.LOOP_IN, 1))
        self.assertIs(self.deck.state.loop.adjust, LoopAdjust.IN)

    def test_a_full_turn_moves_the_start_one_beat(self) -> None:
        self.deck.execute(command(CommandType.LOOP_IN, 1))
        self.turn(1.0)
        self.assertAlmostEqual(self.deck.state.loop.in_s, 10.5)

    def test_the_playback_position_stays_while_adjusting(self) -> None:
        self.deck.execute(command(CommandType.LOOP_IN, 1))
        self.turn(1.0)
        self.assertAlmostEqual(self.deck.state.position_s, 10.0)

    def test_without_adjust_mode_the_jog_moves_the_track(self) -> None:
        before = self.deck.state.position_s
        self.turn(1.0)
        self.assertGreater(self.deck.state.position_s, before)
        self.assertAlmostEqual(self.deck.state.loop.in_s, 10.0)

    def test_ending_the_adjust_saves_the_loop_for_reloop(self) -> None:
        self.deck.execute(command(CommandType.LOOP_OUT, 1))
        self.turn(2.0)
        self.deck.execute(command(CommandType.LOOP_OUT, 1))
        loop = self.deck.state.loop
        self.assertIs(loop.adjust, LoopAdjust.NONE)
        self.assertAlmostEqual(loop.last_out_s, 13.0)


class CallButtonTests(unittest.TestCase):
    """CALL < und CALL >: Loop erzeugen oder Laenge aendern."""

    def setUp(self) -> None:
        self.deck = Deck(1)
        self.deck.load_track(make_track())
        self.deck.execute(command(CommandType.SEEK, 1, position_s=4.0))

    def call(self, direction: int) -> None:
        self.deck.execute(
            command(CommandType.CUE_LOOP_CALL, 1, direction=direction)
        )

    def test_call_left_without_loop_makes_four_beats(self) -> None:
        self.call(-1)
        loop = self.deck.state.loop
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.length_s, 2.0)  # 4 Beats bei 120 BPM

    def test_call_right_without_loop_makes_eight_beats(self) -> None:
        self.call(+1)
        self.assertAlmostEqual(self.deck.state.loop.length_s, 4.0)

    def test_call_left_halves_a_running_loop(self) -> None:
        self.call(+1)
        self.call(-1)
        self.assertAlmostEqual(self.deck.state.loop.length_s, 2.0)
        self.assertEqual(self.deck.state.loop.label(), "4")

    def test_call_right_doubles_a_running_loop(self) -> None:
        self.call(-1)
        self.call(+1)
        self.assertAlmostEqual(self.deck.state.loop.length_s, 4.0)

    def test_without_a_beatgrid_the_gap_is_recorded(self) -> None:
        deck = Deck(2)
        deck.load_track(make_track(beat_grid=None))
        deck.execute(
            command(CommandType.CUE_LOOP_CALL, 2, direction=-1)
        )
        self.assertFalse(deck.state.loop.active)
        self.assertIn("CUE_LOOP_CALL (kein Beatgrid)", deck.unsupported)


class BeatLoopPadTests(unittest.TestCase):
    """Pads A-H im Beatloop-Modus."""

    def setUp(self) -> None:
        self.deck = Deck(1)
        self.deck.load_track(make_track())
        self.deck.execute(
            command(CommandType.PAD_MODE, 1, mode=PadMode.BEAT_LOOP)
        )
        self.deck.execute(command(CommandType.SEEK, 1, position_s=4.0))

    def pad(self, index: int) -> None:
        self.deck.execute(
            command(CommandType.PAD, 1, index=index, pressed=True)
        )

    def test_pad_mode_alone_starts_no_loop(self) -> None:
        # Abschnitt 20: Beatloop-Modus heisst nicht, dass ein Loop laeuft.
        self.assertIs(self.deck.state.pad_mode, PadMode.BEAT_LOOP)
        self.assertFalse(self.deck.state.loop.active)

    def test_pad_e_makes_a_four_beat_loop(self) -> None:
        self.pad(4)
        loop = self.deck.state.loop
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.in_s, 4.0)
        self.assertAlmostEqual(loop.length_s, 2.0)
        self.assertEqual(loop.label(), "4")

    def test_another_pad_changes_only_the_length(self) -> None:
        self.pad(4)
        # Innerhalb des Loops bleiben - ein Sprung nach draussen wuerde ihn
        # verlassen, und dann gaebe es keinen Anfang zum Stehenbleiben.
        self.deck.execute(command(CommandType.SEEK, 1, position_s=5.0))
        self.pad(5)
        loop = self.deck.state.loop
        self.assertAlmostEqual(loop.in_s, 4.0)
        self.assertAlmostEqual(loop.length_s, 4.0)

    def test_a_pad_does_not_leave_a_loop_it_did_not_set(self) -> None:
        """CALL < erzeugt 4 Beats - Pad E (auch 4 Beats) darf nicht beenden."""
        self.deck.execute(command(CommandType.CUE_LOOP_CALL, 1, direction=-1))
        self.assertTrue(self.deck.state.loop.active)
        self.pad(4)
        self.assertTrue(
            self.deck.state.loop.active,
            "Pad E muss den Loop setzen, nicht verlassen",
        )

    def test_the_same_pad_again_leaves_the_loop(self) -> None:
        self.pad(4)
        self.pad(4)
        self.assertFalse(self.deck.state.loop.active)

    def test_hot_cue_mode_pads_stay_hot_cues(self) -> None:
        self.deck.execute(
            command(CommandType.PAD_MODE, 1, mode=PadMode.HOT_CUE)
        )
        self.pad(4)
        self.assertFalse(self.deck.state.loop.active)
        self.assertEqual(len(self.deck.state.track.hot_cues), 1)

    def test_without_a_beatgrid_the_gap_is_recorded(self) -> None:
        deck = Deck(2)
        deck.load_track(make_track(beat_grid=None))
        deck.execute(
            command(CommandType.PAD_MODE, 2, mode=PadMode.BEAT_LOOP)
        )
        deck.execute(command(CommandType.PAD, 2, index=4, pressed=True))
        self.assertFalse(deck.state.loop.active)
        self.assertIn("BEAT_LOOP (kein Beatgrid)", deck.unsupported)


class ReloopTests(unittest.TestCase):
    """RELOOP/EXIT - eine Taste, zwei Funktionen."""

    def setUp(self) -> None:
        self.deck = Deck(1)
        self.deck.load_track(make_track())
        self.deck.execute(command(CommandType.SEEK, 1, position_s=10.0))
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=4))

    def test_exit_does_not_move_the_playback_position(self) -> None:
        self.deck.execute(command(CommandType.SEEK, 1, position_s=11.0))
        self.deck.execute(command(CommandType.RELOOP_EXIT, 1))
        self.assertFalse(self.deck.state.loop.active)
        self.assertAlmostEqual(self.deck.state.position_s, 11.0)

    def test_reloop_inside_the_old_loop_keeps_the_position(self) -> None:
        self.deck.execute(command(CommandType.RELOOP_EXIT, 1))
        self.deck.execute(command(CommandType.SEEK, 1, position_s=11.0))
        self.deck.execute(command(CommandType.RELOOP_EXIT, 1))
        self.assertTrue(self.deck.state.loop.active)
        self.assertAlmostEqual(self.deck.state.position_s, 11.0)

    def test_reloop_from_outside_jumps_to_the_loop_start(self) -> None:
        self.deck.execute(command(CommandType.RELOOP_EXIT, 1))
        self.deck.execute(command(CommandType.SEEK, 1, position_s=40.0))
        self.deck.execute(command(CommandType.RELOOP_EXIT, 1))
        self.assertTrue(self.deck.state.loop.active)
        self.assertAlmostEqual(self.deck.state.position_s, 10.0)

    def test_without_a_saved_loop_nothing_happens(self) -> None:
        deck = Deck(2)
        deck.load_track(make_track())
        deck.execute(command(CommandType.SEEK, 2, position_s=5.0))
        deck.execute(command(CommandType.RELOOP_EXIT, 2))
        self.assertFalse(deck.state.loop.active)
        self.assertAlmostEqual(deck.state.position_s, 5.0)

    def test_a_new_track_forgets_the_saved_loop(self) -> None:
        self.deck.execute(command(CommandType.RELOOP_EXIT, 1))
        self.deck.load_track(make_track(track_id="t2"))
        self.assertFalse(self.deck.state.loop.has_last)


class QuantizeTests(unittest.TestCase):
    """QUANTIZE entscheidet, ob neue Punkte auf das Beatgrid rasten."""

    def setUp(self) -> None:
        self.deck = Deck(1)
        self.deck.load_track(make_track())

    def quantize_on(self) -> None:
        self.deck.execute(command(CommandType.QUANTIZE_TOGGLE, 1))
        self.assertTrue(self.deck.state.quantize)

    def test_quantize_snaps_loop_in_to_beat(self) -> None:
        self.quantize_on()
        self.deck.execute(command(CommandType.SEEK, 1, position_s=1.1))
        self.deck.execute(command(CommandType.LOOP_IN, 1))
        self.assertAlmostEqual(self.deck.state.loop.in_s, 1.0)

    def test_without_quantize_exact_position_is_used(self) -> None:
        self.assertFalse(self.deck.state.quantize)
        self.deck.execute(command(CommandType.SEEK, 1, position_s=1.1))
        self.deck.execute(command(CommandType.LOOP_IN, 1))
        self.assertAlmostEqual(self.deck.state.loop.in_s, 1.1)

    def test_quantize_snaps_loop_out_to_beat(self) -> None:
        self.quantize_on()
        self.deck.execute(command(CommandType.SEEK, 1, position_s=1.1))
        self.deck.execute(command(CommandType.LOOP_IN, 1))
        self.deck.execute(command(CommandType.SEEK, 1, position_s=3.1))
        self.deck.execute(command(CommandType.LOOP_OUT, 1))
        loop = self.deck.state.loop
        self.assertAlmostEqual(loop.out_s, 3.0)
        self.assertAlmostEqual(loop.length_s, 2.0)

    def test_without_quantize_the_manual_loop_is_exact(self) -> None:
        self.deck.execute(command(CommandType.SEEK, 1, position_s=1.1))
        self.deck.execute(command(CommandType.LOOP_IN, 1))
        self.deck.execute(command(CommandType.SEEK, 1, position_s=3.1))
        self.deck.execute(command(CommandType.LOOP_OUT, 1))
        loop = self.deck.state.loop
        self.assertAlmostEqual(loop.in_s, 1.1)
        self.assertAlmostEqual(loop.out_s, 3.1)

    def test_quantize_snaps_the_beat_loop_start(self) -> None:
        self.quantize_on()
        self.deck.execute(command(CommandType.SEEK, 1, position_s=1.1))
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=4))
        self.assertAlmostEqual(self.deck.state.loop.in_s, 1.0)

    def test_without_quantize_the_beat_loop_starts_exactly(self) -> None:
        self.deck.execute(command(CommandType.SEEK, 1, position_s=1.1))
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=4))
        loop = self.deck.state.loop
        self.assertAlmostEqual(loop.in_s, 1.1)
        self.assertAlmostEqual(loop.length_s, 2.0)  # Laenge bleibt 4 Beats

    def test_quantize_snaps_the_call_loop_start(self) -> None:
        self.quantize_on()
        self.deck.execute(command(CommandType.SEEK, 1, position_s=1.1))
        self.deck.execute(
            command(CommandType.CUE_LOOP_CALL, 1, direction=-1)
        )
        self.assertAlmostEqual(self.deck.state.loop.in_s, 1.0)

    def test_without_quantize_the_call_loop_starts_exactly(self) -> None:
        self.deck.execute(command(CommandType.SEEK, 1, position_s=1.1))
        self.deck.execute(
            command(CommandType.CUE_LOOP_CALL, 1, direction=-1)
        )
        self.assertAlmostEqual(self.deck.state.loop.in_s, 1.1)

    def test_quantize_snaps_the_cue_point(self) -> None:
        self.quantize_on()
        self.deck.execute(command(CommandType.SEEK, 1, position_s=1.1))
        self.deck.execute(command(CommandType.CUE, 1, pressed=True))
        self.assertAlmostEqual(self.deck.state.cue_point_s, 1.0)

    def test_without_quantize_the_cue_point_is_exact(self) -> None:
        self.deck.execute(command(CommandType.SEEK, 1, position_s=1.1))
        self.deck.execute(command(CommandType.CUE, 1, pressed=True))
        self.assertAlmostEqual(self.deck.state.cue_point_s, 1.1)

    def test_jog_adjust_is_never_quantized(self) -> None:
        self.quantize_on()
        self.deck.execute(command(CommandType.SEEK, 1, position_s=2.0))
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=4))
        self.deck.execute(command(CommandType.LOOP_IN, 1))
        self.deck.execute(
            command(CommandType.JOG_MOVE, 1, delta=90, ticks_per_rev=360)
        )
        # Viertel Umdrehung = Viertel Beat, ungerastet.
        self.assertAlmostEqual(self.deck.state.loop.in_s, 2.125)


class JogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deck = Deck(1)
        self.deck.load_track(make_track())
        self.deck.execute(command(CommandType.SEEK, 1, position_s=30.0))

    def move(self, delta: int, *, ticks_per_rev: int = STEPS_PER_REV) -> None:
        self.deck.execute(
            command(
                CommandType.JOG_MOVE, 1,
                delta=delta, ticks_per_rev=ticks_per_rev,
            )
        )

    def test_touch_state_is_tracked(self) -> None:
        self.deck.execute(command(CommandType.JOG_TOUCH, 1, pressed=True))
        self.assertTrue(self.deck.state.jog_touch)
        self.deck.execute(command(CommandType.JOG_TOUCH, 1, pressed=False))
        self.assertFalse(self.deck.state.jog_touch)

    def test_scratch_moves_one_beat_per_revolution(self) -> None:
        self.deck.execute(command(CommandType.JOG_TOUCH, 1, pressed=True))
        self.move(STEPS_PER_REV)
        # 120 BPM: ein Beat sind 0.5 s.
        self.assertAlmostEqual(self.deck.state.position_s, 30.5, places=3)

    def test_a_single_step_is_an_eight_hundredth_beat(self) -> None:
        self.deck.execute(command(CommandType.JOG_TOUCH, 1, pressed=True))
        self.move(1)
        self.assertAlmostEqual(
            self.deck.state.position_s, 30.0 + 0.5 / STEPS_PER_REV, places=6
        )

    def test_a_hundred_steps_are_an_eighth_beat(self) -> None:
        self.deck.execute(command(CommandType.JOG_TOUCH, 1, pressed=True))
        self.move(100)
        self.assertAlmostEqual(
            self.deck.state.position_s, 30.0 + 0.5 / 8, places=6
        )

    def test_scratch_follows_the_tempo_of_the_track(self) -> None:
        deck = Deck(2)
        deck.load_track(
            make_track(
                original_bpm=60.0,
                beat_grid=BeatGrid(first_beat_s=0.0, bpm=60.0),
            )
        )
        deck.execute(command(CommandType.SEEK, 2, position_s=30.0))
        deck.execute(command(CommandType.JOG_TOUCH, 2, pressed=True))
        deck.execute(
            command(
                CommandType.JOG_MOVE, 2,
                delta=STEPS_PER_REV, ticks_per_rev=STEPS_PER_REV,
            )
        )
        # 60 BPM: ein Beat sind 1.0 s.
        self.assertAlmostEqual(deck.state.position_s, 31.0, places=3)

    def test_without_a_beatgrid_the_platter_length_is_used(self) -> None:
        deck = Deck(2)
        deck.load_track(make_track(beat_grid=None))
        deck.execute(command(CommandType.SEEK, 2, position_s=30.0))
        deck.execute(command(CommandType.JOG_TOUCH, 2, pressed=True))
        deck.execute(
            command(
                CommandType.JOG_MOVE, 2,
                delta=STEPS_PER_REV, ticks_per_rev=STEPS_PER_REV,
            )
        )
        self.assertAlmostEqual(deck.state.position_s, 31.8, places=3)

    def test_rim_move_bends_less_than_scratch(self) -> None:
        self.move(STEPS_PER_REV)
        self.assertAlmostEqual(self.deck.state.position_s, 30.25, places=3)

    def test_jog_mode_toggles(self) -> None:
        self.assertIs(self.deck.state.jog_mode, JogMode.VINYL)
        self.deck.execute(command(CommandType.JOG_MODE_TOGGLE, 1))
        self.assertIs(self.deck.state.jog_mode, JogMode.CDJ)


class SyncStateTests(unittest.TestCase):
    def test_flags_are_real_state_not_gui_toggles(self) -> None:
        deck = Deck(1)
        for command_type, attribute in (
            (CommandType.SYNC_TOGGLE, "sync"),
            (CommandType.MASTER_SET, "is_master"),
            (CommandType.QUANTIZE_TOGGLE, "quantize"),
            (CommandType.SLIP_TOGGLE, "slip"),
        ):
            before = getattr(deck.state, attribute)
            deck.execute(command(command_type, 1))
            self.assertNotEqual(getattr(deck.state, attribute), before)


class UnsupportedTests(unittest.TestCase):
    def test_missing_backends_are_recorded_not_faked(self) -> None:
        deck = Deck(1)
        deck.load_track(make_track())
        deck.execute(command(CommandType.MEMORY, 1))
        deck.execute(command(CommandType.KEY_SYNC, 1))
        self.assertTrue(any("MEMORY" in entry for entry in deck.unsupported))
        self.assertTrue(any("KEY_SYNC" in entry for entry in deck.unsupported))

    def test_track_search_is_no_longer_unsupported(self) -> None:
        """TRACK SEARCH vermerkt nichts mehr, es fordert den Wechsel an.

        Das Deck kennt weiterhin keine Trackliste - es legt den Wunsch in
        ``track_requests``, und die Anwendung fuehrt ihn aus.
        """
        deck = Deck(1)
        deck.load_track(make_track())
        deck.execute(command(CommandType.TRACK_SEARCH, 1, direction=1))
        self.assertFalse(
            any("TRACK_SEARCH" in entry for entry in deck.unsupported)
        )
        self.assertEqual(deck.take_track_requests(), [1])

    def test_commands_for_other_decks_are_ignored(self) -> None:
        deck = Deck(1)
        deck.load_track(make_track())
        deck.execute(command(CommandType.PLAY_PAUSE, 2))
        self.assertIs(deck.state.play_state, PlayState.STOPPED)


# --------------------------------------------------------------------------


class MappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = InputLayer()
        self.commands: list[DeckCommand] = []
        self.mapper = InputMapper(1, self.commands.append)
        self.layer.subscribe(self.mapper.handle_event)

    def test_play_maps_on_press_only(self) -> None:
        self.layer.press(ids.PLAY)
        self.layer.release(ids.PLAY)
        self.assertEqual([c.type for c in self.commands],
                         [CommandType.PLAY_PAUSE])

    def test_cue_maps_press_and_release(self) -> None:
        self.layer.press(ids.CUE)
        self.layer.release(ids.CUE)
        self.assertEqual([c.type for c in self.commands],
                         [CommandType.CUE, CommandType.CUE])
        self.assertEqual([c.pressed for c in self.commands], [True, False])

    def test_pads_map_to_index(self) -> None:
        for pad in ids.PADS:
            self.layer.press(pad)
            self.layer.release(pad)
        pad_commands = [c for c in self.commands if c.type is CommandType.PAD]
        indexes = [c.get("index") for c in pad_commands if c.pressed]
        self.assertEqual(indexes, list(range(8)))

    def test_shift_is_a_modifier_not_a_command(self) -> None:
        self.layer.press(ids.SHIFT)
        self.assertEqual(self.commands, [])
        self.assertTrue(self.mapper.shift)
        self.layer.press(ids.PLAY)
        self.assertTrue(self.commands[0].get("shift"))
        self.layer.release(ids.SHIFT)
        self.assertFalse(self.mapper.shift)

    def test_tempo_fader_maps_to_value(self) -> None:
        self.layer.set_analog(ids.TEMPO_FADER, 0.75)
        cmd = self.commands[-1]
        self.assertIs(cmd.type, CommandType.TEMPO_SET)
        self.assertAlmostEqual(cmd.get("value"), 0.75)

    def test_jog_maps_with_velocity(self) -> None:
        self.layer.jog_move(ids.JOG_MOVE, 5)
        self.layer.jog_move(ids.JOG_MOVE, 5)
        cmd = self.commands[-1]
        self.assertIs(cmd.type, CommandType.JOG_MOVE)
        self.assertEqual(cmd.get("delta"), 5)
        self.assertIn("velocity", cmd.params)

    def test_jog_touch_maps(self) -> None:
        self.layer.jog_touch(ids.JOG_TOUCH, True)
        self.assertIs(self.commands[-1].type, CommandType.JOG_TOUCH)
        self.assertTrue(self.commands[-1].pressed)

    def test_browse_encoder_maps(self) -> None:
        self.layer.rotate(ids.BROWSE_ROTATE, -2)
        self.assertIs(self.commands[-1].type, CommandType.BROWSE_ROTATE)
        self.assertEqual(self.commands[-1].get("delta"), -2)

    def test_direction_switch_maps(self) -> None:
        self.layer.set_switch(ids.DIRECTION, "REV")
        self.assertIs(self.commands[-1].type, CommandType.DIRECTION)
        self.assertIs(self.commands[-1].get("position"), Direction.REV)

    def test_screen_buttons_map_to_views(self) -> None:
        self.layer.press(ids.BROWSE)
        self.assertIs(self.commands[-1].type, CommandType.VIEW)
        self.assertIs(self.commands[-1].get("view"), Views.BROWSE)

    def test_the_two_mode_buttons_switch_the_pad_mode(self) -> None:
        """Die Modustaster senden nur den Modus - keine Pad-Funktion.

        Welche Funktion ein Pad danach ausloest, entscheidet allein
        ``Deck._cmd_pad`` anhand von ``pad_mode``.
        """
        self.layer.press(ids.HOT_CUE)
        self.assertIs(self.commands[-1].type, CommandType.PAD_MODE)
        self.assertIs(self.commands[-1].get("mode"), PadMode.HOT_CUE)
        self.layer.press(ids.BEAT_JUMP)
        self.assertIs(self.commands[-1].type, CommandType.PAD_MODE)
        self.assertIs(self.commands[-1].get("mode"), PadMode.BEAT_LOOP)

    def test_the_round_beat_loop_buttons_select_four_or_eight_beats(self) -> None:
        self.layer.press(ids.BEAT_LOOP_4)
        self.assertIs(self.commands[-1].type, CommandType.BEAT_LOOP)
        self.assertEqual(self.commands[-1].get("beats"), 4.0)
        self.layer.press(ids.BEAT_LOOP_8)
        self.assertIs(self.commands[-1].type, CommandType.BEAT_LOOP)
        self.assertEqual(self.commands[-1].get("beats"), 8.0)

    def test_call_buttons_map_with_direction(self) -> None:
        self.layer.press(ids.CUE_LOOP_CALL_PREV)
        self.assertIs(self.commands[-1].type, CommandType.CUE_LOOP_CALL)
        self.assertEqual(self.commands[-1].get("direction"), -1)
        self.layer.press(ids.CUE_LOOP_CALL_NEXT)
        self.assertEqual(self.commands[-1].get("direction"), +1)

    def test_every_input_control_is_mapped_or_explicitly_open(self) -> None:
        from virtual_cdj.core import controls
        from virtual_cdj.deck.mapping import (
            _MOMENTARY, _ON_PRESS, _PAD_INDEX, UNMAPPED,
        )

        analog_and_special = {
            ids.TEMPO_FADER, ids.VINYL_SPEED_ADJUST,
            ids.BROWSE_ROTATE, ids.JOG_MOVE, ids.DIRECTION,
        }
        covered = (
            set(_ON_PRESS) | set(_MOMENTARY) | set(_PAD_INDEX)
            | set(UNMAPPED) | analog_and_special
        )
        missing = sorted(set(controls.INPUT_CONTROLS) - covered)
        self.assertEqual(missing, [], f"nicht zugeordnet: {missing}")

    def test_command_carries_source_as_origin(self) -> None:
        self.layer.press(ids.PLAY, Source.HARDWARE)
        self.assertEqual(self.commands[-1].origin, "HARDWARE")

    def test_command_carries_the_triggering_control_id(self) -> None:
        self.layer.press(ids.PLAY)
        self.assertEqual(self.commands[-1].control_id, ids.PLAY)


class EndToEndTests(unittest.TestCase):
    """Bedienelement -> InputLayer -> Mapper -> Deck -> DeckState."""

    def test_button_press_changes_deck_state(self) -> None:
        deck = Deck(1)
        deck.load_track(make_track())
        layer = InputLayer()
        mapper = InputMapper(1, deck.execute)
        layer.subscribe(mapper.handle_event)

        layer.press(ids.PLAY)
        self.assertIs(deck.state.play_state, PlayState.PLAYING)

        layer.set_analog(ids.TEMPO_FADER, 1.0)
        self.assertAlmostEqual(deck.state.current_bpm, 132.0)

        layer.press(ids.BEAT_SYNC)
        self.assertTrue(deck.state.sync)

    def test_same_chain_works_from_hardware_source(self) -> None:
        from virtual_cdj.sources.hardware import HardwareSource

        deck = Deck(1)
        deck.load_track(make_track())
        layer = InputLayer()
        mapper = InputMapper(1, deck.execute)
        layer.subscribe(mapper.handle_event)

        HardwareSource(layer).feed("D PLAY 1\nD PLAY 0\n")
        self.assertIs(deck.state.play_state, PlayState.PLAYING)


class ProviderTests(unittest.TestCase):
    def test_local_provider_forwards(self) -> None:
        deck = Deck(2)
        provider = LocalDeckStateProvider(deck)
        self.assertEqual(provider.deck_id, 2)

        seen: list[int] = []
        provider.subscribe(lambda s: seen.append(s.generation))
        provider.send(command(CommandType.QUANTIZE_TOGGLE, 2))
        self.assertTrue(provider.get_state().quantize)
        self.assertEqual(len(seen), 1)

    def test_network_provider_starts_empty_and_refuses_send(self) -> None:
        provider = NetworkDeckStateProvider(4, "tcp://host:9000")
        self.assertEqual(provider.deck_id, 4)
        self.assertFalse(provider.get_state().has_track)
        with self.assertRaises(NotImplementedError):
            provider.send(command(CommandType.PLAY_PAUSE, 4))

    def test_network_provider_publishes_received_state(self) -> None:
        provider = NetworkDeckStateProvider(4, "tcp://host:9000")
        seen: list[int] = []
        provider.subscribe(lambda s: seen.append(s.deck_id))
        provider.on_state_received(empty_state(4))
        self.assertEqual(seen, [4])


if __name__ == "__main__":
    unittest.main()


class BeatGridCorrectionTests(unittest.TestCase):
    """Manuelle Korrektur (Abschnitt 6).

    Eine Bedienoberflaeche dafuer gibt es noch nicht - das Datenmodell muss
    sie aber ermoeglichen.
    """

    def setUp(self) -> None:
        self.grid = BeatGrid(first_beat_s=0.1, bpm=120.0, beats_per_bar=4)

    def test_shift_moves_the_whole_grid(self) -> None:
        shifted = self.grid.shifted(0.05)
        self.assertAlmostEqual(shifted.first_beat_s, 0.15)
        self.assertAlmostEqual(shifted.bpm, 120.0)
        self.assertAlmostEqual(shifted.beat_time(0), 0.15)
        self.assertAlmostEqual(shifted.beat_time(4), 2.15)

    def test_shift_works_on_explicit_beat_lists(self) -> None:
        grid = BeatGrid(beats_s=(0.0, 0.5, 1.0), beats_per_bar=4)
        shifted = grid.shifted(0.25)
        self.assertEqual(shifted.beats_s, (0.25, 0.75, 1.25))

    def test_bpm_change_keeps_the_anchor(self) -> None:
        # Anker auf Beat 4 (bei 2.1 s), danach halbes Tempo.
        changed = self.grid.with_bpm(60.0, anchor_s=2.1)
        self.assertAlmostEqual(changed.bpm, 60.0)
        self.assertAlmostEqual(changed.beat_interval_s, 1.0)
        # Der Ankerbeat muss weiterhin auf einem Beat liegen.
        number = changed.beat_number_at(2.1)
        self.assertAlmostEqual(changed.beat_time(number), 2.1, places=6)

    def test_bpm_change_discards_stale_beat_list(self) -> None:
        grid = BeatGrid(
            first_beat_s=0.0, bpm=120.0, beats_s=(0.0, 0.5, 1.0)
        )
        changed = grid.with_bpm(140.0)
        self.assertEqual(changed.beats_s, ())
        self.assertAlmostEqual(changed.bpm, 140.0)

    def test_bpm_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            self.grid.with_bpm(0.0)

    def test_downbeat_can_be_set_by_hand(self) -> None:
        # Beat 2 liegt bei 0.6 s und soll Taktanfang werden.
        corrected = self.grid.with_downbeat_at(0.6)
        self.assertEqual(corrected.first_downbeat_index, 1)
        self.assertEqual(corrected.bar_and_beat_at(0.6)[1], 1)
        self.assertEqual(corrected.bar_and_beat_at(0.1)[1], 4)

    def test_downbeat_outside_the_grid_changes_nothing(self) -> None:
        corrected = self.grid.with_downbeat_at(-5.0)
        self.assertIs(corrected, self.grid)

    def test_resegment_from_a_point_gives_variable_tempo(self) -> None:
        # Ab 2.0 s auf 90 BPM umstellen, Track ist 10 s lang.
        changed = self.grid.resegmented_from(2.0, 90.0, until_s=10.0)
        self.assertTrue(changed.beats_s)
        self.assertTrue(changed.is_valid)

        # Vor dem Umschaltpunkt gilt weiter 120 BPM.
        early = [t for t in changed.beats_s if t < 2.0]
        self.assertGreater(len(early), 2)
        self.assertAlmostEqual(early[1] - early[0], 0.5, places=6)

        # Danach 90 BPM, also 0.6667 s Abstand.
        late = [t for t in changed.beats_s if t >= 2.0]
        self.assertGreater(len(late), 1)
        self.assertAlmostEqual(late[1] - late[0], 60.0 / 90.0, places=6)

    def test_resegment_needs_a_valid_grid(self) -> None:
        empty = BeatGrid()
        self.assertIs(empty.resegmented_from(1.0, 120.0, 10.0), empty)
        with self.assertRaises(ValueError):
            self.grid.resegmented_from(1.0, -1.0, 10.0)

    def test_resegment_refuses_to_guess_the_track_end(self) -> None:
        # Ohne until_s ist unbekannt, wie weit Beats zu erzeugen sind.
        with self.assertRaises(ValueError):
            self.grid.resegmented_from(2.0, 90.0)
        # Mit expliziter Beatliste ist deren Ende die Grenze.
        listed = BeatGrid(beats_s=(0.0, 0.5, 1.0, 1.5, 2.0), bpm=120.0)
        self.assertTrue(listed.resegmented_from(1.0, 90.0).beats_s)

    def test_corrections_return_new_objects(self) -> None:
        """Das Grid ist unveraenderlich - Korrekturen liefern Kopien."""
        for corrected in (
            self.grid.shifted(0.1),
            self.grid.with_bpm(128.0),
            self.grid.with_downbeat_at(0.6),
            self.grid.resegmented_from(1.0, 100.0, 10.0),
        ):
            self.assertIsNot(corrected, self.grid)
        self.assertAlmostEqual(self.grid.first_beat_s, 0.1)
        self.assertAlmostEqual(self.grid.bpm, 120.0)

    def test_corrected_grid_drives_the_deck(self) -> None:
        """Nach einer Korrektur muss der Deck-Zustand ihr folgen."""
        import dataclasses

        deck = Deck(1)
        deck.load_track(make_track(beat_grid=self.grid))
        deck.execute(command(CommandType.SEEK, 1, position_s=2.1))
        self.assertEqual(deck.state.beat, 1)

        corrected = self.grid.with_downbeat_at(0.6)
        deck.load_track(
            dataclasses.replace(deck.state.track, beat_grid=corrected)
        )
        deck.execute(command(CommandType.SEEK, 1, position_s=2.1))
        self.assertEqual(deck.state.beat, 4)
