"""Tests der Loop-Engine (``virtual_cdj/deck/loop.py``).

Reine Rechnung, ohne Deck, ohne Audio, ohne Fenster. Ein Test je Regel der
Bedienlogik; die Verdrahtung ins Deck prueft ``test_deck.py``.

Beatgrid der Tests: 120 BPM ab 0.0 - ein Beat ist damit 0.5 s lang.
"""

from __future__ import annotations

import unittest

from virtual_cdj.deck.loop import (
    CALL_LEFT_BEATS,
    CALL_RIGHT_BEATS,
    MAX_LOOP_BEATS,
    MIN_LOOP_BEATS,
    LoopEngine,
)
from virtual_cdj.deck.state import (
    BEAT_LOOP_LENGTHS,
    BeatGrid,
    LoopAdjust,
    LoopExitReason,
    LoopState,
)

BEAT_S = 0.5


def engine(*, quantize: bool = False, bpm: float = 120.0) -> LoopEngine:
    """Loop-Engine mit Testraster. ``quantize`` wie der Schalter am Deck."""
    return LoopEngine(
        grid=BeatGrid(first_beat_s=0.0, bpm=bpm), quantize=quantize
    )


def loop_of(beats: float, in_s: float = 1.0) -> LoopState:
    """Aktiver Loop bekannter Laenge, so wie ihn die Engine erzeugt."""
    return engine().beat_loop(LoopState(), beats, in_s)


# --------------------------------------------------------------------------
# Abschnitt 4: Quantisierung
# --------------------------------------------------------------------------


class NearestBeatgridTests(unittest.TestCase):
    def test_closer_to_the_previous_beat(self) -> None:
        self.assertAlmostEqual(engine().nearest_beatgrid(1.15), 1.0)

    def test_closer_to_the_next_beat(self) -> None:
        self.assertAlmostEqual(engine().nearest_beatgrid(1.35), 1.5)

    def test_exact_half_takes_the_later_beat(self) -> None:
        self.assertAlmostEqual(engine().nearest_beatgrid(1.25), 1.5)

    def test_without_grid_the_position_stays(self) -> None:
        self.assertAlmostEqual(LoopEngine().nearest_beatgrid(1.15), 1.15)

    def test_quantize_on_snaps_a_new_loop_point(self) -> None:
        self.assertAlmostEqual(
            engine(quantize=True).position_for(1.15), 1.0
        )

    def test_quantize_off_uses_the_exact_position(self) -> None:
        self.assertAlmostEqual(
            engine(quantize=False).position_for(1.15), 1.15
        )

    def test_quantize_on_without_grid_cannot_snap(self) -> None:
        self.assertAlmostEqual(
            LoopEngine(quantize=True).position_for(1.15), 1.15
        )

    def test_fractional_beats_are_not_rounded_to_whole_beats(self) -> None:
        # BeatGrid.seconds_for_beats rundet Bruchteile auf ganze Beats;
        # die Loop-Engine darf das nicht tun, sonst waere 1/4 Beat = 0.
        self.assertAlmostEqual(engine().seconds_for_beats(0.25, 0.0), 0.125)
        self.assertAlmostEqual(engine().seconds_for_beats(1.5, 0.0), 0.75)


# --------------------------------------------------------------------------
# Abschnitt 5 und 6: LOOP IN / LOOP OUT
# --------------------------------------------------------------------------


class ManualLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = engine()

    def test_loop_in_takes_the_exact_position_without_quantize(self) -> None:
        loop = self.engine.loop_in_pressed(LoopState(), 1.15)
        self.assertAlmostEqual(loop.in_s, 1.15)
        self.assertFalse(loop.active)
        self.assertIsNone(loop.out_s)

    def test_loop_in_snaps_to_the_beat_with_quantize(self) -> None:
        loop = engine(quantize=True).loop_in_pressed(LoopState(), 1.15)
        self.assertAlmostEqual(loop.in_s, 1.0)

    def test_loop_out_takes_the_exact_position_without_quantize(self) -> None:
        loop = self.engine.loop_in_pressed(LoopState(), 1.15)
        loop = self.engine.loop_out_pressed(loop, 3.15)
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.in_s, 1.15)
        self.assertAlmostEqual(loop.out_s, 3.15)

    def test_loop_out_snaps_to_the_beat_with_quantize(self) -> None:
        quantized = engine(quantize=True)
        loop = quantized.loop_in_pressed(LoopState(), 1.15)
        loop = quantized.loop_out_pressed(loop, 3.15)
        self.assertAlmostEqual(loop.in_s, 1.0)
        self.assertAlmostEqual(loop.out_s, 3.0)
        self.assertAlmostEqual(loop.beats, 4.0)

    def test_loop_out_starts_the_loop(self) -> None:
        loop = self.engine.loop_in_pressed(LoopState(), 1.0)
        loop = self.engine.loop_out_pressed(loop, 3.0)
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.in_s, 1.0)
        self.assertAlmostEqual(loop.out_s, 3.0)
        self.assertAlmostEqual(loop.beats, 4.0)

    def test_loop_out_without_loop_in_does_nothing(self) -> None:
        loop = self.engine.loop_out_pressed(LoopState(), 3.0)
        self.assertFalse(loop.active)
        self.assertIsNone(loop.out_s)

    def test_loop_out_before_loop_in_is_refused(self) -> None:
        loop = self.engine.loop_in_pressed(LoopState(), 3.0)
        after = self.engine.loop_out_pressed(loop, 1.0)
        self.assertEqual(after, loop)
        self.assertFalse(after.active)

    def test_loop_in_during_a_loop_starts_the_in_adjust(self) -> None:
        loop = self.engine.loop_in_pressed(loop_of(4.0), 9.0)
        self.assertIs(loop.adjust, LoopAdjust.IN)
        # Die Punkte selbst bleiben stehen.
        self.assertAlmostEqual(loop.in_s, 1.0)

    def test_loop_out_during_a_loop_starts_the_out_adjust(self) -> None:
        loop = self.engine.loop_out_pressed(loop_of(4.0), 9.0)
        self.assertIs(loop.adjust, LoopAdjust.OUT)

    def test_pressing_again_ends_the_adjust(self) -> None:
        loop = self.engine.loop_in_pressed(loop_of(4.0), 9.0)
        loop = self.engine.loop_in_pressed(loop, 9.0)
        self.assertIs(loop.adjust, LoopAdjust.NONE)

    def test_the_other_button_switches_the_adjusted_point(self) -> None:
        loop = self.engine.loop_in_pressed(loop_of(4.0), 9.0)
        loop = self.engine.loop_out_pressed(loop, 9.0)
        self.assertIs(loop.adjust, LoopAdjust.OUT)


# --------------------------------------------------------------------------
# Abschnitt 7: Jogwheel im Adjust-Modus
# --------------------------------------------------------------------------


class JogAdjustTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = engine()
        self.loop = self.engine.loop_in_pressed(loop_of(4.0), 0.0)  # IN

    def test_one_revolution_moves_one_beat(self) -> None:
        loop = self.engine.adjust_with_jog(self.loop, 1.0)
        self.assertAlmostEqual(loop.in_s, 1.0 + BEAT_S)

    def test_half_a_revolution_moves_half_a_beat(self) -> None:
        loop = self.engine.adjust_with_jog(self.loop, -0.5)
        self.assertAlmostEqual(loop.in_s, 1.0 - BEAT_S / 2)

    def test_a_quarter_turn_is_not_quantized(self) -> None:
        loop = self.engine.adjust_with_jog(self.loop, 0.25)
        self.assertAlmostEqual(loop.in_s, 1.0 + BEAT_S / 4)

    def test_out_adjust_moves_the_end(self) -> None:
        loop = self.engine.loop_out_pressed(loop_of(4.0), 0.0)
        moved = self.engine.adjust_with_jog(loop, 2.0)
        self.assertAlmostEqual(moved.out_s, 3.0 + 2 * BEAT_S)
        self.assertAlmostEqual(moved.in_s, 1.0)

    def test_without_adjust_mode_nothing_moves(self) -> None:
        loop = loop_of(4.0)
        self.assertEqual(self.engine.adjust_with_jog(loop, 1.0), loop)

    def test_the_end_can_never_pass_the_start(self) -> None:
        """Weit ueber die Grenze hinaus: der Punkt bleibt **an** der Grenze.

        Frueher wurde ein solcher Schritt komplett verworfen. Weil das
        Jogwheel im Adjust-Modus ohnehin nicht mehr auf die Wiedergabe
        wirkt, bewegte sich dann gar nichts mehr - das Geraet wirkte
        aufgehaengt. Jetzt wird begrenzt statt abgewiesen.
        """
        loop = self.engine.loop_out_pressed(loop_of(4.0), 0.0)
        moved = self.engine.adjust_with_jog(loop, -8.0)
        self.assertGreater(moved.out_s, moved.in_s)
        self.assertAlmostEqual(moved.beats, MIN_LOOP_BEATS)
        self.assertAlmostEqual(moved.in_s, loop.in_s)

    def test_the_start_can_never_pass_the_end(self) -> None:
        moved = self.engine.adjust_with_jog(self.loop, 8.0)
        self.assertLess(moved.in_s, moved.out_s)
        self.assertAlmostEqual(moved.beats, MIN_LOOP_BEATS)
        self.assertAlmostEqual(moved.out_s, self.loop.out_s)

    def test_a_step_beyond_the_longest_loop_stops_at_the_limit(self) -> None:
        loop = self.engine.loop_out_pressed(loop_of(MAX_LOOP_BEATS), 0.0)
        moved = self.engine.adjust_with_jog(loop, 1.0)
        self.assertAlmostEqual(moved.beats, MAX_LOOP_BEATS)
        self.assertAlmostEqual(moved.out_s, loop.out_s)

    def test_at_the_limit_a_further_step_changes_nothing(self) -> None:
        """An der Grenze angekommen, bleibt es dabei - ohne Sprung."""
        loop = self.engine.loop_out_pressed(loop_of(4.0), 0.0)
        at_limit = self.engine.adjust_with_jog(loop, -8.0)
        self.assertEqual(self.engine.adjust_with_jog(at_limit, -8.0), at_limit)

    def test_a_step_back_from_the_limit_works_again(self) -> None:
        """Wichtig: die Begrenzung darf nicht in einer Sackgasse enden."""
        loop = self.engine.loop_out_pressed(loop_of(4.0), 0.0)
        at_limit = self.engine.adjust_with_jog(loop, -8.0)
        back = self.engine.adjust_with_jog(at_limit, +1.0)
        self.assertGreater(back.out_s, at_limit.out_s)

    def test_an_overlong_manual_loop_is_not_squashed_by_the_limit(self) -> None:
        """Von Hand gezogene Loops duerfen laenger als 64 Beats sein.

        LOOP IN/OUT kennen die Grenzen bewusst nicht. Beim Verschieben darf
        ein solcher Loop deshalb nicht schlagartig auf 64 Beats
        zusammenschnappen - die Grenze verhindert nur, dass er **weiter**
        waechst.
        """
        engine_ = engine()
        loop = engine_.loop_in_pressed(LoopState(), 0.0)
        loop = engine_.loop_out_pressed(loop, 100 * BEAT_S)   # 100 Beats
        self.assertAlmostEqual(loop.length_s, 100 * BEAT_S)

        loop = engine_.loop_out_pressed(loop, 0.0)            # OUT-Adjust
        shorter = engine_.adjust_with_jog(loop, -1.0)
        self.assertAlmostEqual(shorter.length_s, 99 * BEAT_S)
        # Und weiter wachsen darf er nicht.
        longer = engine_.adjust_with_jog(loop, +5.0)
        self.assertAlmostEqual(longer.length_s, 100 * BEAT_S)

    def test_the_saved_loop_survives_every_jog_step(self) -> None:
        # Abschnitt 19: nicht nach jedem Jog-Schritt ueberschreiben.
        loop = self.engine.adjust_with_jog(self.loop, 1.0)
        loop = self.engine.adjust_with_jog(loop, 1.0)
        self.assertAlmostEqual(loop.last_in_s, 1.0)
        self.assertAlmostEqual(loop.last_out_s, 3.0)

    def test_ending_the_adjust_saves_the_new_points(self) -> None:
        loop = self.engine.adjust_with_jog(self.loop, 1.0)
        loop = self.engine.loop_in_pressed(loop, 9.0)
        self.assertIs(loop.adjust, LoopAdjust.NONE)
        self.assertAlmostEqual(loop.last_in_s, 1.0 + BEAT_S)

    def test_an_uneven_length_reports_no_beat_count(self) -> None:
        loop = self.engine.adjust_with_jog(self.loop, 0.3)
        self.assertIsNone(loop.beats)
        self.assertEqual(loop.label(), "")

    def test_a_whole_beat_step_keeps_a_beat_count(self) -> None:
        loop = self.engine.loop_out_pressed(loop_of(4.0), 0.0)
        moved = self.engine.adjust_with_jog(loop, 4.0)
        self.assertAlmostEqual(moved.beats, 8.0)
        self.assertEqual(moved.label(), "8")


