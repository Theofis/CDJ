"""Bedienvorgaenge aus dem CDJ-3000-Handbuch, die spaeter nachgezogen wurden.

Sieben Punkte aus dem Feature-Gap-Audit, jeder mit seiner Handbuchstelle:

* TRACK SEARCH |<< / >>|                    S. 48
* Loop Move (Beat Jump bei laufendem Loop)  S. 66/67
* BEAT JUMP BEAT VALUE 1/2 ... 64           S. 66/78
* LOOP IN/CUE setzt den Cue-Punkt           S. 54/57
* Loop-Adjust ueber SEARCH + 10-s-Automatik S. 58
* Hot Cue speichert einen Loop              S. 62
* Jogwheel invertiert bei Reverse           S. 47

Geprueft wird ausschliesslich ueber ``DeckCommand`` - denselben Weg, den
ein Tastendruck am Bedienfeld und spaeter die Hardware nimmt. Die Uhr wird
gestellt, nicht abgewartet.
"""

from __future__ import annotations

import unittest

from virtual_cdj.core import ids
from virtual_cdj.core.input_layer import InputLayer
from virtual_cdj.deck.commands import CommandType, command
from virtual_cdj.deck.engine import (
    JOG_BEND_SECONDS_PER_REV,
    LOOP_ADJUST_TIMEOUT_S,
    Deck,
)
from virtual_cdj.deck.loop import LoopEngine
from virtual_cdj.deck.mapping import InputMapper
from virtual_cdj.deck.mode_manager import OperatingMode
from virtual_cdj.deck.state import (
    BEAT_JUMP_BEAT_VALUES,
    BeatGrid,
    CueKind,
    Direction,
    HotCue,
    JogMode,
    LoopAdjust,
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
        self.layer.subscribe(InputMapper(1, self.deck.execute).handle_event)

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

    # -- Bedienung -------------------------------------------------------

    def send(self, command_type: CommandType, **params) -> None:
        self.deck.execute(command(command_type, 1, "TEST", **params))

    def tap(self, control_id: str) -> None:
        self.layer.press(control_id)
        self.layer.release(control_id)

    def seek(self, position_s: float) -> None:
        self.send(CommandType.SEEK, position_s=position_s)

    def play(self) -> None:
        if not self.state.is_playing:
            self.send(CommandType.PLAY_PAUSE)

    def turn(self, revolutions: float) -> None:
        self.send(
            CommandType.JOG_MOVE,
            delta=int(round(revolutions * STEPS_PER_REV)),
            ticks_per_rev=STEPS_PER_REV,
        )

    def beat_loop(self, beats: float) -> None:
        self.send(CommandType.BEAT_LOOP, beats=beats)

    def run(self, seconds: float, step: float = FRAME_S) -> None:
        for _ in range(int(round(seconds / step))):
            self.now += step
            self.deck.tick()

    def wait(self, seconds: float) -> None:
        """Zeit vergehen lassen **ohne** Bedienung - ein einziger Takt.

        Fuer die 10-Sekunden-Automatik: viele kleine Takte wuerden dasselbe
        tun, aber der Test soll zeigen, dass allein die verstrichene Zeit
        zaehlt und nicht die Anzahl der Takte.
        """
        self.now += seconds
        self.deck.tick()


# --------------------------------------------------------------------------
# 1. TRACK SEARCH (Handbuch S. 48)
# --------------------------------------------------------------------------


class TrackSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig()

    def test_back_jumps_to_the_start_of_the_running_track(self) -> None:
        self.rig.seek(30.0)
        self.rig.send(CommandType.TRACK_SEARCH, direction=-1)
        self.assertAlmostEqual(self.rig.position, 0.0, places=6)
        # Kein Trackwechsel angefordert - das war der erste Druck.
        self.assertEqual(self.rig.deck.take_track_requests(), [])

    def test_a_second_press_asks_for_the_previous_track(self) -> None:
        self.rig.seek(30.0)
        self.rig.send(CommandType.TRACK_SEARCH, direction=-1)
        self.rig.send(CommandType.TRACK_SEARCH, direction=-1)
        self.assertEqual(self.rig.deck.take_track_requests(), [-1])

    def test_forward_always_asks_for_the_next_track(self) -> None:
        self.rig.seek(30.0)
        self.rig.send(CommandType.TRACK_SEARCH, direction=+1)
        self.assertEqual(self.rig.deck.take_track_requests(), [1])
        self.assertAlmostEqual(self.rig.position, 30.0, places=6)

    def test_the_restart_leaves_a_running_loop(self) -> None:
        """Die Loop-V&V verlangt: TRACK SEARCH verlaesst den Loop."""
        self.rig.seek(10.0)
        self.rig.beat_loop(4.0)
        self.rig.play()
        self.assertTrue(self.rig.loop.active)

        self.rig.send(CommandType.TRACK_SEARCH, direction=-1)
        self.assertFalse(self.rig.loop.active)
        self.assertIs(self.rig.loop.exit_reason, LoopExitReason.JUMPED_OUT)
        self.assertAlmostEqual(self.rig.position, 0.0, places=6)

    def test_a_track_change_ends_a_slip_action_without_a_jump(self) -> None:
        """Abschnitt 22: ein bewusster Trackwechsel ist keine Slip-Aktion."""
        self.rig.seek(30.0)
        self.rig.play()
        self.rig.send(CommandType.SLIP_TOGGLE)
        self.rig.send(CommandType.JOG_TOUCH, pressed=True)
        self.rig.run(2.0)
        self.assertTrue(self.rig.state.slip_active)

        self.rig.send(CommandType.TRACK_SEARCH, direction=+1)
        self.assertFalse(self.rig.state.slip_active)
        self.assertIsNone(self.rig.state.slip_position_s)

    def test_without_a_track_nothing_happens(self) -> None:
        deck = Deck(2)
        deck.execute(command(CommandType.TRACK_SEARCH, 2, direction=+1))
        self.assertEqual(deck.take_track_requests(), [])

    def test_the_button_reaches_the_deck(self) -> None:
        self.rig.seek(30.0)
        self.rig.tap(ids.TRACK_SEARCH_NEXT)
        self.assertEqual(self.rig.deck.take_track_requests(), [1])


# --------------------------------------------------------------------------
# 2. Loop Move (Handbuch S. 66/67)
# --------------------------------------------------------------------------


class LoopMoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig()
        self.rig.seek(10.0)
        self.rig.beat_loop(4.0)
        self.rig.play()

    def test_beat_jump_moves_the_loop_instead_of_jumping_out(self) -> None:
        before = self.rig.loop
        self.assertAlmostEqual(before.in_s, 10.0)
        self.assertAlmostEqual(before.out_s, 12.0)

        self.rig.send(CommandType.BEAT_JUMP, direction=+1, beats=4.0)

        loop = self.rig.loop
        self.assertTrue(loop.active, "Loop Move darf den Loop nicht beenden")
        self.assertIsNone(loop.exit_reason)
        self.assertAlmostEqual(loop.in_s, 12.0, places=9)
        self.assertAlmostEqual(loop.out_s, 14.0, places=9)

    def test_the_length_stays_exactly_the_same(self) -> None:
        length = self.rig.loop.length_s
        self.rig.send(CommandType.BEAT_JUMP, direction=+1, beats=8.0)
        self.assertAlmostEqual(self.rig.loop.length_s, length, places=9)
        self.assertAlmostEqual(self.rig.loop.beats, 4.0)

    def test_the_playback_moves_with_the_loop(self) -> None:
        self.rig.run(0.5)
        offset_in_loop = self.rig.position - self.rig.loop.in_s

        self.rig.send(CommandType.BEAT_JUMP, direction=+1, beats=4.0)

        loop = self.rig.loop
        self.assertAlmostEqual(
            self.rig.position - loop.in_s, offset_in_loop, places=6
        )
        # Und damit weiterhin innerhalb des Loops.
        self.assertGreaterEqual(self.rig.position, loop.in_s - 1e-6)
        self.assertLess(self.rig.position, loop.out_s + 1e-6)

    def test_backwards_moves_the_loop_backwards(self) -> None:
        self.rig.send(CommandType.BEAT_JUMP, direction=-1, beats=4.0)
        self.assertAlmostEqual(self.rig.loop.in_s, 8.0, places=9)
        self.assertAlmostEqual(self.rig.loop.out_s, 10.0, places=9)

    def test_a_move_before_the_track_start_is_refused(self) -> None:
        rig = Rig()
        rig.seek(1.0)
        rig.beat_loop(4.0)
        before = rig.loop
        rig.send(CommandType.BEAT_JUMP, direction=-1, beats=32.0)
        self.assertAlmostEqual(rig.loop.in_s, before.in_s, places=9)
        self.assertAlmostEqual(rig.loop.out_s, before.out_s, places=9)

    def test_without_a_loop_beat_jump_still_jumps(self) -> None:
        rig = Rig()
        rig.seek(10.0)
        rig.send(CommandType.BEAT_JUMP, direction=+1, beats=4.0)
        self.assertAlmostEqual(rig.position, 12.0, places=6)

    def test_reloop_brings_back_the_moved_loop(self) -> None:
        self.rig.send(CommandType.BEAT_JUMP, direction=+1, beats=4.0)
        self.rig.send(CommandType.RELOOP_EXIT)     # verlassen
        self.rig.send(CommandType.RELOOP_EXIT)     # wieder aufrufen
        self.assertTrue(self.rig.loop.active)
        self.assertAlmostEqual(self.rig.loop.in_s, 12.0, places=9)

    def test_the_engine_reports_the_offset(self) -> None:
        engine = LoopEngine(grid=BeatGrid(first_beat_s=0.0, bpm=BPM))
        loop = self.rig.loop
        moved, offset = engine.moved(loop, 4.0)
        self.assertAlmostEqual(offset, 4 * BEAT_S, places=9)
        self.assertAlmostEqual(moved.in_s - loop.in_s, offset, places=9)


# --------------------------------------------------------------------------
# 3. BEAT JUMP BEAT VALUE (Handbuch S. 66/78)
# --------------------------------------------------------------------------


class BeatJumpBeatValueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig()

    def test_the_manual_values_are_supported(self) -> None:
        self.assertEqual(
            BEAT_JUMP_BEAT_VALUES, (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0)
        )

    def test_the_default_is_sixteen(self) -> None:
        self.assertAlmostEqual(self.rig.state.beat_jump_beats, 16.0)

    def test_every_value_can_be_set(self) -> None:
        for beats in BEAT_JUMP_BEAT_VALUES:
            with self.subTest(beats=beats):
                self.rig.send(CommandType.BEAT_JUMP_BEATS, beats=beats)
                self.assertAlmostEqual(self.rig.state.beat_jump_beats, beats)

    def test_without_a_value_the_command_cycles(self) -> None:
        seen = [self.rig.state.beat_jump_beats]
        for _ in range(len(BEAT_JUMP_BEAT_VALUES) - 1):
            self.rig.send(CommandType.BEAT_JUMP_BEATS)
            seen.append(self.rig.state.beat_jump_beats)
        self.assertEqual(sorted(seen), sorted(BEAT_JUMP_BEAT_VALUES))
        self.rig.send(CommandType.BEAT_JUMP_BEATS)
        self.assertAlmostEqual(self.rig.state.beat_jump_beats, seen[0])

    def test_call_delete_and_beat_jump_change_the_value(self) -> None:
        """CALL/DELETE gehalten: die Taste springt nicht, sie stellt ein."""
        self.rig.seek(30.0)
        self.rig.send(CommandType.DELETE, pressed=True)
        self.rig.send(CommandType.BEAT_JUMP, direction=+1)
        self.assertAlmostEqual(self.rig.state.beat_jump_beats, 32.0)
        self.assertAlmostEqual(self.rig.position, 30.0, places=6)

        self.rig.send(CommandType.BEAT_JUMP, direction=-1)
        self.assertAlmostEqual(self.rig.state.beat_jump_beats, 16.0)
        self.rig.send(CommandType.DELETE, pressed=False)

    def test_the_step_stops_at_the_ends(self) -> None:
        self.rig.send(CommandType.BEAT_JUMP_BEATS, beats=64.0)
        self.rig.send(CommandType.DELETE, pressed=True)
        self.rig.send(CommandType.BEAT_JUMP, direction=+1)
        self.assertAlmostEqual(self.rig.state.beat_jump_beats, 64.0)

        self.rig.send(CommandType.BEAT_JUMP_BEATS, beats=0.5)
        self.rig.send(CommandType.BEAT_JUMP, direction=-1)
        self.assertAlmostEqual(self.rig.state.beat_jump_beats, 0.5)

    def test_the_set_value_drives_the_jump(self) -> None:
        self.rig.seek(10.0)
        self.rig.send(CommandType.BEAT_JUMP_BEATS, beats=2.0)
        self.rig.send(CommandType.BEAT_JUMP, direction=+1)
        self.assertAlmostEqual(self.rig.position, 10.0 + 2 * BEAT_S, places=6)

    def test_an_unknown_value_snaps_to_the_nearest(self) -> None:
        self.rig.send(CommandType.BEAT_JUMP_BEATS, beats=5.0)
        self.assertAlmostEqual(self.rig.state.beat_jump_beats, 4.0)

    def test_the_touch_panel_still_jumps_while_delete_is_held(self) -> None:
        """Das Panel schickt ``beats`` mit und meint damit den Sprung."""
        self.rig.seek(10.0)
        self.rig.send(CommandType.DELETE, pressed=True)
        self.rig.send(CommandType.BEAT_JUMP, direction=+1, beats=4.0)
        self.assertAlmostEqual(self.rig.position, 12.0, places=6)
        self.assertAlmostEqual(self.rig.state.beat_jump_beats, 16.0)


# --------------------------------------------------------------------------
# 4. LOOP IN/CUE setzt den Cue-Punkt (Handbuch S. 54/57)
# --------------------------------------------------------------------------


class LoopInSetsCueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig()

    def test_loop_in_sets_loop_start_and_cue_point(self) -> None:
        self.rig.seek(30.0)
        self.rig.play()
        self.rig.send(CommandType.LOOP_IN)
        self.assertAlmostEqual(self.rig.loop.in_s, 30.0, places=6)
        self.assertAlmostEqual(self.rig.state.cue_point_s, 30.0, places=6)

    def test_both_points_land_on_the_same_quantised_spot(self) -> None:
        self.rig.send(CommandType.QUANTIZE_TOGGLE)
        self.rig.seek(30.2)
        self.rig.play()
        self.rig.send(CommandType.LOOP_IN)
        self.assertAlmostEqual(self.rig.loop.in_s, 30.0, places=6)
        self.assertAlmostEqual(self.rig.state.cue_point_s, 30.0, places=6)

    def test_the_adjust_toggle_leaves_the_cue_alone(self) -> None:
        # Cue-Punkt bei 5 s setzen: pausiert an der Stelle, CUE druecken.
        self.rig.seek(5.0)
        self.rig.send(CommandType.CUE, pressed=True)
        self.rig.send(CommandType.CUE, pressed=False)
        self.assertAlmostEqual(self.rig.state.cue_point_s, 5.0, places=6)

        self.rig.seek(10.0)
        self.rig.beat_loop(4.0)
        self.rig.play()
        self.assertTrue(self.rig.loop.active)

        self.rig.send(CommandType.LOOP_IN)     # schaltet IN-Adjust ein
        self.assertIs(self.rig.loop.adjust, LoopAdjust.IN)
        self.assertAlmostEqual(self.rig.state.cue_point_s, 5.0, places=9)

    def test_cue_returns_to_the_loop_start(self) -> None:
        """Der praktische Nutzen: CUE fuehrt zurueck an den Loop-Anfang."""
        self.rig.seek(30.0)
        self.rig.play()
        self.rig.send(CommandType.LOOP_IN)
        self.rig.run(1.0)
        self.rig.send(CommandType.CUE, pressed=True)
        self.assertAlmostEqual(self.rig.position, 30.0, places=6)


# --------------------------------------------------------------------------
# 5. Loop-Adjust: SEARCH und die 10-Sekunden-Automatik (Handbuch S. 58)
# --------------------------------------------------------------------------


class LoopAdjustTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig()
        self.rig.seek(10.0)
        self.rig.beat_loop(4.0)
        self.rig.play()
        self.rig.send(CommandType.LOOP_OUT)    # OUT-Adjust ein
        self.assertIs(self.rig.loop.adjust, LoopAdjust.OUT)

    def test_search_moves_the_loop_point_instead_of_searching(self) -> None:
        before = self.rig.loop
        position = self.rig.position

        self.rig.send(CommandType.SEARCH, direction=+1, pressed=True)
        self.rig.run(1.0)
        self.rig.send(CommandType.SEARCH, direction=+1, pressed=False)

        # Eine Sekunde Halten verschiebt um einen Beat.
        self.assertAlmostEqual(
            self.rig.loop.out_s, before.out_s + BEAT_S, delta=0.02
        )
        # Die Wiedergabe bleibt, wo sie war - es wurde nicht gesucht.
        self.assertAlmostEqual(self.rig.position, position, delta=0.02)
        self.assertTrue(self.rig.loop.active)

    def test_search_back_moves_the_point_back(self) -> None:
        before = self.rig.loop
        self.rig.send(CommandType.SEARCH, direction=-1, pressed=True)
        self.rig.run(0.5)
        self.rig.send(CommandType.SEARCH, direction=-1, pressed=False)
        self.assertLess(self.rig.loop.out_s, before.out_s)

    def test_without_an_adjust_search_still_searches(self) -> None:
        self.rig.send(CommandType.LOOP_OUT)    # Adjust wieder aus
        self.assertIs(self.rig.loop.adjust, LoopAdjust.NONE)
        before = self.rig.loop.out_s

        self.rig.send(CommandType.SEARCH, direction=+1, pressed=True)
        self.rig.run(0.5)
        self.rig.send(CommandType.SEARCH, direction=+1, pressed=False)

        self.assertAlmostEqual(self.rig.loop.out_s, before, places=9)

    def test_the_adjust_ends_after_ten_seconds_without_operation(self) -> None:
        self.rig.wait(LOOP_ADJUST_TIMEOUT_S + 0.1)
        self.assertIs(self.rig.loop.adjust, LoopAdjust.NONE)
        # Der Loop selbst laeuft weiter und ist gesichert.
        self.assertTrue(self.rig.loop.active)
        self.assertTrue(self.rig.loop.has_last)

    def test_the_adjust_survives_just_under_ten_seconds(self) -> None:
        self.rig.wait(LOOP_ADJUST_TIMEOUT_S - 0.5)
        self.assertIs(self.rig.loop.adjust, LoopAdjust.OUT)

    def test_any_operation_restarts_the_ten_seconds(self) -> None:
        self.rig.wait(LOOP_ADJUST_TIMEOUT_S - 0.5)
        self.rig.send(CommandType.QUANTIZE_TOGGLE)   # irgendeine Bedienung
        self.rig.wait(LOOP_ADJUST_TIMEOUT_S - 0.5)
        self.assertIs(self.rig.loop.adjust, LoopAdjust.OUT)

    def test_turning_the_jog_restarts_the_ten_seconds(self) -> None:
        self.rig.wait(LOOP_ADJUST_TIMEOUT_S - 0.5)
        self.rig.turn(0.1)
        self.rig.wait(LOOP_ADJUST_TIMEOUT_S - 0.5)
        self.assertIs(self.rig.loop.adjust, LoopAdjust.OUT)

    def test_an_adjust_without_a_loop_does_not_hijack_search(self) -> None:
        """Ein Adjust-Modus ohne Loop darf SEARCH nicht umwidmen."""
        rig = Rig()
        rig.seek(30.0)
        rig.send(CommandType.LOOP_IN)          # nur Anfang, kein Loop
        rig.send(CommandType.SEARCH, direction=+1, pressed=True)
        rig.run(0.5)
        rig.send(CommandType.SEARCH, direction=+1, pressed=False)
        self.assertGreater(rig.position, 30.0)


# --------------------------------------------------------------------------
# 6. Hot Cue speichert bei laufendem Loop einen Loop (Handbuch S. 62)
# --------------------------------------------------------------------------


class HotCueLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig()

    def test_a_pad_during_loop_playback_stores_the_loop(self) -> None:
        self.rig.seek(10.0)
        self.rig.beat_loop(4.0)
        self.rig.play()

        self.rig.send(CommandType.PAD, index=0, pressed=True)

        cue = self.rig.state.track.hot_cue(0)
        self.assertIsNotNone(cue)
        self.assertIs(cue.kind, CueKind.LOOP)
        self.assertAlmostEqual(cue.position_s, 10.0, places=9)
        self.assertAlmostEqual(cue.loop_end_s, 12.0, places=9)

    def test_without_a_loop_the_pad_stores_a_point(self) -> None:
        self.rig.seek(10.0)
        self.rig.play()
        self.rig.send(CommandType.PAD, index=0, pressed=True)
        cue = self.rig.state.track.hot_cue(0)
        self.assertIs(cue.kind, CueKind.CUE)
        self.assertIsNone(cue.loop_end_s)

    def test_the_stored_loop_can_be_played_again(self) -> None:
        self.rig.seek(10.0)
        self.rig.beat_loop(4.0)
        self.rig.play()
        self.rig.send(CommandType.PAD, index=0, pressed=True)
        self.rig.send(CommandType.PAD, index=0, pressed=False)

        # Loop verlassen und weit wegspringen.
        self.rig.send(CommandType.RELOOP_EXIT)
        self.rig.seek(60.0)
        self.assertFalse(self.rig.loop.active)

        # Dasselbe Pad holt den Loop zurueck.
        self.rig.send(CommandType.PAD, index=0, pressed=True)
        self.assertTrue(self.rig.loop.active)
        self.assertAlmostEqual(self.rig.loop.in_s, 10.0, places=9)
        self.assertAlmostEqual(self.rig.loop.out_s, 12.0, places=9)

    def test_the_stored_points_are_not_quantised_twice(self) -> None:
        """Der Hotcue-Loop ist derselbe wie der laufende - Punkt fuer Punkt."""
        self.rig.send(CommandType.QUANTIZE_TOGGLE)
        self.rig.send(CommandType.QUANTIZE_BEATS, beats=0.125)
        self.rig.seek(10.3)
        self.rig.beat_loop(4.0)
        loop = self.rig.loop
        self.rig.play()

        self.rig.send(CommandType.PAD, index=0, pressed=True)
        cue = self.rig.state.track.hot_cue(0)
        self.assertAlmostEqual(cue.position_s, loop.in_s, places=9)
        self.assertAlmostEqual(cue.loop_end_s, loop.out_s, places=9)

    def test_an_occupied_pad_is_not_overwritten(self) -> None:
        track = make_track(
            hot_cues=(HotCue(index=0, position_s=5.0, color=""),)
        )
        rig = Rig(track)
        rig.seek(10.0)
        rig.beat_loop(4.0)
        rig.play()
        rig.send(CommandType.PAD, index=0, pressed=True)
        cue = rig.state.track.hot_cue(0)
        self.assertIs(cue.kind, CueKind.CUE)
        self.assertAlmostEqual(cue.position_s, 5.0)


# --------------------------------------------------------------------------
# 7. Jogwheel invertiert bei Reverse (Handbuch S. 47)
# --------------------------------------------------------------------------


class ReverseJogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig()
        self.rig.seek(30.0)

    def _reverse(self) -> None:
        self.rig.send(CommandType.DIRECTION, position=Direction.REV)

    def test_scratch_runs_the_other_way(self) -> None:
        self.rig.play()
        self.rig.send(CommandType.JOG_TOUCH, pressed=True)
        self.rig.turn(1.0)
        forward = self.rig.position - 30.0
        self.assertAlmostEqual(forward, BEAT_S, places=6)

        # Dasselbe noch einmal auf einem eigenen Deck, diesmal rueckwaerts.
        rig = Rig()
        rig.seek(30.0)
        rig.play()
        rig.send(CommandType.DIRECTION, position=Direction.REV)
        rig.send(CommandType.JOG_TOUCH, pressed=True)
        rig.turn(1.0)
        self.assertAlmostEqual(rig.position - 30.0, -BEAT_S, places=6)

    def test_pitch_bend_runs_the_other_way(self) -> None:
        self.rig.play()
        self._reverse()
        self.rig.turn(1.0)
        self.assertAlmostEqual(
            self.rig.position, 30.0 - JOG_BEND_SECONDS_PER_REV, places=6
        )

    def test_frame_search_runs_the_other_way(self) -> None:
        self._reverse()
        self.rig.send(CommandType.JOG_TOUCH, pressed=True)
        self.rig.turn(1.0)
        self.assertAlmostEqual(self.rig.position, 30.0 - BEAT_S, places=6)

    def test_back_on_forward_the_jog_is_normal_again(self) -> None:
        self.rig.play()
        self._reverse()
        self.rig.send(CommandType.DIRECTION, position=Direction.FWD)
        self.rig.send(CommandType.JOG_TOUCH, pressed=True)
        self.rig.turn(1.0)
        self.assertAlmostEqual(self.rig.position, 30.0 + BEAT_S, places=6)

    def test_the_loop_adjust_keeps_its_direction(self) -> None:
        """Die Feineinstellung bearbeitet, sie spielt nicht ab."""
        self.rig.seek(10.0)
        self.rig.beat_loop(4.0)
        self.rig.play()
        self.rig.send(CommandType.LOOP_OUT)
        before = self.rig.loop.out_s

        self._reverse()
        self.rig.turn(1.0)
        self.assertAlmostEqual(
            self.rig.loop.out_s, before + BEAT_S, places=6
        )

    def test_slip_reverse_still_runs_its_timeline_forward(self) -> None:
        self.rig.play()
        self.rig.send(CommandType.SLIP_TOGGLE)
        self._reverse()
        self.rig.run(3.0)
        self.assertAlmostEqual(self.rig.state.slip_position_s, 33.0, places=3)
        self.assertAlmostEqual(self.rig.position, 27.0, places=3)


# --------------------------------------------------------------------------
# Regression: die sieben Punkte tasten den Loop nicht an
# --------------------------------------------------------------------------


class LoopStaysIntactTests(unittest.TestCase):
    def test_none_of_the_new_paths_ends_a_loop_by_itself(self) -> None:
        rig = Rig()
        rig.seek(10.0)
        rig.beat_loop(4.0)
        rig.play()

        steps = (
            ("Beat Jump vor", lambda: rig.send(
                CommandType.BEAT_JUMP, direction=+1, beats=4.0)),
            ("Beat Jump zurueck", lambda: rig.send(
                CommandType.BEAT_JUMP, direction=-1, beats=4.0)),
            ("Beat-Wert setzen", lambda: rig.send(
                CommandType.BEAT_JUMP_BEATS, beats=8.0)),
            ("Beat-Wert weiter", lambda: rig.send(
                CommandType.BEAT_JUMP_BEATS)),
            ("OUT-Adjust ein", lambda: rig.send(CommandType.LOOP_OUT)),
            ("SEARCH im Adjust", lambda: (
                rig.send(CommandType.SEARCH, direction=+1, pressed=True),
                rig.run(0.2),
                rig.send(CommandType.SEARCH, direction=+1, pressed=False),
            )),
            ("OUT-Adjust aus", lambda: rig.send(CommandType.LOOP_OUT)),
            ("Hotcue-Loop speichern", lambda: rig.send(
                CommandType.PAD, index=1, pressed=True)),
            ("Pad los", lambda: rig.send(
                CommandType.PAD, index=1, pressed=False)),
            ("Reverse", lambda: rig.send(
                CommandType.DIRECTION, position=Direction.REV)),
            ("Jog im Reverse", lambda: rig.turn(0.5)),
            ("Vorwaerts", lambda: rig.send(
                CommandType.DIRECTION, position=Direction.FWD)),
        )
        for label, action in steps:
            with self.subTest(label):
                action()
                rig.run(0.1)
                loop = rig.loop
                self.assertTrue(
                    loop.active,
                    f"{label} hat den Loop beendet, Grund="
                    f"{loop.exit_reason.value if loop.exit_reason else '-'}",
                )


# --------------------------------------------------------------------------
# TRACK SEARCH ueber die ganze Kette: Taste -> Deck -> Backend -> Anwendung
# --------------------------------------------------------------------------


class TrackSearchApplicationTests(unittest.TestCase):
    """Der Trackwechsel selbst - er braucht die Bibliothek der Anwendung.

    Das Deck kennt keine Trackliste. Erst hier zeigt sich, ob der ganze Weg
    traegt: Kommando -> ``Deck.track_requests`` -> Backend -> ``track_search``
    -> Laden.
    """

    def setUp(self) -> None:
        import tempfile

        from virtual_cdj.app import CdjApplication

        # Eigener Analyse-Cache: diese Tests sollen den gemeinsamen Cache
        # des Projekts weder fuellen noch von seinem Inhalt abhaengen.
        self._cache = tempfile.TemporaryDirectory(prefix="cdj_ops_cache_")
        self.app = CdjApplication(
            [1],
            start_audio=False,
            demo=True,
            cache_directory=self._cache.name,
            operating_mode=OperatingMode.CDJ,
        )
        self.ids = self.app.demo_track_ids()
        self.assertGreaterEqual(len(self.ids), 2, "Demo-Quelle zu klein")

    def tearDown(self) -> None:
        self.app.close()
        self._cache.cleanup()

    def _load(self, index: int) -> None:
        result = self.app.load_track_now(1, f"demo://{self.ids[index]}")
        self.assertTrue(result.ok)

    def _await_track(self, track_id: str) -> None:
        """Der Ladeauftrag laeuft im Worker - auf ihn warten."""
        import time

        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            self.app.poll_worker()
            loaded = self.app.decks[1].state.track
            if loaded is not None and loaded.track_id == track_id:
                return
            time.sleep(0.02)
        self.fail(f"Track {track_id} wurde nicht geladen")

    def _press(self, direction: int) -> None:
        self.app.providers[1].send(
            command(
                CommandType.TRACK_SEARCH, 1, "TEST", direction=direction
            )
        )

    def test_next_loads_the_following_track(self) -> None:
        self._load(0)
        self._press(+1)
        self._await_track(self.ids[1])

    def test_previous_first_restarts_then_changes_track(self) -> None:
        self._load(1)
        self.app.providers[1].send(
            command(CommandType.SEEK, 1, "TEST", position_s=20.0)
        )
        self.assertGreater(self.app.decks[1].state.position_s, 0.0)

        self._press(-1)                      # erster Druck: an den Anfang
        self.assertAlmostEqual(
            self.app.decks[1].state.position_s, 0.0, places=6
        )
        self.assertEqual(
            self.app.decks[1].state.track.track_id, self.ids[1]
        )

        self._press(-1)                      # zweiter Druck: Trackwechsel
        self._await_track(self.ids[0])

    def test_at_the_end_of_the_list_nothing_happens(self) -> None:
        self._load(len(self.ids) - 1)
        self._press(+1)
        self.app.poll_worker()
        self.assertEqual(
            self.app.decks[1].state.track.track_id, self.ids[-1]
        )

    def test_without_a_track_nothing_happens(self) -> None:
        self.app.track_search(1, +1)
        self.assertIsNone(self.app.decks[1].state.track)


if __name__ == "__main__":
    unittest.main()
