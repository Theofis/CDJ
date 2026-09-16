"""Tests der zentralen Input-Schicht.

Diese Tests laufen ohne GUI. Sie pruefen genau die Punkte, die spaeter beim
Anschluss echter Hardware falsch laufen koennen.
"""

from __future__ import annotations

import unittest

from virtual_cdj.core import controls, ids
from virtual_cdj.core.controller import CdjController
from virtual_cdj.core.input_layer import (
    ControlTypeMismatch,
    InputLayer,
    UnknownControlError,
)
from virtual_cdj.core.model import ControlType, Direction, EventType, Source


class Recorder:
    def __init__(self, layer: InputLayer) -> None:
        self.events: list = []
        layer.subscribe(self.events.append)

    def ids_and_events(self) -> list[tuple[str, str]]:
        return [(e.control_id, e.event.value) for e in self.events]


class DigitalButtonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = InputLayer()
        self.rec = Recorder(self.layer)

    def test_press_and_release_emit_exactly_one_event_each(self) -> None:
        self.layer.press(ids.PLAY)
        self.layer.release(ids.PLAY)
        self.assertEqual(
            self.rec.ids_and_events(),
            [(ids.PLAY, "PRESS"), (ids.PLAY, "RELEASE")],
        )

    def test_repeated_press_does_not_emit_again(self) -> None:
        self.assertIsNotNone(self.layer.press(ids.PLAY))
        self.assertIsNone(self.layer.press(ids.PLAY))
        self.assertIsNone(self.layer.press(ids.PLAY))
        self.assertEqual(len(self.rec.events), 1)

    def test_release_without_press_is_ignored(self) -> None:
        self.assertIsNone(self.layer.release(ids.PLAY))
        self.assertEqual(self.rec.events, [])

    def test_button_is_not_a_toggle(self) -> None:
        self.layer.press(ids.CUE)
        self.assertTrue(self.layer.state.is_pressed(ids.CUE))
        self.layer.press(ids.CUE)
        self.assertTrue(self.layer.state.is_pressed(ids.CUE))
        self.layer.release(ids.CUE)
        self.assertFalse(self.layer.state.is_pressed(ids.CUE))

    def test_event_carries_value_and_source(self) -> None:
        event = self.layer.press(ids.PLAY, Source.HARDWARE)
        assert event is not None
        self.assertEqual(event.value, 1)
        self.assertEqual(event.source, Source.HARDWARE)
        self.assertEqual(event.control_type, ControlType.DIGITAL_BUTTON)
        self.assertGreater(event.timestamp, 0)

    def test_unknown_id_is_rejected(self) -> None:
        with self.assertRaises(UnknownControlError):
            self.layer.press("AUTO_CUE")

    def test_wrong_type_is_rejected(self) -> None:
        with self.assertRaises(ControlTypeMismatch):
            self.layer.press(ids.TEMPO_FADER)


class SimultaneousPressTests(unittest.TestCase):
    """Mehrere gleichzeitig gedrueckte Tasten muessen erhalten bleiben."""

    def setUp(self) -> None:
        self.layer = InputLayer()

    def test_two_buttons_held_at_the_same_time(self) -> None:
        self.layer.press(ids.TAG_TRACK_REMOVE)
        self.layer.press(ids.PAD_C)

        self.assertTrue(self.layer.state.is_pressed(ids.TAG_TRACK_REMOVE))
        self.assertTrue(self.layer.state.is_pressed(ids.PAD_C))
        self.assertEqual(
            self.layer.state.pressed_ids(), (ids.PAD_C, ids.TAG_TRACK_REMOVE)
        )

    def test_releasing_one_keeps_the_other(self) -> None:
        self.layer.press(ids.TAG_TRACK_REMOVE)
        self.layer.press(ids.PAD_C)
        self.layer.release(ids.PAD_C)

        self.assertTrue(self.layer.state.is_pressed(ids.TAG_TRACK_REMOVE))
        self.assertFalse(self.layer.state.is_pressed(ids.PAD_C))

    def test_listener_sees_complete_chord(self) -> None:
        seen: list[tuple[str, ...]] = []
        self.layer.subscribe(
            lambda e: seen.append(self.layer.state.pressed_ids())
        )
        self.layer.press(ids.DELETE)
        self.layer.press(ids.MEMORY)

        self.assertEqual(seen[0], (ids.DELETE,))
        self.assertEqual(seen[1], (ids.DELETE, ids.MEMORY))

    def test_eight_pads_can_be_held_together(self) -> None:
        for pad in ids.PADS:
            self.layer.press(pad)
        self.assertEqual(self.layer.state.pressed_ids(), tuple(sorted(ids.PADS)))


class AnalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = InputLayer()
        self.rec = Recorder(self.layer)

    def test_default_value_is_set(self) -> None:
        control = controls.get(ids.TEMPO_FADER)
        self.assertEqual(
            self.layer.state.analog(ids.TEMPO_FADER), control.default_value
        )

    def test_continuous_values_between_zero_and_one(self) -> None:
        for step in range(0, 101):
            self.layer.set_analog(ids.TEMPO_FADER, step / 100.0)
        values = [e.value for e in self.rec.events]
        self.assertTrue(all(0.0 <= v <= 1.0 for v in values))
        self.assertEqual(values[0], 0.0)
        self.assertEqual(values[-1], 1.0)

    def test_value_is_clamped(self) -> None:
        self.layer.set_analog(ids.TEMPO_FADER, 5.0)
        self.assertEqual(self.layer.state.analog(ids.TEMPO_FADER), 1.0)
        self.layer.set_analog(ids.TEMPO_FADER, -5.0)
        self.assertEqual(self.layer.state.analog(ids.TEMPO_FADER), 0.0)

    def test_unchanged_value_emits_nothing(self) -> None:
        self.layer.set_analog(ids.TEMPO_FADER, 0.25)
        count = len(self.rec.events)
        self.assertIsNone(self.layer.set_analog(ids.TEMPO_FADER, 0.25))
        self.assertEqual(len(self.rec.events), count)

    def test_raw_adc_value_is_normalised(self) -> None:
        control = controls.get(ids.TEMPO_FADER)
        self.assertEqual((control.raw_min, control.raw_max), (0, 4095))

        event = self.layer.set_analog_raw(ids.TEMPO_FADER, 4095)
        assert event is not None
        self.assertAlmostEqual(event.value, 1.0)
        self.assertEqual(event.raw, 4095)

        event = self.layer.set_analog_raw(ids.TEMPO_FADER, 0)
        assert event is not None
        self.assertAlmostEqual(event.value, 0.0)

        event = self.layer.set_analog_raw(ids.TEMPO_FADER, 2048)
        assert event is not None
        self.assertAlmostEqual(event.value, 0.5001, places=3)

    def test_raw_value_never_leaves_as_control_value(self) -> None:
        event = self.layer.set_analog_raw(ids.TEMPO_FADER, 3000)
        assert event is not None
        self.assertLessEqual(event.value, 1.0)
        self.assertNotEqual(event.value, 3000)


class EncoderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = InputLayer()
        self.rec = Recorder(self.layer)

    def test_clockwise_gives_positive_relative_values(self) -> None:
        for _ in range(3):
            self.layer.rotate(ids.BROWSE_ROTATE, +1)
        self.assertEqual([e.delta for e in self.rec.events], [1, 1, 1])
        self.assertTrue(
            all(e.direction is Direction.CW for e in self.rec.events)
        )
        self.assertEqual(self.layer.state.encoder_total(ids.BROWSE_ROTATE), 3)

    def test_counter_clockwise_gives_negative_relative_values(self) -> None:
        self.layer.rotate(ids.BROWSE_ROTATE, -2)
        event = self.rec.events[-1]
        self.assertEqual(event.delta, -2)
        self.assertIs(event.direction, Direction.CCW)
        self.assertEqual(self.layer.state.encoder_total(ids.BROWSE_ROTATE), -2)

    def test_zero_delta_emits_nothing(self) -> None:
        self.assertIsNone(self.layer.rotate(ids.BROWSE_ROTATE, 0))
        self.assertEqual(self.rec.events, [])

    def test_rotation_and_button_are_separate_inputs(self) -> None:
        self.assertIn(ids.BROWSE_ROTATE, controls.CONTROLS)
        self.assertIn(ids.BROWSE_PRESS, controls.CONTROLS)
        self.assertIs(
            controls.get(ids.BROWSE_ROTATE).type, ControlType.ENCODER
        )
        self.assertIs(
            controls.get(ids.BROWSE_PRESS).type, ControlType.DIGITAL_BUTTON
        )

        self.layer.press(ids.BROWSE_PRESS)
        self.layer.rotate(ids.BROWSE_ROTATE, +1)
        self.assertEqual(
            self.rec.ids_and_events(),
            [(ids.BROWSE_PRESS, "PRESS"), (ids.BROWSE_ROTATE, "ROTATE")],
        )
        self.assertTrue(self.layer.state.is_pressed(ids.BROWSE_PRESS))


class JogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = InputLayer()
        self.rec = Recorder(self.layer)

    def test_jog_is_not_an_analog_control(self) -> None:
        self.assertIs(controls.get(ids.JOG_MOVE).type, ControlType.JOG)
        with self.assertRaises(ControlTypeMismatch):
            self.layer.set_analog(ids.JOG_MOVE, 0.5)

    def test_slow_clockwise(self) -> None:
        base = 1_000_000.0
        for i in range(4):
            self.layer.jog_move(ids.JOG_MOVE, +1, timestamp=base + i * 50)
        self.assertEqual([e.delta for e in self.rec.events], [1, 1, 1, 1])
        self.assertTrue(all(e.direction is Direction.CW for e in self.rec.events))
        self.assertEqual(self.layer.state.jog(ids.JOG_MOVE).total, 4)

    def test_slow_counter_clockwise(self) -> None:
        base = 1_000_000.0
        for i in range(4):
            self.layer.jog_move(ids.JOG_MOVE, -1, timestamp=base + i * 50)
        self.assertTrue(
            all(e.direction is Direction.CCW for e in self.rec.events)
        )
        self.assertEqual(self.layer.state.jog(ids.JOG_MOVE).total, -4)

    def test_fast_movement_has_higher_velocity_than_slow(self) -> None:
        base = 1_000_000.0
        for i in range(1, 21):
            self.layer.jog_move(ids.JOG_MOVE, +1, timestamp=base + i * 200)
        slow = abs(self.layer.state.jog(ids.JOG_MOVE).velocity)

        layer = InputLayer()
        for i in range(1, 21):
            layer.jog_move(ids.JOG_MOVE, +20, timestamp=base + i * 5)
        fast = abs(layer.state.jog(ids.JOG_MOVE).velocity)

        self.assertGreater(fast, slow * 10)

    def test_backspin_reverses_direction_and_sign(self) -> None:
        base = 1_000_000.0
        self.layer.jog_move(ids.JOG_MOVE, +30, timestamp=base)
        self.layer.jog_move(ids.JOG_MOVE, +30, timestamp=base + 5)
        forward = self.layer.state.jog(ids.JOG_MOVE).velocity
        self.assertGreater(forward, 0)

        for i in range(1, 11):
            self.layer.jog_move(ids.JOG_MOVE, -60, timestamp=base + 5 + i * 5)
        jog = self.layer.state.jog(ids.JOG_MOVE)
        self.assertLess(jog.velocity, 0)
        self.assertIs(jog.last_direction, Direction.CCW)

    def test_move_event_reports_velocity_and_direction(self) -> None:
        base = 1_000_000.0
        self.layer.jog_move(ids.JOG_MOVE, +5, timestamp=base)
        event = self.layer.jog_move(ids.JOG_MOVE, +5, timestamp=base + 10)
        assert event is not None
        self.assertIs(event.event, EventType.MOVE)
        self.assertIn("velocity_ticks_per_s", event.meta)
        self.assertIn("rev_per_s", event.meta)
        self.assertGreater(event.meta["velocity_ticks_per_s"], 0)

    def test_zero_delta_emits_nothing(self) -> None:
        self.assertIsNone(self.layer.jog_move(ids.JOG_MOVE, 0))


class JogTouchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = InputLayer()
        self.rec = Recorder(self.layer)

    def test_touch_is_a_digital_state(self) -> None:
        self.assertIs(
            controls.get(ids.JOG_TOUCH).type, ControlType.DIGITAL_BUTTON
        )
        self.layer.jog_touch(ids.JOG_TOUCH, True)
        self.layer.jog_touch(ids.JOG_TOUCH, False)
        self.assertEqual(
            self.rec.ids_and_events(),
            [(ids.JOG_TOUCH, "PRESS"), (ids.JOG_TOUCH, "RELEASE")],
        )

    def test_touch_and_move_coexist(self) -> None:
        self.layer.jog_touch(ids.JOG_TOUCH, True)
        event = self.layer.jog_move(ids.JOG_MOVE, +3)
        assert event is not None

        self.assertTrue(self.layer.state.is_pressed(ids.JOG_TOUCH))
        self.assertTrue(event.meta["touched"])
        self.assertEqual(self.layer.state.jog(ids.JOG_MOVE).total, 3)

        self.layer.jog_touch(ids.JOG_TOUCH, False)
        event = self.layer.jog_move(ids.JOG_MOVE, +1)
        assert event is not None
        self.assertFalse(event.meta["touched"])
        self.assertFalse(self.layer.state.is_pressed(ids.JOG_TOUCH))

    def test_touch_does_not_repeat(self) -> None:
        self.layer.jog_touch(ids.JOG_TOUCH, True)
        self.assertIsNone(self.layer.jog_touch(ids.JOG_TOUCH, True))
        self.assertEqual(len(self.rec.events), 1)


class SwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = InputLayer()
        self.rec = Recorder(self.layer)

    def test_default_position(self) -> None:
        self.assertEqual(self.layer.state.switch(ids.DIRECTION), "FWD")

    def test_changing_position_emits_event(self) -> None:
        event = self.layer.set_switch(ids.DIRECTION, "REV")
        assert event is not None
        self.assertIs(event.event, EventType.POSITION)
        self.assertEqual(event.position, "REV")
        self.assertEqual(self.layer.state.switch(ids.DIRECTION), "REV")

    def test_same_position_emits_nothing(self) -> None:
        self.assertIsNone(self.layer.set_switch(ids.DIRECTION, "FWD"))

    def test_invalid_position_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.layer.set_switch(ids.DIRECTION, "TURBO")


class ConfirmedPanelButtonTests(unittest.TestCase):
    """Die korrigierten Taster muessen eigenstaendige Inputs sein."""

    def test_both_beat_loop_buttons_exist(self) -> None:
        for control_id in (ids.BEAT_LOOP_4, ids.BEAT_LOOP_8):
            self.assertIn(control_id, controls.CONTROLS)
            self.assertIs(
                controls.get(control_id).type, ControlType.DIGITAL_BUTTON
            )

    def test_beat_loop_is_independent_from_pad_input(self) -> None:
        layer = InputLayer()
        rec = Recorder(layer)
        layer.press(ids.BEAT_LOOP_4)
        layer.release(ids.BEAT_LOOP_4)
        layer.press(ids.PAD_A)
        layer.release(ids.PAD_A)
        self.assertEqual(
            rec.ids_and_events(),
            [
                (ids.BEAT_LOOP_4, "PRESS"),
                (ids.BEAT_LOOP_4, "RELEASE"),
                (ids.PAD_A, "PRESS"),
                (ids.PAD_A, "RELEASE"),
            ],
        )

    def test_no_auto_cue_control_exists(self) -> None:
        for control_id in controls.CONTROLS:
            self.assertNotIn("AUTO_CUE", control_id)


class LedTests(unittest.TestCase):
    def test_led_can_be_set_only_where_present(self) -> None:
        layer = InputLayer()
        layer.set_led(ids.PLAY, True)
        self.assertTrue(layer.state.led(ids.PLAY))
        with self.assertRaises(ValueError):
            layer.set_led(ids.DELETE, True)


class ControllerTests(unittest.TestCase):
    def test_controller_receives_events_without_any_function_assigned(self) -> None:
        layer = InputLayer()
        controller = CdjController(layer)

        layer.press(ids.PLAY)
        layer.release(ids.PLAY)

        self.assertEqual(len(controller.history), 2)
        self.assertEqual(controller.unhandled_count, 2)
        self.assertEqual(controller.assigned_functions(), ())
        self.assertTrue(
            all(handler is None for handler in controller.functions.values())
        )

    def test_controller_sees_chord(self) -> None:
        layer = InputLayer()
        controller = CdjController(layer)
        layer.press(ids.TAG_TRACK_REMOVE)
        layer.press(ids.PAD_B)
        self.assertEqual(controller.chord(), (ids.PAD_B, ids.TAG_TRACK_REMOVE))

    def test_controller_does_not_care_about_the_source(self) -> None:
        layer = InputLayer()
        controller = CdjController(layer)
        layer.press(ids.PLAY, Source.VIRTUAL)
        layer.release(ids.PLAY, Source.VIRTUAL)
        layer.press(ids.PLAY, Source.HARDWARE)
        layer.release(ids.PLAY, Source.HARDWARE)

        sources = [e.source for e in controller.history]
        self.assertEqual(
            sources,
            [Source.VIRTUAL, Source.VIRTUAL, Source.HARDWARE, Source.HARDWARE],
        )
        events = [(e.control_id, e.event.value) for e in controller.history]
        self.assertEqual(events[0:2], events[2:4])


if __name__ == "__main__":
    unittest.main()
