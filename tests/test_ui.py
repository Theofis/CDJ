"""Tests der virtuellen Oberflaeche.

Die Widgets werden direkt bedient (kein echter Mauszeiger), es wird aber ein
echtes Tk-Fenster aufgebaut. Ohne Anzeige werden die Tests uebersprungen.
"""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

try:
    import tkinter as tk

    _root = tk.Tk()
    _root.withdraw()  # nicht anzeigen, damit beim Testen kein Fenster blitzt
    _root.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - kopfloser Rechner
    TK_AVAILABLE = False

from virtual_cdj.core import controls, ids
from virtual_cdj.core.button_customization import ButtonLedColor
from virtual_cdj.core.model import ControlType, EventType
from virtual_cdj.ui import theme


@unittest.skipUnless(TK_AVAILABLE, "keine Tk-Anzeige verfuegbar")
class PanelTests(unittest.TestCase):
    def setUp(self) -> None:
        from virtual_cdj.ui.app import VirtualCdjApp

        self.directory = tempfile.TemporaryDirectory()
        self.app = VirtualCdjApp(
            button_settings_path=Path(self.directory.name) / "buttons.json",
            console_output=lambda _line: None,
        )
        # Das Testfenster bleibt unsichtbar - die Widgets werden trotzdem
        # vollstaendig aufgebaut, weil die Geometrie aus der
        # Komponentenliste kommt und nicht vom Fenstermanager.
        self.app.withdraw()
        self.app.update()
        self.panel = self.app.panel
        self.src = self.app.source
        self.events: list = []
        self.app.input_layer.subscribe(self.events.append)

    def tearDown(self) -> None:
        self.app.destroy()
        self.directory.cleanup()

    # -- Aufbau ------------------------------------------------------------

    def test_every_control_is_reachable_in_the_panel(self) -> None:
        missing = [
            control_id
            for control_id in controls.CONTROLS
            if control_id not in self.panel._widget_by_id
        ]
        self.assertEqual(missing, [])

    def test_hit_test_resolves_overlapping_elements(self) -> None:
        # Jog-Platte liegt im Jog-Rand, Browse-Taster im Browse-Drehgeber.
        jog = controls.get(ids.JOG_MOVE)
        widget = self.panel._find(self.panel.tf.x(jog.x), self.panel.tf.y(jog.y))
        self.assertIsNotNone(widget)
        self.assertIn(ids.JOG_TOUCH, widget.control_ids)

        browse = controls.get(ids.BROWSE_ROTATE)
        widget = self.panel._find(
            self.panel.tf.x(browse.x), self.panel.tf.y(browse.y)
        )
        self.assertIsNotNone(widget)
        self.assertIn(ids.BROWSE_PRESS, widget.control_ids)

    # -- Taster ------------------------------------------------------------

    def _widget(self, control_id: str):
        return self.panel._widget_by_id[control_id]

    def _center(self, control_id: str) -> tuple[float, float]:
        widget = self._widget(control_id)
        return widget.cx, widget.cy

    def test_click_gives_one_press_and_one_release(self) -> None:
        widget = self._widget(ids.PLAY)
        x, y = self._center(ids.PLAY)
        widget.on_press(self.src, x, y, 1)
        widget.on_release(self.src, x, y)
        self.assertEqual(
            [(e.control_id, e.event) for e in self.events],
            [
                (ids.PLAY, EventType.PRESS),
                (ids.PLAY, EventType.RELEASE),
            ],
        )

    def test_right_click_latches_and_left_click_unlatches(self) -> None:
        widget = self._widget(ids.BEAT_JUMP_PREV)
        x, y = self._center(ids.BEAT_JUMP_PREV)

        widget.on_press(self.src, x, y, 3)
        self.assertTrue(widget.latched)
        self.assertTrue(self.app.input_layer.state.is_pressed(ids.BEAT_JUMP_PREV))

        widget.on_press(self.src, x, y, 1)
        widget.on_release(self.src, x, y)
        self.assertFalse(widget.latched)
        self.assertFalse(self.app.input_layer.state.is_pressed(ids.BEAT_JUMP_PREV))
        self.assertEqual(
            [e.event for e in self.events],
            [EventType.PRESS, EventType.RELEASE],
        )

    def test_two_latched_buttons_are_held_together(self) -> None:
        for control_id in (ids.DELETE, ids.MEMORY):
            widget = self._widget(control_id)
            x, y = self._center(control_id)
            widget.on_press(self.src, x, y, 3)
        self.assertEqual(
            self.app.input_layer.state.pressed_ids(), (ids.DELETE, ids.MEMORY)
        )

    def test_latch_plus_momentary_click_on_another_button(self) -> None:
        mode = self._widget(ids.BEAT_LOOP_4)
        mx, my = self._center(ids.BEAT_LOOP_4)
        mode.on_press(self.src, mx, my, 3)

        pad = self._widget(ids.PAD_D)
        px, py = self._center(ids.PAD_D)
        pad.on_press(self.src, px, py, 1)
        self.assertEqual(
            self.app.input_layer.state.pressed_ids(),
            (ids.BEAT_LOOP_4, ids.PAD_D),
        )
        pad.on_release(self.src, px, py)
        self.assertEqual(
            self.app.input_layer.state.pressed_ids(), (ids.BEAT_LOOP_4,)
        )

    def test_release_all_latches(self) -> None:
        widget = self._widget(ids.SLIP)
        x, y = self._center(ids.SLIP)
        widget.on_press(self.src, x, y, 3)
        self.panel.release_all_latches()
        self.assertEqual(self.app.input_layer.state.pressed_ids(), ())

    def test_saved_button_name_is_redrawn_in_the_panel(self) -> None:
        self.app.button_customizations.set(ids.PLAY, display_name="Start")
        widget = self._widget(ids.PLAY)
        self.assertEqual(widget.control.label, "Start")
        self.assertEqual(widget.control.short, "Start")
        drawn_text = [
            self.panel.canvas.itemcget(item, "text")
            for item in widget.items
            if self.panel.canvas.type(item) == "text"
        ]
        self.assertIn("Start", drawn_text)

    def test_edit_mode_selects_a_button_without_emitting_an_input(self) -> None:
        selected = []
        self.panel.on_edit = selected.append
        self.panel.set_edit_mode(True)
        widget = self._widget(ids.PLAY)
        self.panel._on_button(
            SimpleNamespace(x=widget.cx, y=widget.cy), 1
        )
        self.assertEqual([control.id for control in selected], [ids.PLAY])
        self.assertEqual(self.events, [])
        self.assertFalse(self.app.input_layer.state.is_pressed(ids.PLAY))

    def test_entering_edit_mode_releases_an_active_button(self) -> None:
        widget = self._widget(ids.CUE)
        self.panel._on_button(
            SimpleNamespace(x=widget.cx, y=widget.cy), 1
        )
        self.assertTrue(self.app.input_layer.state.is_pressed(ids.CUE))
        self.panel.set_edit_mode(True)
        self.assertFalse(self.app.input_layer.state.is_pressed(ids.CUE))

    def test_custom_blue_led_is_drawn_and_lights_while_pressed(self) -> None:
        self.app.button_customizations.set(
            ids.TAG_LIST, led_color=ButtonLedColor.BLUE
        )
        widget = self._widget(ids.TAG_LIST)
        self.assertTrue(widget.control.has_led)
        self.assertIsNotNone(widget._led)
        assert widget._led is not None
        self.assertEqual(
            self.panel.canvas.itemcget(widget._led, "fill"),
            theme.BUTTON_LED_COLORS["BLUE"][1],
        )
        widget.on_press(self.src, widget.cx, widget.cy, 1)
        self.panel.refresh()
        self.assertEqual(
            self.panel.canvas.itemcget(widget._led, "fill"),
            theme.BUTTON_LED_COLORS["BLUE"][0],
        )
        widget.on_release(self.src, widget.cx, widget.cy)

    # -- Fader -------------------------------------------------------------

    def test_fader_drag_produces_values_between_zero_and_one(self) -> None:
        widget = self._widget(ids.TEMPO_FADER)
        widget.on_press(self.src, widget.cx, widget.bottom, 1)
        values = []
        steps = 40
        for i in range(steps + 1):
            y = widget.bottom - (widget.bottom - widget.top) * i / steps
            widget.on_drag(self.src, widget.cx, y)
            values.append(self.app.input_layer.state.analog(ids.TEMPO_FADER))
        self.assertTrue(all(0.0 <= v <= 1.0 for v in values))
        self.assertAlmostEqual(values[0], 0.0, places=2)
        self.assertAlmostEqual(values[-1], 1.0, places=2)
        self.assertEqual(values, sorted(values))

    def test_fader_wheel_changes_value(self) -> None:
        widget = self._widget(ids.TEMPO_FADER)
        before = self.app.input_layer.state.analog(ids.TEMPO_FADER)
        widget.on_wheel(self.src, +1)
        self.assertGreater(
            self.app.input_layer.state.analog(ids.TEMPO_FADER), before
        )

    # -- Encoder -----------------------------------------------------------

    def test_encoder_wheel_right_and_left(self) -> None:
        widget = self._widget(ids.BROWSE_ROTATE)
        widget.on_wheel(self.src, +1)
        widget.on_wheel(self.src, +1)
        widget.on_wheel(self.src, -1)
        deltas = [e.delta for e in self.events if e.event is EventType.ROTATE]
        self.assertEqual(deltas, [1, 1, -1])
        self.assertEqual(
            self.app.input_layer.state.encoder_total(ids.BROWSE_ROTATE), 1
        )

    def _button_events(self) -> list[str]:
        return [
            e.control_id for e in self.events
            if e.event in (EventType.PRESS, EventType.RELEASE)
        ]

    def test_encoder_drag_on_rim_rotates_without_pressing(self) -> None:
        """Am Rand ziehen waehlt aus, ohne zu laden.

        Vorher loeste jeder Linksklick auf dem Regler ``BROWSE_PRESS`` aus -
        Auswaehlen ohne Ausloesen war mit der Maus nicht moeglich.
        """
        widget = self._widget(ids.BROWSE_ROTATE)
        r = widget.w / 2
        widget.on_press(self.src, widget.cx, widget.cy - r, 1)
        # Ein Viertelkreis im Uhrzeigersinn, durchgehend am Rand.
        widget.on_drag(self.src, widget.cx + r, widget.cy)
        widget.on_release(self.src, widget.cx + r, widget.cy)

        rotate = [e for e in self.events if e.event is EventType.ROTATE]
        self.assertTrue(rotate)
        self.assertTrue(all(e.delta > 0 for e in rotate))
        self.assertEqual(self._button_events(), [])

    def test_encoder_click_on_hub_presses(self) -> None:
        """Die Nabe ist der Drucktaster - Drehung und Druck bleiben getrennt."""
        widget = self._widget(ids.BROWSE_ROTATE)
        widget.on_press(self.src, widget.cx, widget.cy, 1)
        widget.on_release(self.src, widget.cx, widget.cy)
        self.assertEqual(
            self._button_events(), [ids.BROWSE_PRESS, ids.BROWSE_PRESS]
        )
        self.assertEqual(
            [e for e in self.events if e.event is EventType.ROTATE], []
        )

    def test_encoder_wheel_never_presses(self) -> None:
        widget = self._widget(ids.BROWSE_ROTATE)
        widget.on_wheel(self.src, +3)
        self.assertEqual(self._button_events(), [])

    # -- Schalter ----------------------------------------------------------

    def test_switch_segments_select_positions(self) -> None:
        widget = self._widget(ids.DIRECTION)
        top = widget.cy - widget.h / 2
        for index, name in enumerate(widget.positions):
            y = top + widget.h * (index + 0.5) / len(widget.positions)
            widget.on_press(self.src, widget.cx, y, 1)
            self.assertEqual(
                self.app.input_layer.state.switch(ids.DIRECTION), name
            )

    # -- Jogwheel ----------------------------------------------------------

    def frame(self) -> list:
        """Einen Bildtakt nachbilden.

        Das Jog-Widget erzeugt keine Ereignisse mehr, sondern sammelt die
        Bewegung in einem Positionszaehler - genau wie das echte Jogwheel.
        Weitergegeben wird sie erst hier.
        """
        return self.src.poll_jog()

    def _jog_drag(
        self, widget, radius: float, degrees: float, steps: int = 12,
        *, frames: bool = True,
    ):
        import math

        for i in range(steps + 1):
            a = math.radians(degrees * i / steps)
            x = widget.cx + radius * math.sin(a)
            y = widget.cy - radius * math.cos(a)
            if i == 0:
                widget.on_press(self.src, x, y, 1)
            else:
                widget.on_drag(self.src, x, y)
            if frames:
                self.frame()
        widget.on_release(self.src, x, y)
        self.frame()

    def test_platter_drag_gives_touch_and_move(self) -> None:
        widget = self._widget(ids.JOG_MOVE)
        self._jog_drag(widget, widget.r_touch * 0.8, 90)

        touch = [e for e in self.events if e.control_id == ids.JOG_TOUCH]
        move = [e for e in self.events if e.control_id == ids.JOG_MOVE]
        self.assertEqual(
            [e.event for e in touch], [EventType.PRESS, EventType.RELEASE]
        )
        self.assertTrue(move)
        self.assertTrue(all(e.delta > 0 for e in move))
        # Waehrend der Bewegung war Touch aktiv.
        self.assertTrue(all(e.meta["touched"] for e in move))

    def test_rim_drag_gives_move_without_touch(self) -> None:
        widget = self._widget(ids.JOG_MOVE)
        rim = (widget.r_touch + widget.r_outer) / 2
        self._jog_drag(widget, rim, 90)

        self.assertEqual([e for e in self.events if e.control_id == ids.JOG_TOUCH], [])
        move = [e for e in self.events if e.control_id == ids.JOG_MOVE]
        self.assertTrue(move)
        self.assertTrue(all(not e.meta["touched"] for e in move))

    def test_jog_direction_both_ways(self) -> None:
        widget = self._widget(ids.JOG_MOVE)
        self._jog_drag(widget, widget.r_touch * 0.8, +120)
        forward = self.app.input_layer.state.jog(ids.JOG_MOVE).total
        self.assertGreater(forward, 0)

        self.events.clear()
        self._jog_drag(widget, widget.r_touch * 0.8, -120)
        move = [e for e in self.events if e.control_id == ids.JOG_MOVE]
        self.assertTrue(all(e.delta < 0 for e in move))
        self.assertLess(self.app.input_layer.state.jog(ids.JOG_MOVE).total, forward)

    def test_jog_tick_count_matches_the_rotation(self) -> None:
        widget = self._widget(ids.JOG_MOVE)
        ticks_per_rev = controls.get(ids.JOG_MOVE).ticks_per_rev
        self._jog_drag(widget, widget.r_touch * 0.8, 180, steps=60)
        total = self.app.input_layer.state.jog(ids.JOG_MOVE).total
        self.assertAlmostEqual(total, ticks_per_rev / 2, delta=3)

    def test_jog_wheel_notch_gives_fast_movement(self) -> None:
        widget = self._widget(ids.JOG_MOVE)
        widget.on_wheel(self.src, -1, boost=5)
        self.frame()
        move = [e for e in self.events if e.control_id == ids.JOG_MOVE]
        self.assertEqual(len(move), 1)
        self.assertEqual(move[0].delta, -widget._wheel_ticks * 5)

    def test_touch_held_while_moving_keeps_both_states(self) -> None:
        widget = self._widget(ids.JOG_MOVE)
        widget.on_press(self.src, widget.cx, widget.cy - widget.r_touch * 0.5, 1)
        self.frame()
        self.assertTrue(self.app.input_layer.state.is_pressed(ids.JOG_TOUCH))
        widget.on_drag(self.src, widget.cx + widget.r_touch * 0.5, widget.cy)
        self.frame()
        self.assertTrue(self.app.input_layer.state.is_pressed(ids.JOG_TOUCH))
        self.assertNotEqual(self.app.input_layer.state.jog(ids.JOG_MOVE).total, 0)

    # -- Anforderungen an die Jog-Eingabe ----------------------------------

    def test_touching_without_moving_reports_no_movement(self) -> None:
        """Beruehren heisst nicht bewegen (Abschnitt 4)."""
        widget = self._widget(ids.JOG_MOVE)
        widget.on_press(self.src, widget.cx, widget.cy - widget.r_touch * 0.5, 1)
        self.frame()
        movement = self.src.jog.poll()
        self.assertTrue(self.app.input_layer.state.is_pressed(ids.JOG_TOUCH))
        self.assertEqual(movement.steps, 0)
        self.assertEqual(movement.direction, 0)
        self.assertEqual(movement.speed, 0.0)

    def test_a_fast_drag_gives_one_event_per_frame(self) -> None:
        """Abschnitt 9: keine Warteschlange aus Teilbewegungen.

        Frueher erzeugte jede Mausbewegung ein eigenes Ereignis - bei einer
        schnellen Drehung ueber ein Bild waren das Dutzende, waehrend das
        echte Jogwheel genau eines liefert.
        """
        widget = self._widget(ids.JOG_MOVE)
        self._jog_drag(widget, widget.r_touch * 0.8, 180, steps=60, frames=False)
        move = [e for e in self.events if e.control_id == ids.JOG_MOVE]
        self.assertEqual(len(move), 1, "mehr als ein Bewegungsereignis je Bild")
        # Und die gesamte Bewegung ist trotzdem vollstaendig angekommen.
        ticks_per_rev = controls.get(ids.JOG_MOVE).ticks_per_rev
        self.assertAlmostEqual(move[0].delta, ticks_per_rev / 2, delta=3)

    def test_many_revolutions_never_wrap(self) -> None:
        """Abschnitt 18: beliebig viele volle Umdrehungen, keine Spruenge."""
        widget = self._widget(ids.JOG_MOVE)
        ticks_per_rev = controls.get(ids.JOG_MOVE).ticks_per_rev
        for _ in range(3):
            self._jog_drag(widget, widget.r_touch * 0.8, 360, steps=72)
        total = self.app.input_layer.state.jog(ids.JOG_MOVE).total
        self.assertAlmostEqual(total, 3 * ticks_per_rev, delta=12)
        self.assertGreater(widget.accumulated_angle, 1000.0)
        # Die Einzelschritte bleiben klein - kein 359-nach-0-Sprung.
        move = [e for e in self.events if e.control_id == ids.JOG_MOVE]
        self.assertTrue(all(abs(e.delta) < ticks_per_rev / 4 for e in move))

    def test_a_backspin_is_fast_and_negative(self) -> None:
        """Abschnitt 16: hoher Betrag, negative Richtung, Touch an."""
        widget = self._widget(ids.JOG_MOVE)
        widget.on_press(self.src, widget.cx, widget.cy - widget.r_touch * 0.8, 1)
        self.frame()
        self._spin(widget, -720, steps=8)
        movement = self.src.jog.poll()
        self.assertLess(movement.steps, 0)
        self.assertEqual(movement.direction, -1)
        self.assertTrue(movement.touch)
        widget.on_release(self.src, widget.cx, widget.cy)
        self.frame()
        self.assertFalse(self.app.input_layer.state.is_pressed(ids.JOG_TOUCH))

    def _spin(self, widget, degrees: float, steps: int) -> None:
        import math

        radius = widget.r_touch * 0.8
        for i in range(1, steps + 1):
            a = math.radians(degrees * i / steps)
            widget.on_drag(
                self.src,
                widget.cx + radius * math.sin(a),
                widget.cy - radius * math.cos(a),
            )

    def test_releasing_outside_the_jog_clears_the_touch(self) -> None:
        """Abschnitt 17: die Eingabe darf nicht haengen bleiben."""
        widget = self._widget(ids.JOG_MOVE)
        widget.on_press(self.src, widget.cx, widget.cy - widget.r_touch * 0.5, 1)
        self.frame()
        self.assertTrue(self.app.input_layer.state.is_pressed(ids.JOG_TOUCH))
        # Loslassen weit ausserhalb des Bedienfelds.
        widget.on_release(self.src, widget.cx + 5000, widget.cy + 5000)
        self.frame()
        self.assertFalse(self.app.input_layer.state.is_pressed(ids.JOG_TOUCH))

    def test_cancel_clears_the_touch(self) -> None:
        widget = self._widget(ids.JOG_MOVE)
        widget.on_press(self.src, widget.cx, widget.cy, 1)
        self.frame()
        widget.cancel(self.src)
        self.frame()
        self.assertFalse(self.app.input_layer.state.is_pressed(ids.JOG_TOUCH))

    def test_the_visual_angle_is_not_the_data_source(self) -> None:
        """Abschnitt 10/21: die Darstellung steuert nichts.

        Der sichtbare Winkel laeuft bei 360 Grad um, die Jog-Position nicht.
        Wuerde die Anzeige die Quelle sein, ginge bei jeder Umdrehung
        Bewegung verloren.
        """
        widget = self._widget(ids.JOG_MOVE)
        self._jog_drag(widget, widget.r_touch * 0.8, 350, steps=70)
        self._jog_drag(widget, widget.r_touch * 0.8, 350, steps=70)
        self.assertLess(widget.visual_angle, 360.0)
        self.assertGreater(widget.accumulated_angle, 600.0)
        self.assertGreater(self.src.jog.position, 1000)
        widget.on_release(self.src, widget.cx + widget.r_touch * 0.5, widget.cy)
        self.assertFalse(self.app.input_layer.state.is_pressed(ids.JOG_TOUCH))

    # -- LED ---------------------------------------------------------------

    def test_led_test_button_toggles_all_leds(self) -> None:
        self.app._led_test()
        self.assertTrue(
            all(
                self.app.input_layer.state.led(cid)
                for cid in controls.LED_CONTROLS
            )
        )
        self.app._led_test()
        self.assertFalse(
            any(
                self.app.input_layer.state.led(cid)
                for cid in controls.LED_CONTROLS
            )
        )

    # -- Debug / Monitor ---------------------------------------------------

    def test_debug_and_monitor_receive_events(self) -> None:
        widget = self._widget(ids.CUE)
        x, y = self._center(ids.CUE)
        widget.on_press(self.src, x, y, 1)
        self.app.update()
        self.assertEqual(self.app.debug._values["control_id"].get(), ids.CUE)
        self.assertEqual(self.app.debug._values["event"].get(), "PRESS")
        self.assertEqual(self.app.debug._values["source"].get(), "VIRTUAL")
        self.assertEqual(self.app.debug._values["value"].get(), "1")
        self.assertGreaterEqual(len(self.app.monitor._events), 1)
        text = self.app.monitor.text.get("1.0", "end")
        self.assertIn(ids.CUE, text)
        self.assertIn("PRESS", text)

    def test_monitor_line_format(self) -> None:
        from virtual_cdj.ui.monitor_view import format_line

        event = self.app.input_layer.press(ids.PLAY)
        assert event is not None
        line = format_line(event)
        self.assertRegex(line, r"^\d{2}:\d{2}:\d{2}\.\d{3} V PLAY\s+PRESS$")

    def test_hover_shows_id_type_and_hardware(self) -> None:
        self.app._on_hover(controls.get(ids.TEMPO_FADER))
        text = self.app.hover_var.get()
        self.assertIn(ids.TEMPO_FADER, text)
        self.assertIn(ControlType.ANALOG_FADER.value, text)
        self.assertIn("nicht zugeordnet", text)