# --------------------------------------------------------------------------
# Abschnitt 8 und 9: CALL < und CALL >
# --------------------------------------------------------------------------


class CallButtonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = engine()

    def test_call_left_without_loop_makes_four_beats(self) -> None:
        loop = self.engine.call_left_pressed(LoopState(), 1.15)
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.in_s, 1.15)  # ohne QUANTIZE exakt
        self.assertAlmostEqual(loop.beats, CALL_LEFT_BEATS)
        self.assertAlmostEqual(loop.length_s, 4 * BEAT_S)

    def test_call_right_without_loop_makes_eight_beats(self) -> None:
        loop = self.engine.call_right_pressed(LoopState(), 1.15)
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.beats, CALL_RIGHT_BEATS)

    def test_call_snaps_the_start_with_quantize(self) -> None:
        loop = engine(quantize=True).call_left_pressed(LoopState(), 1.15)
        self.assertAlmostEqual(loop.in_s, 1.0)
        self.assertAlmostEqual(loop.length_s, 4 * BEAT_S)

    def test_halving_walks_down_the_lengths(self) -> None:
        loop = loop_of(8.0)
        seen = []
        for _ in range(5):
            loop = self.engine.call_left_pressed(loop, 0.0)
            seen.append(loop.beats)
        self.assertEqual(seen, [4.0, 2.0, 1.0, 0.5, 0.25])
        # Der Anfang bleibt dabei stehen.
        self.assertAlmostEqual(loop.in_s, 1.0)

    def test_doubling_walks_up_the_lengths(self) -> None:
        loop = loop_of(1.0)
        seen = []
        for _ in range(5):
            loop = self.engine.call_right_pressed(loop, 0.0)
            seen.append(loop.beats)
        self.assertEqual(seen, [2.0, 4.0, 8.0, 16.0, 32.0])

    def test_the_shortest_loop_cannot_be_halved(self) -> None:
        loop = loop_of(MIN_LOOP_BEATS)
        self.assertEqual(self.engine.call_left_pressed(loop, 0.0), loop)

    def test_the_longest_loop_cannot_be_doubled(self) -> None:
        loop = loop_of(MAX_LOOP_BEATS)
        self.assertEqual(self.engine.call_right_pressed(loop, 0.0), loop)

    def test_a_changed_length_is_saved(self) -> None:
        loop = self.engine.call_left_pressed(loop_of(8.0), 0.0)
        self.assertAlmostEqual(loop.last_out_s, loop.out_s)


