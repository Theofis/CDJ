"""Tests der Jog-Engine: Quadratur, Touch-Sensor, Scan-Programm, Leser.

Gliederung wie die Vorgabe: erst die Sensorseite, dann die Schnittstelle
zum DJ-Programm, dann der Weg bis in die Deck-Engine.
"""

from __future__ import annotations

import unittest

from virtual_cdj.core import controls, ids
from virtual_cdj.core.input_layer import InputLayer
from virtual_cdj.deck.commands import CommandType, command
from virtual_cdj.deck.engine import Deck
from virtual_cdj.deck.mapping import InputMapper
from virtual_cdj.deck.state import BeatGrid, TrackInfo
from virtual_cdj.demo.jog import SimulatedJog
from virtual_cdj.jog import (
    EDGES_PER_SLOT,
    SLOTS_PER_REV,
    STEPS_PER_BEAT,
    STEPS_PER_REV,
    JogReader,
    JogScanner,
    JogState,
    QuadratureDecoder,
    TouchSensor,
    is_valid,
    state_of,
    step_for,
)
from virtual_cdj.sources.hardware import HardwareSource, ProtocolError

#: Die vier Zustaende in Vorwaertsrichtung, als (A, B).
FORWARD_CYCLE = ((True, False), (False, False), (False, True), (True, True))


def make_track(**overrides) -> TrackInfo:
    defaults = dict(
        track_id="t1",
        duration_s=300.0,
        original_bpm=120.0,
        beat_grid=BeatGrid(first_beat_s=0.0, bpm=120.0),
    )
    defaults.update(overrides)
    return TrackInfo(**defaults)


def turn(decoder: QuadratureDecoder, steps: int) -> None:
    """Den Decoder um ``steps`` Schritte drehen (Vorzeichen = Richtung)."""
    cycle = FORWARD_CYCLE if steps >= 0 else tuple(reversed(FORWARD_CYCLE))
    index = 0
    if decoder.state is not None:
        index = [state_of(a, b) for a, b in cycle].index(decoder.state)
    for step in range(abs(steps)):
        index = (index + 1) % len(cycle)
        decoder.update(*cycle[index])


# --------------------------------------------------------------------------
# Abschnitt 5 bis 10: Quadraturauswertung
# --------------------------------------------------------------------------


class QuadratureTableTests(unittest.TestCase):
    def test_resolution_is_four_edges_per_slot(self) -> None:
        self.assertEqual(SLOTS_PER_REV, 200)
        self.assertEqual(EDGES_PER_SLOT, 4)
        self.assertEqual(STEPS_PER_REV, 800)

    def test_one_revolution_is_one_beat(self) -> None:
        self.assertEqual(STEPS_PER_BEAT, STEPS_PER_REV)

    def test_state_packs_both_sensors(self) -> None:
        self.assertEqual(state_of(True, False), 0b10)
        self.assertEqual(state_of(False, False), 0b00)
        self.assertEqual(state_of(False, True), 0b01)
        self.assertEqual(state_of(True, True), 0b11)

    def test_forward_transitions(self) -> None:
        for previous, current in (
            (0b10, 0b00), (0b00, 0b01), (0b01, 0b11), (0b11, 0b10)
        ):
            self.assertEqual(step_for(previous, current), +1)

    def test_backward_transitions(self) -> None:
        for previous, current in (
            (0b10, 0b11), (0b11, 0b01), (0b01, 0b00), (0b00, 0b10)
        ):
            self.assertEqual(step_for(previous, current), -1)

    def test_standing_still_is_no_step(self) -> None:
        for state in (0b00, 0b01, 0b10, 0b11):
            self.assertEqual(step_for(state, state), 0)
            self.assertTrue(is_valid(state, state))

    def test_both_sensors_changing_at_once_is_invalid(self) -> None:
        for previous, current in ((0b00, 0b11), (0b11, 0b00),
                                  (0b01, 0b10), (0b10, 0b01)):
            self.assertEqual(step_for(previous, current), 0)
            self.assertFalse(is_valid(previous, current))


class QuadratureDecoderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.decoder = QuadratureDecoder()

    def test_the_first_sample_only_sets_the_state(self) -> None:
        self.assertEqual(self.decoder.update(True, False), 0)
        self.assertEqual(self.decoder.position, 0)
        self.assertEqual(self.decoder.errors, 0)

    def test_three_steps_forward_count_up(self) -> None:
        self.decoder.update(True, False)
        turn(self.decoder, 3)
        self.assertEqual(self.decoder.position, 3)
        self.assertEqual(self.decoder.steps, 3)

    def test_steps_backward_count_down(self) -> None:
        self.decoder.update(True, False)
        turn(self.decoder, -3)
        self.assertEqual(self.decoder.position, -3)

    def test_a_full_revolution_is_eight_hundred_steps(self) -> None:
        self.decoder.update(True, False)
        turn(self.decoder, STEPS_PER_REV)
        self.assertEqual(self.decoder.position, STEPS_PER_REV)
        self.assertEqual(self.decoder.errors, 0)

    def test_the_counter_starts_where_it_is_told(self) -> None:
        decoder = QuadratureDecoder(position=1000)
        decoder.update(True, False)
        turn(decoder, 3)
        self.assertEqual(decoder.position, 1003)

    def test_an_invalid_jump_moves_nothing_and_is_counted(self) -> None:
        self.decoder.update(False, False)
        self.assertEqual(self.decoder.update(True, True), 0)
        self.assertEqual(self.decoder.position, 0)
        self.assertEqual(self.decoder.errors, 1)

    def test_the_same_sample_twice_is_no_error(self) -> None:
        self.decoder.update(True, False)
        self.decoder.update(True, False)
        self.assertEqual(self.decoder.errors, 0)
        self.assertEqual(self.decoder.position, 0)

    def test_invert_swaps_the_direction(self) -> None:
        decoder = QuadratureDecoder(invert=True)
        decoder.update(True, False)
        turn(decoder, 4)
        self.assertEqual(decoder.position, -4)

    def test_reset_clears_counter_and_statistics(self) -> None:
        self.decoder.update(True, False)
        turn(self.decoder, 5)
        self.decoder.reset()
        self.assertEqual(self.decoder.position, 0)
        self.assertEqual(self.decoder.steps, 0)
        self.assertIsNone(self.decoder.state)


# --------------------------------------------------------------------------
# Abschnitt 3 und 4: Touch-Sensor
# --------------------------------------------------------------------------


class TouchSensorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sensor = TouchSensor(700, 650)

    def test_above_the_threshold_is_touched(self) -> None:
        self.assertTrue(self.sensor.update(750))

    def test_below_the_threshold_is_not_touched(self) -> None:
        self.assertFalse(self.sensor.update(500))

    def test_hysteresis_holds_the_state_between_the_thresholds(self) -> None:
        self.sensor.update(750)
        self.assertTrue(self.sensor.update(680))  # unter EIN, ueber AUS
        self.assertFalse(self.sensor.update(640))
        self.assertFalse(self.sensor.update(680))  # ueber AUS, unter EIN

    def test_without_hysteresis_one_threshold_decides(self) -> None:
        sensor = TouchSensor(700)
        self.assertFalse(sensor.has_hysteresis)
        self.assertTrue(sensor.update(701))
        self.assertFalse(sensor.update(699))

    def test_an_off_threshold_above_the_on_threshold_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            TouchSensor(650, 700)

    def test_statistics_follow_the_raw_values(self) -> None:
        for value in (500, 900, 700):
            self.sensor.update(value)
        stats = self.sensor.stats
        self.assertEqual(stats.samples, 3)
        self.assertAlmostEqual(stats.minimum, 500)
        self.assertAlmostEqual(stats.maximum, 900)
        self.assertAlmostEqual(stats.average, 700)
        self.assertAlmostEqual(stats.span, 400)
        self.assertAlmostEqual(stats.last, 700)

    def test_without_samples_there_are_no_statistics(self) -> None:
        stats = TouchSensor(700).stats
        self.assertFalse(stats.has_samples)
        self.assertIsNone(stats.minimum)
        self.assertIsNone(stats.average)

    def test_the_history_keeps_the_recent_values(self) -> None:
        sensor = TouchSensor(700, history=3)
        for value in (1, 2, 3, 4):
            sensor.update(value)
        self.assertEqual(sensor.history, (2.0, 3.0, 4.0))

    def test_a_suggestion_needs_a_real_span(self) -> None:
        self.assertIsNone(self.sensor.suggested_thresholds())
        for value in (500, 500, 500):
            self.sensor.update(value)
        self.assertIsNone(self.sensor.suggested_thresholds())

    def test_noise_alone_gives_no_suggestion(self) -> None:
        # Nur Ruhewerte mit etwas Rauschen: der Unterschied zwischen
        # beruehrt und nicht beruehrt wurde nie gemessen.
        for value in (868, 874, 880, 886, 892):
            self.sensor.update(value)
        self.assertIsNone(self.sensor.suggested_thresholds())

    def test_the_suggestion_lies_between_rest_and_touch(self) -> None:
        for value in (500, 520, 880, 900):
            self.sensor.update(value)
        suggestion = self.sensor.suggested_thresholds()
        self.assertIsNotNone(suggestion)
        on_value, off_value = suggestion
        self.assertGreater(on_value, off_value)
        self.assertGreater(on_value, 520)
        self.assertLess(off_value, 880)

    def test_reset_keeps_the_state_but_clears_the_numbers(self) -> None:
        self.sensor.update(900)
        self.sensor.reset_stats()
        self.assertTrue(self.sensor.touched)
        self.assertEqual(self.sensor.stats.samples, 0)
        self.assertEqual(self.sensor.history, ())


