"""Tests des Anwendungsrahmens: Modus, Eingabeverteilung, Kalibrierung,
zentraler Hardware-Zustand.

Alles ohne Tkinter - diese Schicht kennt keine Oberflaeche.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from virtual_cdj.core import controls, ids
from virtual_cdj.core.input_layer import InputLayer
from virtual_cdj.core.model import Source
from virtual_cdj.jog import JogScanner
from virtual_cdj.shell import (
    MENU_ENTRIES,
    AnalogCalibration,
    ApplicationMode,
    CalibrationStore,
    HardwareState,
    InputRouter,
    ModeController,
    TouchCalibration,
    analog_control_ids,
)


# --------------------------------------------------------------------------
# Application_Mode
# --------------------------------------------------------------------------


class ModeControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.modes = ModeController()
        self.seen: list[ApplicationMode] = []
        self.modes.subscribe(self.seen.append)

    def test_performance_is_the_starting_mode(self) -> None:
        self.assertIs(self.modes.mode, ApplicationMode.PERFORMANCE)

    def test_a_change_reaches_the_listeners(self) -> None:
        self.modes.set_mode(ApplicationMode.TEST)
        self.assertEqual(self.seen, [ApplicationMode.TEST])

    def test_the_same_mode_again_changes_nothing(self) -> None:
        self.modes.set_mode(ApplicationMode.PERFORMANCE)
        self.assertEqual(self.seen, [])

    def test_the_menu_toggles_back_to_where_it_came_from(self) -> None:
        self.modes.set_mode(ApplicationMode.TEST)
        self.modes.toggle_menu()
        self.assertIs(self.modes.mode, ApplicationMode.MENU)
        self.modes.toggle_menu()
        self.assertIs(self.modes.mode, ApplicationMode.TEST)

    def test_only_performance_controls_the_deck(self) -> None:
        self.assertTrue(ApplicationMode.PERFORMANCE.controls_deck)
        for mode in (
            ApplicationMode.MENU, ApplicationMode.TEST,
            ApplicationMode.CALIBRATION, ApplicationMode.SETTINGS,
        ):
            self.assertFalse(mode.controls_deck, mode)

    def test_every_menu_entry_has_a_mode(self) -> None:
        modes = [entry.mode for entry in MENU_ENTRIES]
        self.assertIn(ApplicationMode.PERFORMANCE, modes)
        self.assertIn(ApplicationMode.TEST, modes)
        self.assertIn(ApplicationMode.CALIBRATION, modes)
        self.assertIn(ApplicationMode.SETTINGS, modes)
        self.assertEqual(len(set(modes)), len(modes))


# --------------------------------------------------------------------------
# Eingabeverteilung
# --------------------------------------------------------------------------


class InputRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = InputLayer()
        self.modes = ModeController()
        self.dj: list = []
        self.router = InputRouter(self.modes, performance_sink=self.dj.append)
        self.layer.subscribe(self.router.handle_event)

    def test_performance_reaches_the_dj_functions(self) -> None:
        self.layer.press(ids.PLAY)
        self.assertEqual(len(self.dj), 1)

    def test_test_mode_blocks_the_dj_functions(self) -> None:
        self.modes.set_mode(ApplicationMode.TEST)
        self.layer.press(ids.PLAY)
        self.layer.release(ids.PLAY)
        self.assertEqual(self.dj, [])
        self.assertEqual(self.router.blocked, 2)

    def test_every_mode_but_performance_blocks(self) -> None:
        for mode in (
            ApplicationMode.MENU, ApplicationMode.TEST,
            ApplicationMode.CALIBRATION, ApplicationMode.SETTINGS,
        ):
            self.modes.set_mode(mode)
            self.dj.clear()
            self.layer.press(ids.CUE)
            self.layer.release(ids.CUE)
            self.assertEqual(self.dj, [], mode)

    def test_an_observer_only_sees_its_own_mode(self) -> None:
        seen: list = []
        self.router.observe(ApplicationMode.TEST, seen.append)
        self.layer.press(ids.PLAY)  # Performance
        self.assertEqual(seen, [])
        self.modes.set_mode(ApplicationMode.TEST)
        self.layer.press(ids.CUE)
        self.assertEqual(len(seen), 1)

    def test_an_always_observer_sees_everything(self) -> None:
        seen: list = []
        self.router.observe_always(seen.append)
        self.layer.press(ids.PLAY)
        self.modes.set_mode(ApplicationMode.TEST)
        self.layer.press(ids.CUE)
        self.assertEqual(len(seen), 2)

    def test_observers_can_be_removed(self) -> None:
        seen: list = []
        remove = self.router.observe_always(seen.append)
        remove()
        self.layer.press(ids.PLAY)
        self.assertEqual(seen, [])

    def test_the_state_is_kept_in_every_mode(self) -> None:
        # Die Input-Schicht fuehrt den Zustand - der Router sperrt nur den
        # Weg zur Deck-Engine.
        self.modes.set_mode(ApplicationMode.TEST)
        self.layer.press(ids.PAD_A)
        self.assertTrue(self.layer.state.is_pressed(ids.PAD_A))


# --------------------------------------------------------------------------
# Kalibrierung
# --------------------------------------------------------------------------


class AnalogCalibrationTests(unittest.TestCase):
    def test_endpoints_map_to_zero_and_one(self) -> None:
        entry = AnalogCalibration(raw_min=100, raw_max=3900)
        self.assertAlmostEqual(entry.normalize(100), 0.0)
        self.assertAlmostEqual(entry.normalize(3900), 1.0)
        self.assertAlmostEqual(entry.normalize(2000), 0.5, places=2)

    def test_values_outside_the_range_are_clamped(self) -> None:
        entry = AnalogCalibration(raw_min=100, raw_max=3900)
        self.assertAlmostEqual(entry.normalize(0), 0.0)
        self.assertAlmostEqual(entry.normalize(5000), 1.0)

    def test_a_deadzone_makes_the_ends_reachable(self) -> None:
        entry = AnalogCalibration(raw_min=0, raw_max=1000, deadzone=0.05)
        self.assertAlmostEqual(entry.normalize(40), 0.0)
        self.assertAlmostEqual(entry.normalize(960), 1.0)
        self.assertAlmostEqual(entry.normalize(500), 0.5)

    def test_a_measured_center_lands_on_a_half(self) -> None:
        entry = AnalogCalibration(raw_min=0, raw_max=1000, center=400)
        self.assertAlmostEqual(entry.normalize(400), 0.5)
        self.assertAlmostEqual(entry.normalize(0), 0.0)
        self.assertAlmostEqual(entry.normalize(1000), 1.0)

    def test_invert_turns_the_direction(self) -> None:
        entry = AnalogCalibration(raw_min=0, raw_max=100, invert=True)
        self.assertAlmostEqual(entry.normalize(0), 1.0)
        self.assertAlmostEqual(entry.normalize(100), 0.0)

    def test_an_empty_range_is_not_usable(self) -> None:
        self.assertFalse(AnalogCalibration(raw_min=5, raw_max=5).is_usable)


class CalibrationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "calibration.json"
        self.store = CalibrationStore(path=self.path)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_without_a_measurement_the_control_defaults_apply(self) -> None:
        entry = self.store.analog(ids.TEMPO_FADER)
        control = controls.get(ids.TEMPO_FADER)
        self.assertEqual(entry.raw_min, control.raw_min)
        self.assertEqual(entry.raw_max, control.raw_max)
        self.assertFalse(self.store.is_measured(ids.TEMPO_FADER))

    def test_a_measurement_is_marked_as_such(self) -> None:
        self.store.set_analog(
            ids.TEMPO_FADER, AnalogCalibration(raw_min=120, raw_max=3800)
        )
        self.assertTrue(self.store.is_measured(ids.TEMPO_FADER))
        self.assertEqual(self.store.measured_ids(), (ids.TEMPO_FADER,))

    def test_an_empty_range_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_analog(
                ids.TEMPO_FADER, AnalogCalibration(raw_min=100, raw_max=100)
            )

    def test_clearing_brings_back_the_default(self) -> None:
        self.store.set_analog(
            ids.TEMPO_FADER, AnalogCalibration(raw_min=120, raw_max=3800)
        )
        self.store.clear_analog(ids.TEMPO_FADER)
        self.assertFalse(self.store.is_measured(ids.TEMPO_FADER))

    def test_normalize_is_only_answered_for_measured_controls(self) -> None:
        self.assertIsNone(self.store.normalize(ids.TEMPO_FADER, 2048))
        self.store.set_analog(
            ids.TEMPO_FADER, AnalogCalibration(raw_min=0, raw_max=1000)
        )
        self.assertAlmostEqual(
            self.store.normalize(ids.TEMPO_FADER, 500), 0.5
        )

    def test_an_inverted_touch_pair_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_touch(
                TouchCalibration(on_threshold=600, off_threshold=700)
            )

    def test_saving_and_loading_keeps_the_values(self) -> None:
        self.store.set_analog(
            ids.TEMPO_FADER,
            AnalogCalibration(
                raw_min=120, raw_max=3800, deadzone=0.02, center=1900
            ),
        )
        self.store.set_touch(
            TouchCalibration(on_threshold=820, off_threshold=780)
        )
        self.store.save()

        loaded = CalibrationStore.load(self.path)
        entry = loaded.analog(ids.TEMPO_FADER)
        self.assertEqual(entry.raw_min, 120)
        self.assertEqual(entry.raw_max, 3800)
        self.assertAlmostEqual(entry.deadzone, 0.02)
        self.assertAlmostEqual(entry.center, 1900)
        self.assertAlmostEqual(loaded.touch.on_threshold, 820)
        self.assertFalse(loaded.has_unsaved_changes)

    def test_unsaved_changes_are_reported(self) -> None:
        self.assertFalse(self.store.has_unsaved_changes)
        self.store.set_analog(
            ids.TEMPO_FADER, AnalogCalibration(raw_min=0, raw_max=10)
        )
        self.assertTrue(self.store.has_unsaved_changes)
        self.store.save()
        self.assertFalse(self.store.has_unsaved_changes)

    def test_a_missing_file_gives_the_defaults(self) -> None:
        loaded = CalibrationStore.load(self.path)
        self.assertEqual(loaded.measured_ids(), ())
        self.assertEqual(loaded.load_error, "")

    def test_a_broken_file_does_not_stop_the_start(self) -> None:
        self.path.write_text("{kaputt", encoding="utf-8")
        loaded = CalibrationStore.load(self.path)
        self.assertEqual(loaded.measured_ids(), ())
        self.assertNotEqual(loaded.load_error, "")

    def test_an_unusable_entry_in_the_file_is_skipped(self) -> None:
        broken = {"analog": {ids.TEMPO_FADER: {"raw_min": 5, "raw_max": 5}}}
        self.path.write_text(json.dumps(broken), encoding="utf-8")
        loaded = CalibrationStore.load(self.path)
        self.assertFalse(loaded.is_measured(ids.TEMPO_FADER))

    def test_all_analog_controls_are_known(self) -> None:
        found = analog_control_ids()
        self.assertIn(ids.TEMPO_FADER, found)
        self.assertIn(ids.VINYL_SPEED_ADJUST, found)


class InputLayerCalibrationTests(unittest.TestCase):
    """Die Normierung passiert in der Input-Schicht, nicht in der GUI."""

    def setUp(self) -> None:
        self.layer = InputLayer()
        self.store = CalibrationStore()

    def test_without_calibration_the_control_defaults_apply(self) -> None:
        self.layer.set_analog_raw(ids.TEMPO_FADER, 2048)
        self.assertAlmostEqual(
            self.layer.state.analog(ids.TEMPO_FADER), 0.5, places=2
        )

    def test_a_measured_range_is_used(self) -> None:
        self.layer.calibration = self.store
        self.store.set_analog(
            ids.TEMPO_FADER, AnalogCalibration(raw_min=1000, raw_max=2000)
        )
        self.layer.set_analog_raw(ids.TEMPO_FADER, 1500)
        self.assertAlmostEqual(self.layer.state.analog(ids.TEMPO_FADER), 0.5)
        self.layer.set_analog_raw(ids.TEMPO_FADER, 2000)
        self.assertAlmostEqual(self.layer.state.analog(ids.TEMPO_FADER), 1.0)

    def test_an_unmeasured_control_still_uses_the_defaults(self) -> None:
        self.layer.calibration = self.store
        self.layer.set_analog_raw(ids.VINYL_SPEED_ADJUST, 4095)
        self.assertAlmostEqual(
            self.layer.state.analog(ids.VINYL_SPEED_ADJUST), 1.0
        )

    def test_the_raw_value_stays_available(self) -> None:
        self.layer.calibration = self.store
        self.layer.set_analog_raw(ids.TEMPO_FADER, 1234)
        self.assertEqual(self.layer.state.raw(ids.TEMPO_FADER), 1234)


# --------------------------------------------------------------------------
# Zentraler Hardware-Zustand
# --------------------------------------------------------------------------


class HardwareStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = InputLayer()
        self.scanner = JogScanner()
        self.hardware = HardwareState(self.layer, jog=self.scanner)

    def test_buttons_are_reported_as_zero_or_one(self) -> None:
        self.assertFalse(self.hardware.button(ids.PLAY))
        self.layer.press(ids.PLAY)
        self.assertTrue(self.hardware.button(ids.PLAY))
        self.assertIn(ids.PLAY, self.hardware.pressed_ids())

    def test_every_button_of_the_control_list_appears(self) -> None:
        buttons = self.hardware.buttons()
        self.assertIn(ids.PLAY, buttons)
        self.assertIn(ids.PAD_A, buttons)
        self.assertNotIn(ids.TEMPO_FADER, buttons)

    def test_analog_readings_carry_raw_and_normalized(self) -> None:
        self.layer.set_analog_raw(ids.TEMPO_FADER, 1024)
        reading = self.hardware.analog(ids.TEMPO_FADER)
        self.assertEqual(reading.raw, 1024)
        self.assertAlmostEqual(reading.value, 0.25, places=2)
        self.assertFalse(reading.calibrated)

    def test_a_measured_control_is_marked(self) -> None:
        store = CalibrationStore()
        store.set_analog(
            ids.TEMPO_FADER, AnalogCalibration(raw_min=0, raw_max=100)
        )
        hardware = HardwareState(InputLayer(), calibration=store)
        self.assertTrue(hardware.analog(ids.TEMPO_FADER).calibrated)

    def test_switches_and_encoders_are_reported(self) -> None:
        self.layer.set_switch(ids.DIRECTION, "REV")
        self.layer.rotate(ids.BROWSE_ROTATE, 3)
        self.assertEqual(self.hardware.switches()[ids.DIRECTION], "REV")
        self.assertEqual(self.hardware.encoders()[ids.BROWSE_ROTATE], 3)

    def test_leds_can_be_switched_one_by_one(self) -> None:
        self.assertFalse(self.hardware.leds()[ids.PLAY])
        self.assertTrue(self.hardware.toggle_led(ids.PLAY))
        self.assertTrue(self.hardware.leds()[ids.PLAY])
        self.hardware.set_led(ids.PLAY, False)
        self.assertFalse(self.hardware.leds()[ids.PLAY])

    def test_all_leds_can_be_switched_together(self) -> None:
        self.hardware.all_leds(True)
        self.assertTrue(all(self.hardware.leds().values()))
        self.hardware.all_leds(False)
        self.assertFalse(any(self.hardware.leds().values()))

    def test_the_jog_reading_comes_from_the_scan_program(self) -> None:
        self.scanner.set_position(1000)
        self.hardware.read_jog()          # Startstand
        self.scanner.set_position(1050)
        reading = self.hardware.read_jog()
        self.assertEqual(reading.position, 1050)
        self.assertEqual(reading.delta, 50)
        self.assertEqual(reading.direction, "CW")
        self.assertAlmostEqual(reading.beats, 50 / 800)

    def test_the_jog_reading_shows_the_sensor_levels(self) -> None:
        self.scanner.scan(True, False, touch_raw=900)
        reading = self.hardware.read_jog()
        self.assertTrue(reading.sensor_a)
        self.assertFalse(reading.sensor_b)
        self.assertEqual(reading.state_text, "10")
        self.assertTrue(reading.touch)

    def test_invalid_quadrature_changes_are_counted(self) -> None:
        self.scanner.scan(False, False)
        self.scanner.scan(True, True)
        self.assertEqual(self.hardware.read_jog().errors, 1)

    def test_without_a_scanner_the_panel_movement_is_used(self) -> None:
        hardware = HardwareState(self.layer)
        hardware.read_jog()
        self.layer.jog_move(ids.JOG_MOVE, 40)
        reading = hardware.read_jog()
        self.assertEqual(reading.delta, 40)
        self.assertEqual(reading.position, 40)

    def test_two_readers_do_not_steal_each_others_movement(self) -> None:
        other = HardwareState(InputLayer(), jog=self.scanner)
        self.hardware.read_jog()
        other.read_jog()
        self.scanner.set_position(80)
        self.assertEqual(self.hardware.read_jog().delta, 80)
        self.assertEqual(other.read_jog().delta, 80)

    def test_the_link_status_is_honest_without_a_device(self) -> None:
        link = self.hardware.link()
        self.assertEqual(link.events, 0)
        self.assertFalse(link.hardware_connected)
        self.assertIn("keine Eingabe", link.text)

    def test_the_link_status_notices_hardware(self) -> None:
        self.layer.press(ids.PLAY, Source.HARDWARE)
        link = self.hardware.link()
        self.assertTrue(link.hardware_connected)
        self.assertEqual(link.source, "HARDWARE")
        self.assertEqual(link.events, 1)


if __name__ == "__main__":
    unittest.main()
