"""Tests der Seitenstruktur des CDJ-Bildschirms.

Geprueft wird der Rahmen: welcher Modus welche Seite zeigt, dass die
Performance-Oberflaeche unveraendert weiterlaeuft und dass Test- und
Kalibrierseite nur aus dem zentralen Hardware-Zustand lesen.

Es wird ein echtes Tk-Fenster aufgebaut, aber nicht angezeigt.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import tkinter as tk

    _root = tk.Tk()
    _root.withdraw()
    _root.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - kopfloser Rechner
    TK_AVAILABLE = False

from virtual_cdj.core import ids
from virtual_cdj.core.input_layer import InputLayer
from virtual_cdj.deck.commands import CommandType
from virtual_cdj.deck.engine import Deck
from virtual_cdj.deck.provider import LocalDeckStateProvider
from virtual_cdj.deck.state import BeatGrid, TrackInfo
from virtual_cdj.jog import JogScanner
from virtual_cdj.shell import (
    AnalogCalibration,
    ApplicationMode,
    CalibrationStore,
    HardwareState,
    InputRouter,
    ModeController,
)


def make_track() -> TrackInfo:
    return TrackInfo(
        track_id="t1", title="Testtrack", duration_s=120.0,
        original_bpm=120.0,
        beat_grid=BeatGrid(first_beat_s=0.0, bpm=120.0),
    )


class Rig:
    """Ein Fenster mit allen Schichten, wie ``run_cdj.py`` es baut."""

    def __init__(self, path: Path) -> None:
        from virtual_cdj.cdj_ui.window import CdjDisplayApp

        self.layer = InputLayer()
        self.modes = ModeController()
        self.store = CalibrationStore(path=path)
        self.layer.calibration = self.store
        self.scanner = JogScanner()
        self.hardware = HardwareState(
            self.layer, jog=self.scanner, calibration=self.store
        )
        #: Steuerbare Uhr - damit laesst sich pruefen, ob der Transport
        #: waehrend eines anderen Modus weiterlaeuft.
        self.now = 0.0
        self.deck = Deck(1, time_source=lambda: self.now)
        self.deck.load_track(make_track())
        self.provider = LocalDeckStateProvider(self.deck)
        self.mapper_calls: list = []

        self.router = InputRouter(self.modes)
        self.layer.subscribe(self.router.handle_event)
        from virtual_cdj.deck.mapping import InputMapper

        mapper = InputMapper(1, self.provider.send)
        self.router.performance_sink = mapper.handle_event

        self.app = CdjDisplayApp()
        self.window = self.app.add_display(
            self.provider,
            modes=self.modes,
            hardware=self.hardware,
            calibration=self.store,
            jog=self.scanner,
            audio_status=lambda: "kein Ausgabegeraet",
        )
        self.app.update()

    def tap(self, control_id: str) -> None:
        self.layer.press(control_id)
        self.layer.release(control_id)
        self.app.update()

    def mode(self, mode: ApplicationMode):
        self.modes.set_mode(mode)
        self.app.update()
        return self.window.host.current

    def advance(self, seconds: float) -> None:
        """Uhr weiterstellen und das Deck takten - wie der Takt der
        Anwendung, der unabhaengig von der angezeigten Seite laeuft."""
        self.now += seconds
        self.deck.tick()
        self.app.update()

    def close(self) -> None:
        self.app.destroy()


@unittest.skipUnless(TK_AVAILABLE, "keine Tk-Anzeige verfuegbar")
class PageHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.rig = Rig(Path(self.directory.name) / "calibration.json")

    def tearDown(self) -> None:
        self.rig.close()
        self.directory.cleanup()

    def test_performance_is_shown_at_the_start(self) -> None:
        page = self.rig.window.host.current
        self.assertIs(page.mode, ApplicationMode.PERFORMANCE)
        self.assertIs(page.screen, self.rig.window.screen)

    def test_every_mode_has_its_page(self) -> None:
        for mode in ApplicationMode:
            page = self.rig.mode(mode)
            self.assertIsNotNone(page, mode)
            self.assertIs(page.mode, mode)

    def test_only_one_page_is_visible(self) -> None:
        self.rig.mode(ApplicationMode.TEST)
        visible = [
            page for page in self.rig.window.host._pages.values()
            if page.winfo_ismapped()
        ]
        self.assertEqual(len(visible), 1)
        self.assertIs(visible[0].mode, ApplicationMode.TEST)

    def test_the_performance_loop_pauses_and_resumes(self) -> None:
        screen = self.rig.window.screen
        self.assertTrue(screen._running)
        self.rig.mode(ApplicationMode.TEST)
        self.assertFalse(screen._running)
        self.rig.mode(ApplicationMode.PERFORMANCE)
        self.assertTrue(screen._running)

    def test_the_deck_keeps_its_track_across_a_mode_change(self) -> None:
        self.rig.mode(ApplicationMode.TEST)
        self.rig.mode(ApplicationMode.CALIBRATION)
        self.rig.mode(ApplicationMode.PERFORMANCE)
        self.assertTrue(self.rig.deck.state.has_track)

    def test_the_track_keeps_playing_in_another_mode(self) -> None:
        # Deck und Audio takten aus der Anwendung, nicht aus der Anzeige.
        self.rig.tap(ids.PLAY)
        self.assertTrue(self.rig.deck.state.is_playing)
        self.rig.mode(ApplicationMode.TEST)
        self.rig.advance(2.0)
        self.assertTrue(self.rig.deck.state.is_playing)
        self.assertAlmostEqual(self.rig.deck.state.position_s, 2.0, places=3)

    def test_f1_opens_and_closes_the_menu(self) -> None:
        self.rig.window.toggle_menu()
        self.rig.app.update()
        self.assertIs(self.rig.modes.mode, ApplicationMode.MENU)
        self.rig.window.toggle_menu()
        self.rig.app.update()
        self.assertIs(self.rig.modes.mode, ApplicationMode.PERFORMANCE)

    def test_escape_returns_to_performance(self) -> None:
        self.rig.mode(ApplicationMode.TEST)
        self.rig.window._on_escape()
        self.assertIs(self.rig.modes.mode, ApplicationMode.PERFORMANCE)

    def test_a_window_without_hardware_only_has_performance(self) -> None:
        from virtual_cdj.cdj_ui.window import CdjDisplayApp

        app = CdjDisplayApp()
        window = app.add_display(LocalDeckStateProvider(Deck(2)))
        app.update()
        try:
            self.assertIsNone(window.host.page(ApplicationMode.TEST))
            # Ein Wechsel in einen Modus ohne Seite bleibt wirkungslos.
            window.set_mode(ApplicationMode.TEST)
            self.assertIs(window.modes.mode, ApplicationMode.PERFORMANCE)
        finally:
            app.destroy()


@unittest.skipUnless(TK_AVAILABLE, "keine Tk-Anzeige verfuegbar")
class MenuPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.rig = Rig(Path(self.directory.name) / "calibration.json")
        self.page = self.rig.mode(ApplicationMode.MENU)

    def tearDown(self) -> None:
        self.rig.close()
        self.directory.cleanup()

    def test_the_entry_you_came_from_is_preselected(self) -> None:
        self.rig.mode(ApplicationMode.TEST)
        page = self.rig.mode(ApplicationMode.MENU)
        from virtual_cdj.shell.modes import MENU_ENTRIES

        self.assertIs(
            MENU_ENTRIES[page.selected].mode, ApplicationMode.TEST
        )

    def test_moving_and_activating_switches_the_mode(self) -> None:
        from virtual_cdj.shell.modes import MENU_ENTRIES

        self.page.selected = 0
        self.page.move(+1)
        self.assertEqual(self.page.selected, 1)
        mode = self.page.activate()
        self.assertIs(mode, MENU_ENTRIES[1].mode)
        self.assertIs(self.rig.modes.mode, MENU_ENTRIES[1].mode)

    def test_moving_stops_at_the_ends(self) -> None:
        self.page.selected = 0
        self.page.move(-5)
        self.assertEqual(self.page.selected, 0)
        self.page.move(+50)
        self.assertEqual(self.page.selected, len(self.page._rows) - 1)

    def test_a_click_selects_the_row_under_the_pointer(self) -> None:
        self.page.refresh()
        top, bottom, mode = self.page._rows[2]

        class Click:
            y = (top + bottom) / 2

        self.page._on_click(Click())
        self.assertIs(self.rig.modes.mode, mode)


@unittest.skipUnless(TK_AVAILABLE, "keine Tk-Anzeige verfuegbar")
class TestPageTests(unittest.TestCase):
    """Die Pruefseite zeigt Hardware - und loest keine DJ-Funktion aus."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.rig = Rig(Path(self.directory.name) / "calibration.json")
        self.page = self.rig.mode(ApplicationMode.TEST)

    def tearDown(self) -> None:
        self.rig.close()
        self.directory.cleanup()

    def test_a_pressed_button_shows_up_but_does_not_reach_the_deck(self) -> None:
        generation = self.rig.deck.state.generation
        self.rig.layer.press(ids.PAD_A)
        self.page.refresh()
        self.assertTrue(self.page.hardware.button(ids.PAD_A))
        self.assertEqual(self.rig.deck.state.generation, generation)
        self.assertEqual(len(self.rig.deck.state.track.hot_cues), 0)

    def test_play_does_not_start_the_track(self) -> None:
        self.rig.tap(ids.PLAY)
        self.assertFalse(self.rig.deck.state.is_playing)

    def test_the_page_draws_without_a_device(self) -> None:
        self.page.refresh()
        self.assertGreater(len(self.page.canvas.find_all()), 20)

    def test_an_led_can_be_switched_from_the_page(self) -> None:
        self.page.refresh()
        hits = [h for h in self.page._hits if h[4] == "led"]
        self.assertTrue(hits)
        x0, y0, x1, y1, _action, control_id = hits[0]

        class Click:
            x = (x0 + x1) / 2
            y = (y0 + y1) / 2

        self.page._on_click(Click())
        self.assertTrue(self.rig.hardware.leds()[control_id])

    def test_all_leds_at_once(self) -> None:
        self.page.refresh()
        on = next(h for h in self.page._hits if h[4] == "all" and h[5] == "1")

        class Click:
            x = (on[0] + on[2]) / 2
            y = (on[1] + on[3]) / 2

        self.page._on_click(Click())
        self.assertTrue(all(self.rig.hardware.leds().values()))

    def test_the_page_offers_a_way_back_without_a_keyboard(self) -> None:
        self.page.refresh()
        hit = next(h for h in self.page._hits if h[4] == "menu")

        class Click:
            x = (hit[0] + hit[2]) / 2
            y = (hit[1] + hit[3]) / 2

        self.page._on_click(Click())
        self.assertIs(self.rig.modes.mode, ApplicationMode.MENU)

    def test_the_jog_values_come_from_the_scan_program(self) -> None:
        self.rig.scanner.scan(True, False, touch_raw=900)
        self.page.refresh()
        reading = self.rig.hardware.jog
        self.assertTrue(reading.touch)
        self.assertEqual(reading.state_text, "10")