# --------------------------------------------------------------------------
# Abschnitt 2 und 16: Scan-Programm und Schnittstelle
# --------------------------------------------------------------------------


class JogScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scanner = JogScanner(touch_on=700, touch_off=650)

    def turn(self, steps: int) -> None:
        turn(self.scanner.decoder, steps)

    def test_the_interface_is_touch_and_position(self) -> None:
        state = self.scanner.state
        self.assertIsInstance(state, JogState)
        self.assertFalse(state.touch)
        self.assertEqual(state.position, 0)

    def test_scanning_counts_every_step(self) -> None:
        self.scanner.scan(True, False)
        self.turn(50)
        self.assertEqual(self.scanner.state.position, 50)

    def test_touch_comes_from_the_raw_value(self) -> None:
        self.scanner.scan(True, False, touch_raw=900)
        self.assertTrue(self.scanner.state.touch)
        self.scanner.scan_touch(400)
        self.assertFalse(self.scanner.state.touch)

    def test_a_scan_without_raw_value_keeps_the_touch_state(self) -> None:
        self.scanner.scan_touch(900)
        self.scanner.scan(True, False)
        self.assertTrue(self.scanner.state.touch)

    def test_a_device_that_counts_itself_can_set_the_position(self) -> None:
        self.scanner.set_position(15427)
        self.assertEqual(self.scanner.state.position, 15427)

    def test_a_device_can_report_the_touch_state_directly(self) -> None:
        self.scanner.set_touch(True)
        self.assertTrue(self.scanner.state.touch)

    def test_the_state_converts_into_beats(self) -> None:
        self.scanner.set_position(STEPS_PER_REV)
        state = self.scanner.state
        self.assertAlmostEqual(state.beats_since(0), 1.0)
        self.assertAlmostEqual(state.revolutions_since(0), 1.0)
        self.assertEqual(state.steps_since(400), 400)

    def test_errors_are_reported_not_hidden(self) -> None:
        self.scanner.scan(False, False)
        self.scanner.scan(True, True)
        self.assertEqual(self.scanner.state.errors, 1)


# --------------------------------------------------------------------------
# Abschnitt 11 bis 17: kein Warteschlangenbetrieb
# --------------------------------------------------------------------------


class JogReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scanner = JogScanner()
        self.reader = JogReader()

    def read(self):
        return self.reader.read(self.scanner.state)

    def test_the_first_read_only_takes_over_the_stand(self) -> None:
        self.scanner.set_position(1000)
        movement = self.read()
        self.assertEqual(movement.steps, 0)
        self.assertEqual(self.reader.last_position, 1000)

    def test_the_difference_is_the_movement(self) -> None:
        self.scanner.set_position(15427)
        self.read()
        self.scanner.set_position(15435)
        movement = self.read()
        self.assertEqual(movement.steps, 8)
        self.assertAlmostEqual(movement.beats, 8 / STEPS_PER_BEAT)

    def test_fifty_steps_arrive_in_one_go(self) -> None:
        # Beispiel aus Abschnitt 12: 1000 -> 1050 waehrend das DJ-Programm
        # beschaeftigt war. Es entsteht **eine** Bewegung, keine 50.
        self.scanner.set_position(1000)
        self.read()
        for position in range(1001, 1051):
            self.scanner.set_position(position)
        movement = self.read()
        self.assertEqual(movement.steps, 50)
        self.assertAlmostEqual(movement.beats, 50 / 800)

    def test_reading_twice_reports_no_movement_the_second_time(self) -> None:
        self.scanner.set_position(500)
        self.read()
        self.scanner.set_position(600)
        self.assertEqual(self.read().steps, 100)
        self.assertEqual(self.read().steps, 0)

    def test_a_full_revolution_is_one_beat(self) -> None:
        self.read()
        self.scanner.set_position(STEPS_PER_REV)
        self.assertAlmostEqual(self.read().beats, 1.0)

    def test_the_examples_of_the_specification(self) -> None:
        self.read()
        for steps, beats in (
            (1, 1 / 800), (100, 1 / 8), (200, 1 / 4), (400, 1 / 2), (800, 1.0)
        ):
            self.reader.reset()
            self.scanner.set_position(0)
            self.read()
            self.scanner.set_position(steps)
            self.assertAlmostEqual(self.read().beats, beats)

    def test_backwards_movement_is_negative(self) -> None:
        self.scanner.set_position(1000)
        self.read()
        self.scanner.set_position(920)
        self.assertAlmostEqual(self.read().beats, -0.1)

    def test_the_touch_change_is_reported_once(self) -> None:
        self.read()
        self.scanner.set_touch(True)
        movement = self.read()
        self.assertTrue(movement.touch)
        self.assertTrue(movement.touch_changed)
        self.assertFalse(self.read().touch_changed)

    def test_sync_skips_the_movement_of_a_pause(self) -> None:
        self.scanner.set_position(100)
        self.read()
        self.scanner.set_position(9000)
        self.reader.sync(self.scanner.state)
        self.assertEqual(self.read().steps, 0)


# --------------------------------------------------------------------------
# Sensorsignal -> Scan-Programm (ohne Hardware)
# --------------------------------------------------------------------------


class SimulatedJogTests(unittest.TestCase):
    """Das Demo-Signal muss echte Sensorpegel liefern, keine Positionen."""

    def setUp(self) -> None:
        self.jog = SimulatedJog()
        self.scanner = JogScanner()

    def feed(self, revolutions: float) -> None:
        for a, b, raw in self.jog.burst(revolutions):
            self.scanner.scan(a, b, touch_raw=raw)

    def test_one_turn_gives_exactly_one_revolution_of_steps(self) -> None:
        self.feed(1.0)
        state = self.scanner.state
        self.assertEqual(state.errors, 0)
        self.assertAlmostEqual(
            state.position, STEPS_PER_REV, delta=2
        )

    def test_turning_back_gives_negative_steps(self) -> None:
        self.feed(-0.5)
        self.assertLess(self.scanner.state.position, 0)
        self.assertAlmostEqual(
            self.scanner.state.position, -STEPS_PER_REV / 2, delta=2
        )

    def test_touching_lifts_the_raw_value_over_the_threshold(self) -> None:
        self.jog.touch(True)
        self.feed(0.01)
        self.assertTrue(self.scanner.state.touch)
        self.jog.touch(False)
        self.feed(0.01)
        self.assertFalse(self.scanner.state.touch)


# --------------------------------------------------------------------------
# Geraeteprotokoll und Weg in die Input-Schicht
# --------------------------------------------------------------------------


class HardwareJogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = InputLayer()
        self.events: list = []
        self.layer.subscribe(self.events.append)
        self.source = HardwareSource(self.layer, mapping={})

    def test_raw_lines_produce_no_input_events(self) -> None:
        self.assertIsNone(self.source.feed_line("Q JOG_MOVE 10"))
        self.assertIsNone(self.source.feed_line("C JOG_TOUCH 900"))
        self.assertEqual(self.events, [])

    def test_the_scan_program_counts_the_lines(self) -> None:
        for a, b in FORWARD_CYCLE * 3:
            self.source.feed_line(f"Q JOG_MOVE {int(a)}{int(b)}")
        self.assertEqual(self.source.jog.state.position, 11)

    def test_polling_bundles_the_movement_into_one_event(self) -> None:
        self.source.poll_jog()  # Startstand uebernehmen
        for a, b in FORWARD_CYCLE * 3:
            self.source.feed_line(f"Q JOG_MOVE {int(a)}{int(b)}")
        events = self.source.poll_jog()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].control_id, ids.JOG_MOVE)
        self.assertEqual(events[0].delta, 11)

    def test_polling_without_movement_stays_quiet(self) -> None:
        self.source.poll_jog()
        self.assertEqual(self.source.poll_jog(), [])

    def test_touch_is_reported_before_the_movement(self) -> None:
        self.source.poll_jog()
        self.source.feed_line("C JOG_TOUCH 900")
        for a, b in FORWARD_CYCLE:
            self.source.feed_line(f"Q JOG_MOVE {int(a)}{int(b)}")
        events = self.source.poll_jog()
        self.assertEqual(
            [e.control_id for e in events], [ids.JOG_TOUCH, ids.JOG_MOVE]
        )
        self.assertTrue(events[1].meta["touched"])

    def test_a_device_position_line_is_taken_over(self) -> None:
        self.source.feed_line("P JOG_MOVE 15427")
        self.assertEqual(self.source.jog.state.position, 15427)

    def test_wrong_control_ids_are_refused(self) -> None:
        with self.assertRaises(ProtocolError):
            self.source.feed_line("Q JOG_TOUCH 10")
        with self.assertRaises(ProtocolError):
            self.source.feed_line("C JOG_MOVE 900")

    def test_a_broken_level_pair_is_refused(self) -> None:
        with self.assertRaises(ProtocolError):
            self.source.feed_line("Q JOG_MOVE 12")

    def test_the_event_carries_the_resolution(self) -> None:
        self.source.poll_jog()
        self.source.feed_line("P JOG_MOVE 40")
        events = self.source.poll_jog()
        self.assertEqual(events[0].meta["ticks_per_rev"], STEPS_PER_REV)


class ControlListTests(unittest.TestCase):
    def test_the_component_list_matches_the_encoder(self) -> None:
        self.assertEqual(
            controls.get(ids.JOG_MOVE).ticks_per_rev, STEPS_PER_REV
        )


# --------------------------------------------------------------------------
# Sensor -> Input-Schicht -> Deck
# --------------------------------------------------------------------------


class JogToDeckTests(unittest.TestCase):
    """Der ganze Weg: Lichtschranken bis Trackposition."""

    def setUp(self) -> None:
        self.layer = InputLayer()
        self.deck = Deck(1)
        self.deck.load_track(make_track())
        self.mapper = InputMapper(1, self.deck.execute)
        self.layer.subscribe(self.mapper.handle_event)
        self.source = HardwareSource(self.layer, mapping={})
        self.source.poll_jog()  # Startstand uebernehmen
        self.deck.execute(command(CommandType.SEEK, 1, position_s=10.0))
        self.jog = SimulatedJog()

    def feed(self, revolutions: float) -> None:
        for a, b, raw in self.jog.burst(revolutions):
            self.source.jog.scan(a, b, touch_raw=raw)

    def test_touching_and_turning_moves_the_track_by_one_beat(self) -> None:
        self.jog.touch(True)
        self.feed(1.0)
        self.source.poll_jog()
        self.assertTrue(self.deck.state.jog_touch)
        # 120 BPM: ein Beat sind 0.5 s, eine Umdrehung also 0.5 s.
        self.assertAlmostEqual(self.deck.state.position_s, 10.5, places=2)

    def test_a_fast_turn_arrives_completely_in_one_poll(self) -> None:
        self.jog.touch(True)
        self.feed(4.0)  # vier Umdrehungen, ohne dazwischen abzuholen
        self.source.poll_jog()
        self.assertAlmostEqual(self.deck.state.position_s, 12.0, places=2)

    def test_without_touch_the_same_turn_only_bends(self) -> None:
        self.feed(1.0)
        self.source.poll_jog()
        self.assertFalse(self.deck.state.jog_touch)
        self.assertAlmostEqual(self.deck.state.position_s, 10.25, places=2)


if __name__ == "__main__":
    unittest.main()