# --------------------------------------------------------------------------
# Abschnitt 11 bis 14: Beatloop-Pads
# --------------------------------------------------------------------------


class BeatLoopPadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = engine()

    def test_pad_lengths_are_a_quarter_up_to_thirty_two(self) -> None:
        self.assertEqual(
            BEAT_LOOP_LENGTHS, (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
        )

    def test_a_pad_starts_a_loop_of_its_length(self) -> None:
        loop = self.engine.beat_loop_pad_pressed(LoopState(), 4, 1.15)
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.in_s, 1.15)  # ohne QUANTIZE exakt
        self.assertAlmostEqual(loop.length_s, 4 * BEAT_S)
        self.assertEqual(loop.pad_index, 4)

    def test_a_pad_snaps_the_start_with_quantize(self) -> None:
        loop = engine(quantize=True).beat_loop_pad_pressed(
            LoopState(), 4, 1.15
        )
        self.assertAlmostEqual(loop.in_s, 1.0)
        self.assertAlmostEqual(loop.length_s, 4 * BEAT_S)

    def test_another_pad_only_changes_the_length(self) -> None:
        loop = self.engine.beat_loop_pad_pressed(LoopState(), 4, 1.0)
        loop = self.engine.beat_loop_pad_pressed(loop, 5, 9.0)
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.in_s, 1.0)  # Anfang bleibt
        self.assertAlmostEqual(loop.length_s, 8 * BEAT_S)
        self.assertEqual(loop.pad_index, 5)

    def test_the_same_pad_again_leaves_the_loop(self) -> None:
        loop = self.engine.beat_loop_pad_pressed(LoopState(), 4, 1.0)
        loop = self.engine.beat_loop_pad_pressed(loop, 4, 9.0)
        self.assertFalse(loop.active)
        self.assertTrue(loop.is_set)
        self.assertTrue(loop.has_last)

    def test_the_same_pad_a_third_time_starts_a_new_loop(self) -> None:
        loop = self.engine.beat_loop_pad_pressed(LoopState(), 4, 1.0)
        loop = self.engine.beat_loop_pad_pressed(loop, 4, 9.0)
        loop = self.engine.beat_loop_pad_pressed(loop, 4, 9.0)
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.in_s, 9.0)

    def test_a_pad_remembers_that_it_set_the_loop(self) -> None:
        """``Last_BeatLoop_Pad`` wird gefuehrt, nicht aus der Laenge geraten."""
        loop = self.engine.beat_loop_pad_pressed(LoopState(), 4, 1.0)
        self.assertEqual(loop.pad, 4)
        loop = self.engine.beat_loop_pad_pressed(loop, 5, 9.0)
        self.assertEqual(loop.pad, 5)

    def test_a_pad_does_not_leave_a_loop_it_did_not_set(self) -> None:
        """Der Fehler, der den Loop verschwinden liess.

        CALL < erzeugt einen 4-Beat-Loop. Pad E ist ebenfalls 4 Beats lang -
        frueher wurde das Pad daran erkannt, dass die **Laenge** passt, und
        ein Druck auf Pad E verliess den Loop, statt ihn zu setzen. Der
        Loop war auf einen Tastendruck weg.
        """
        loop = self.engine.call_left_pressed(LoopState(), 1.0)
        self.assertAlmostEqual(loop.beats, 4.0)
        self.assertIsNone(loop.pad)

        loop = self.engine.beat_loop_pad_pressed(loop, 4, 9.0)
        self.assertTrue(loop.active, "Pad E muss den Loop setzen")
        self.assertEqual(loop.pad, 4)
        # Und jetzt, wo er wirklich von Pad E kommt, verlaesst Pad E ihn.
        loop = self.engine.beat_loop_pad_pressed(loop, 4, 9.0)
        self.assertFalse(loop.active)

    def test_a_manual_loop_is_not_owned_by_any_pad(self) -> None:
        loop = self.engine.loop_in_pressed(LoopState(), 1.0)
        loop = self.engine.loop_out_pressed(loop, 3.0)
        self.assertAlmostEqual(loop.beats, 4.0)
        self.assertIsNone(loop.pad)
        loop = self.engine.beat_loop_pad_pressed(loop, 4, 9.0)
        self.assertTrue(loop.active)

    def test_scaling_gives_up_the_pad(self) -> None:
        """Nach CALL passt die Laenge nicht mehr zum Pad."""
        loop = self.engine.beat_loop_pad_pressed(LoopState(), 5, 1.0)
        loop = self.engine.call_left_pressed(loop, 1.0)   # 8 -> 4 Beats
        self.assertIsNone(loop.pad)
        loop = self.engine.beat_loop_pad_pressed(loop, 4, 1.0)
        self.assertTrue(loop.active)

    def test_leaving_the_loop_clears_the_pad(self) -> None:
        loop = self.engine.beat_loop_pad_pressed(LoopState(), 4, 1.0)
        loop = self.engine.exit_loop(loop, LoopExitReason.RELOOP_EXIT)
        self.assertIsNone(loop.pad)

    def test_leaving_a_loop_always_records_a_reason(self) -> None:
        """Es gibt keinen anonymen Weg aus einem Loop."""
        loop = self.engine.beat_loop_pad_pressed(LoopState(), 4, 1.0)
        with self.assertRaises(TypeError):
            self.engine.exit_loop(loop)  # type: ignore[call-arg]
        left = self.engine.exit_loop(loop, LoopExitReason.RELOOP_EXIT)
        self.assertIs(left.exit_reason, LoopExitReason.RELOOP_EXIT)

    def test_an_unknown_pad_changes_nothing(self) -> None:
        loop = LoopState()
        self.assertEqual(self.engine.beat_loop_pad_pressed(loop, 9, 1.0), loop)

    def test_without_a_beatgrid_no_beatloop_is_invented(self) -> None:
        loop = LoopState()
        self.assertEqual(
            LoopEngine().beat_loop_pad_pressed(loop, 4, 1.0), loop
        )