@unittest.skipUnless(TK_AVAILABLE, "keine Tk-Anzeige verfuegbar")
class JogCalibrationTests(unittest.TestCase):
    """Kalibrierungsoberflaeche des Jog-Sensors.

    Die Ansicht wertet nichts selbst aus - sie zeigt, was das
    Scan-Programm liefert, und gibt den Grenzwert dorthin weiter.
    """

    def setUp(self) -> None:
        from virtual_cdj.demo.jog import SimulatedJog
        from virtual_cdj.jog import JogScanner
        from virtual_cdj.ui.jog_calibration import JogCalibrationApp

        self.jog = SimulatedJog()
        self.scanner = JogScanner(touch_on=700, touch_off=650)
        self.app = JogCalibrationApp(self.scanner, demo=self.jog)
        self.app.withdraw()
        self.view = self.app.view
        # Der Takt laeuft im Test nicht mit - die Abtastung wird von Hand
        # ausgeloest, damit jeder Test weiss, was gemessen wurde.
        self.view.stop()
        self.app.update()

    def tearDown(self) -> None:
        self.app.destroy()

    def _sample(self, revolutions: float = 0.0) -> None:
        for a, b, raw in self.jog.burst(revolutions):
            self.scanner.scan(a, b, touch_raw=raw)
        self.view.refresh()

    def test_the_thresholds_of_the_scanner_are_shown(self) -> None:
        self.assertEqual(self.view.on_entry.get(), "700")
        self.assertEqual(self.view.off_entry.get(), "650")

    def test_a_typed_threshold_reaches_the_sensor(self) -> None:
        self.view.on_entry.delete(0, "end")
        self.view.on_entry.insert(0, "820")
        self.view.off_entry.delete(0, "end")
        self.view.off_entry.insert(0, "780")
        self.assertTrue(self.view.apply_thresholds())
        self.assertAlmostEqual(self.scanner.touch.on_threshold, 820)
        self.assertAlmostEqual(self.scanner.touch.off_threshold, 780)

    def test_a_nonsense_threshold_is_refused_with_a_message(self) -> None:
        self.view.on_entry.delete(0, "end")
        self.view.on_entry.insert(0, "abc")
        self.assertFalse(self.view.apply_thresholds())
        self.assertIn("Zahl", self.view.status.cget("text"))
        self.assertAlmostEqual(self.scanner.touch.on_threshold, 700)

    def test_an_inverted_pair_is_refused(self) -> None:
        self.view.off_entry.delete(0, "end")
        self.view.off_entry.insert(0, "900")
        self.assertFalse(self.view.apply_thresholds())
        self.assertAlmostEqual(self.scanner.touch.off_threshold, 650)

    def test_a_suggestion_needs_measured_values(self) -> None:
        self.assertFalse(self.view.suggest_thresholds())
        self.assertIn("beruehren", self.view.status.cget("text"))

    def test_noise_alone_gives_no_suggestion(self) -> None:
        # Nur Ruhewerte: der Unterschied zu einer Beruehrung fehlt.
        self._sample(0.05)
        self.assertFalse(self.view.suggest_thresholds())
        self.assertAlmostEqual(self.scanner.touch.on_threshold, 700)

    def test_a_suggestion_separates_rest_from_touch(self) -> None:
        self._sample(0.05)
        self.jog.touch(True)
        self._sample(0.05)
        self.assertTrue(self.view.suggest_thresholds())
        sensor = self.scanner.touch
        self.assertGreater(sensor.on_threshold, sensor.off_threshold)
        # Mit dem eingemessenen Grenzwert wird die Beruehrung erkannt.
        self.jog.touch(True)
        self._sample(0.01)
        self.assertTrue(self.scanner.state.touch)
        self.jog.touch(False)
        self._sample(0.01)
        self.assertFalse(self.scanner.state.touch)

    def test_sampling_feeds_the_scan_program(self) -> None:
        self.app.speed.set(1.0)
        count = self.view.sample()
        self.assertGreater(count, 0)
        self.assertGreater(self.scanner.state.position, 0)
        self.assertEqual(self.scanner.state.errors, 0)

    def test_resetting_clears_the_measurement_not_the_state(self) -> None:
        self._sample(0.02)
        self.assertGreater(self.scanner.touch.stats.samples, 0)
        self.view.reset_stats()
        self.assertEqual(self.scanner.touch.stats.samples, 0)

    def test_the_chart_draws_the_history(self) -> None:
        self._sample(0.05)
        self.view.refresh()
        self.assertGreater(len(self.view.chart.find_all()), 1)

    def test_without_values_the_chart_says_so(self) -> None:
        self.view.refresh()
        texts = [
            self.view.chart.itemcget(item, "text")
            for item in self.view.chart.find_all()
            if self.view.chart.type(item) == "text"
        ]
        self.assertTrue(any("keine Messwerte" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