@unittest.skipUnless(TK_AVAILABLE, "keine Tk-Anzeige verfuegbar")
class CalibrationPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "calibration.json"
        self.rig = Rig(self.path)
        self.page = self.rig.mode(ApplicationMode.CALIBRATION)

    def tearDown(self) -> None:
        self.rig.close()
        self.directory.cleanup()

    def _move_fader(self, *raw_values: float) -> None:
        for value in raw_values:
            self.rig.layer.set_analog_raw(ids.TEMPO_FADER, value)
            self.page.refresh()

    def test_measuring_takes_over_the_travelled_range(self) -> None:
        self.page._show_tab("FADER / POTI")
        self.page.start_measuring(ids.TEMPO_FADER)
        self._move_fader(300, 2000, 3700)
        self.assertTrue(self.page.finish_measuring(ids.TEMPO_FADER))
        entry = self.rig.store.analog(ids.TEMPO_FADER)
        self.assertEqual(entry.raw_min, 300)
        self.assertEqual(entry.raw_max, 3700)
        self.assertTrue(self.rig.store.is_measured(ids.TEMPO_FADER))

    def test_a_measurement_without_movement_is_not_taken_over(self) -> None:
        self.page._show_tab("FADER / POTI")
        self.rig.layer.set_analog_raw(ids.TEMPO_FADER, 1000)
        self.page.start_measuring(ids.TEMPO_FADER)
        self.assertFalse(self.page.finish_measuring(ids.TEMPO_FADER))
        self.assertFalse(self.rig.store.is_measured(ids.TEMPO_FADER))
        self.assertIn("keine Bewegung", self.page.message)

    def test_the_measured_range_reaches_the_input_layer(self) -> None:
        self.page.start_measuring(ids.TEMPO_FADER)
        self._move_fader(1000, 2000)
        self.page.finish_measuring(ids.TEMPO_FADER)
        self.rig.layer.set_analog_raw(ids.TEMPO_FADER, 1500)
        self.assertAlmostEqual(
            self.rig.layer.state.analog(ids.TEMPO_FADER), 0.5, places=3
        )

    def test_the_centre_position_can_be_set(self) -> None:
        self.rig.store.set_analog(
            ids.TEMPO_FADER, AnalogCalibration(raw_min=0, raw_max=1000)
        )
        self.rig.layer.set_analog_raw(ids.TEMPO_FADER, 400)
        self.assertTrue(self.page.set_center(ids.TEMPO_FADER))
        self.assertAlmostEqual(
            self.rig.store.analog(ids.TEMPO_FADER).center, 400
        )
        self.rig.layer.set_analog_raw(ids.TEMPO_FADER, 400)
        self.assertAlmostEqual(
            self.rig.layer.state.analog(ids.TEMPO_FADER), 0.5, places=3
        )

    def test_a_centre_outside_the_range_is_refused(self) -> None:
        self.rig.store.set_analog(
            ids.TEMPO_FADER, AnalogCalibration(raw_min=100, raw_max=200)
        )
        self.rig.layer.set_analog_raw(ids.TEMPO_FADER, 4000)
        self.assertFalse(self.page.set_center(ids.TEMPO_FADER))

    def test_the_deadzone_steps_and_wraps(self) -> None:
        values = [
            self.page.step_deadzone(ids.TEMPO_FADER) for _ in range(6)
        ]
        self.assertEqual(
            [round(v, 2) for v in values], [0.01, 0.02, 0.03, 0.04, 0.05, 0.0]
        )

    def test_resetting_brings_back_the_control_defaults(self) -> None:
        self.page.start_measuring(ids.TEMPO_FADER)
        self._move_fader(100, 900)
        self.page.finish_measuring(ids.TEMPO_FADER)
        self.page.apply("reset", ids.TEMPO_FADER)
        self.assertFalse(self.rig.store.is_measured(ids.TEMPO_FADER))

    def test_saving_writes_the_file(self) -> None:
        self.page.start_measuring(ids.TEMPO_FADER)
        self._move_fader(100, 900)
        self.page.finish_measuring(ids.TEMPO_FADER)
        self.assertTrue(self.page.save())
        self.assertTrue(self.path.exists())

        loaded = CalibrationStore.load(self.path)
        self.assertTrue(loaded.is_measured(ids.TEMPO_FADER))
        self.assertEqual(loaded.analog(ids.TEMPO_FADER).raw_min, 100)

    def test_saving_keeps_the_touch_thresholds(self) -> None:
        self.rig.scanner.touch.set_thresholds(820, 780)
        self.assertTrue(self.page.save())
        loaded = CalibrationStore.load(self.path)
        self.assertAlmostEqual(loaded.touch.on_threshold, 820)
        self.assertAlmostEqual(loaded.touch.off_threshold, 780)

    def test_both_tabs_can_be_drawn(self) -> None:
        for tab in ("TOUCH / JOG", "FADER / POTI"):
            self.page._show_tab(tab)
            self.rig.app.update()
            self.assertEqual(self.page.tab, tab)