# --------------------------------------------------------------------------
# Abschnitt 16 bis 19: Exit, Reloop, letzten Loop speichern
# --------------------------------------------------------------------------


class ExitReloopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = engine()
        self.loop = loop_of(4.0)  # 1.0 bis 3.0

    def test_exit_keeps_the_points_and_does_not_jump(self) -> None:
        loop, jump = self.engine.exit_reloop_pressed(self.loop, 2.0)
        self.assertFalse(loop.active)
        self.assertIsNone(jump)
        self.assertAlmostEqual(loop.in_s, 1.0)
        self.assertIs(loop.adjust, LoopAdjust.NONE)

    def test_exit_ends_a_running_adjust(self) -> None:
        loop = self.engine.loop_in_pressed(self.loop, 2.0)
        loop, _ = self.engine.exit_reloop_pressed(loop, 2.0)
        self.assertIs(loop.adjust, LoopAdjust.NONE)

    def test_reloop_inside_the_old_loop_keeps_the_position(self) -> None:
        loop, _ = self.engine.exit_reloop_pressed(self.loop, 2.0)
        loop, jump = self.engine.exit_reloop_pressed(loop, 2.0)
        self.assertTrue(loop.active)
        self.assertIsNone(jump)

    def test_reloop_outside_jumps_to_the_start(self) -> None:
        loop, _ = self.engine.exit_reloop_pressed(self.loop, 2.0)
        loop, jump = self.engine.exit_reloop_pressed(loop, 30.0)
        self.assertTrue(loop.active)
        self.assertAlmostEqual(jump, 1.0)

    def test_the_loop_end_counts_as_outside(self) -> None:
        loop, _ = self.engine.exit_reloop_pressed(self.loop, 2.0)
        _, jump = self.engine.exit_reloop_pressed(loop, 3.0)
        self.assertAlmostEqual(jump, 1.0)

    def test_reloop_uses_the_saved_loop_not_a_new_loop_in(self) -> None:
        loop, _ = self.engine.exit_reloop_pressed(self.loop, 2.0)
        # Ein neuer Loop-Anfang wurde gesetzt, aber nie abgeschlossen.
        loop = self.engine.loop_in_pressed(loop, 20.0)
        loop, jump = self.engine.exit_reloop_pressed(loop, 30.0)
        self.assertAlmostEqual(loop.in_s, 1.0)
        self.assertAlmostEqual(loop.out_s, 3.0)
        self.assertAlmostEqual(jump, 1.0)

    def test_without_a_saved_loop_nothing_happens(self) -> None:
        loop = LoopState()
        self.assertEqual(
            self.engine.exit_reloop_pressed(loop, 5.0), (loop, None)
        )