@unittest.skipUnless(TK_AVAILABLE, "keine Tk-Anzeige verfuegbar")
class SettingsPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.rig = Rig(Path(self.directory.name) / "calibration.json")
        self.page = self.rig.mode(ApplicationMode.SETTINGS)

    def tearDown(self) -> None:
        self.rig.close()
        self.directory.cleanup()

    def test_the_page_shows_the_real_display_state(self) -> None:
        self.page.refresh()
        texts = [
            self.page.canvas.itemcget(item, "text")
            for item in self.page.canvas.find_all()
            if self.page.canvas.type(item) == "text"
        ]
        self.assertIn("EINSTELLUNGEN", texts)
        self.assertIn(self.rig.window.screen.display.time_mode.value, texts)

    def test_a_toggle_goes_through_the_normal_command_path(self) -> None:
        before = self.rig.window.screen.display.time_mode
        hit = next(
            h for h in self.page._hits if h[4] is CommandType.TIME_MODE
        )

        class Click:
            x = (hit[0] + hit[2]) / 2
            y = (hit[1] + hit[3]) / 2

        self.page._on_click(Click())
        self.assertIsNot(self.rig.window.screen.display.time_mode, before)

    def test_the_page_offers_a_way_back_without_a_keyboard(self) -> None:
        self.page.refresh()
        x0, y0, x1, y1 = self.page._menu_hit

        class Click:
            x = (x0 + x1) / 2
            y = (y0 + y1) / 2

        self.page._on_click(Click())
        self.assertIs(self.rig.modes.mode, ApplicationMode.MENU)

    def test_quantize_can_be_switched_from_the_settings(self) -> None:
        self.assertFalse(self.rig.deck.state.quantize)
        hit = next(
            h for h in self.page._hits
            if h[4] is CommandType.QUANTIZE_TOGGLE
        )

        class Click:
            x = (hit[0] + hit[2]) / 2
            y = (hit[1] + hit[3]) / 2

        self.page._on_click(Click())
        self.assertTrue(self.rig.deck.state.quantize)


if __name__ == "__main__":
    unittest.main()