# --------------------------------------------------------------------------
# Abschnitt 3: Loop-Grenze bei der Wiedergabe
# --------------------------------------------------------------------------


class BoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = engine()
        self.loop = loop_of(4.0)  # 1.0 bis 3.0

    def test_inside_the_loop_nothing_changes(self) -> None:
        self.assertAlmostEqual(
            self.engine.check_boundary(self.loop, 2.0), 2.0
        )

    def test_the_end_wraps_to_the_start_with_the_overshoot(self) -> None:
        self.assertAlmostEqual(
            self.engine.check_boundary(self.loop, 3.25), 1.25
        )

    def test_a_long_overshoot_stays_inside(self) -> None:
        position = self.engine.check_boundary(self.loop, 9.5)
        self.assertGreaterEqual(position, 1.0)
        self.assertLess(position, 3.0)

    def test_before_the_start_wraps_to_the_end_with_the_overshoot(
        self,
    ) -> None:
        """Rueckwaerts ist der Loop derselbe Ring wie vorwaerts.

        Frueher wurde hier auf den Loop-Anfang gezogen. Dann blieb
        Rueckwaertslauf am Loop-Anfang haengen, und ein Backspin trug die
        Wiedergabe aus dem Loop heraus.
        """
        # Loop 1.0 bis 3.0: eine halbe Sekunde vor dem Anfang ist eine
        # halbe Sekunde vor dem Ende.
        self.assertAlmostEqual(
            self.engine.check_boundary(self.loop, 0.5), 2.5
        )

    def test_a_long_backward_overshoot_stays_inside(self) -> None:
        position = self.engine.check_boundary(self.loop, -7.25)
        self.assertGreaterEqual(position, 1.0)
        self.assertLess(position, 3.0)

    def test_exactly_one_length_back_lands_on_the_start(self) -> None:
        self.assertAlmostEqual(
            self.engine.check_boundary(self.loop, -1.0), 1.0
        )

    def test_an_inactive_loop_does_not_hold_anything(self) -> None:
        loop, _ = self.engine.exit_reloop_pressed(self.loop, 2.0)
        self.assertAlmostEqual(self.engine.check_boundary(loop, 9.5), 9.5)


# --------------------------------------------------------------------------
# Abschnitt 20: getrennte Zustaende
# --------------------------------------------------------------------------


class LoopStateTests(unittest.TestCase):
    def test_a_fresh_loop_state_is_empty(self) -> None:
        loop = LoopState()
        self.assertFalse(loop.active)
        self.assertFalse(loop.is_set)
        self.assertFalse(loop.has_last)
        self.assertIs(loop.adjust, LoopAdjust.NONE)
        self.assertIsNone(loop.pad_index)
        self.assertEqual(loop.label(), "")

    def test_labels_of_the_pad_lengths(self) -> None:
        labels = [
            loop_of(beats).label() for beats in BEAT_LOOP_LENGTHS
        ]
        self.assertEqual(
            labels, ["1/4", "1/2", "1", "2", "4", "8", "16", "32"]
        )

    def test_a_length_between_two_pads_has_no_pad(self) -> None:
        loop = loop_of(3.0)
        self.assertIsNone(loop.pad_index)
        self.assertEqual(loop.label(), "3")


if __name__ == "__main__":
    unittest.main()
