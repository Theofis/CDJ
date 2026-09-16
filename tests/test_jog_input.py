"""Jog-Eingabe: eine Schnittstelle fuer Maus, Touchscreen und ESP32.

Geprueft wird die Kette, die die Vorgabe verlangt::

    Zeiger / Sensor -> JogInput (Positionszaehler) -> JogMovement
                    -> InputLayer -> InputMapper -> Deck

und vor allem, dass **beide** Quellen dieselbe Form liefern: gleiche
Ereigniszahl, gleiche Felder, gleiche Bedeutung.

Die Testfaelle aus Abschnitt 23 der Vorgabe stehen unten in
``ScenarioTests``, mit der Nummer im Namen.
"""

from __future__ import annotations

import math
import unittest

from virtual_cdj.core import controls, ids
from virtual_cdj.core.input_layer import InputLayer
from virtual_cdj.core.model import EventType
from virtual_cdj.deck.commands import CommandType
from virtual_cdj.deck.engine import Deck
from virtual_cdj.deck.mapping import InputMapper
from virtual_cdj.deck.state import BeatGrid, JogMode, PlayState, TrackInfo
from virtual_cdj.jog import STEPS_PER_REV, JogScanner
from virtual_cdj.sources.hardware import HardwareSource
from virtual_cdj.sources.jog_input import (
    HardwareJogInput,
    JogInput,
    VirtualJogInput,
)
from virtual_cdj.sources.virtual import VirtualSource

TICKS_PER_REV = controls.get(ids.JOG_MOVE).ticks_per_rev


class Clock:
    """Steuerbare Uhr - Geschwindigkeiten sollen nachrechenbar sein."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_track() -> TrackInfo:
    return TrackInfo(
        track_id="t", title="T", duration_s=300.0, original_bpm=120.0,
        beat_grid=BeatGrid(first_beat_s=0.0, bpm=120.0),
    )


# ----------------------------------------------------------------------
# Das Datenmodell
# ----------------------------------------------------------------------


class JogMovementTests(unittest.TestCase):
    """``JogMovement`` ist der ``JogInputState`` der Vorgabe."""

    def setUp(self) -> None:
        self.clock = Clock()
        self.jog = VirtualJogInput(time_source=self.clock)
        self.jog.sync()

    def move(self, steps: int, seconds: float = 0.1):
        self.jog.rotate(steps)
        self.clock.advance(seconds)
        return self.jog.poll()

    def test_the_four_values_of_the_contract(self) -> None:
        movement = self.move(400, 0.5)
        self.assertTrue(hasattr(movement, "touch"))       # touched
        self.assertEqual(movement.steps, 400)             # deltaPosition
        self.assertEqual(movement.direction, +1)          # direction
        self.assertAlmostEqual(movement.speed, 800.0)     # speed

    def test_direction_forward_backward_and_still(self) -> None:
        self.assertEqual(self.move(+10).direction, +1)
        self.assertEqual(self.move(-10).direction, -1)
        self.assertEqual(self.move(0).direction, 0)

    def test_direction_comes_from_this_movement_not_a_history(self) -> None:
        """Abschnitt 6: nach dem Anhalten ist die Richtung 0, nicht die
        zuletzt gedrehte."""
        self.move(+100)
        self.assertEqual(self.move(0).direction, 0)

    def test_speed_is_steps_per_second(self) -> None:
        self.assertAlmostEqual(self.move(800, 1.0).speed, 800.0)
        self.assertAlmostEqual(self.move(400, 2.0).speed, 200.0)

    def test_speed_in_revolutions_per_second(self) -> None:
        movement = self.move(STEPS_PER_REV * 2, 1.0)
        self.assertAlmostEqual(movement.revolutions_per_second, 2.0)

    def test_a_backspin_is_negative_and_fast(self) -> None:
        movement = self.move(-STEPS_PER_REV * 3, 0.5)
        self.assertEqual(movement.direction, -1)
        self.assertLess(movement.speed, -4000)

    def test_an_implausibly_short_interval_reports_no_speed(self) -> None:
        """Zwei Aufnahmen Mikrosekunden auseinander sagen nichts aus."""
        movement = self.move(10, 0.000001)
        self.assertEqual(movement.steps, 10)
        self.assertEqual(movement.speed, 0.0)

    def test_touching_without_moving(self) -> None:
        """Abschnitt 4: Beruehren ist nicht Bewegen."""
        self.jog.set_touch(True)
        self.clock.advance(0.1)
        movement = self.jog.poll()
        self.assertTrue(movement.touch)
        self.assertEqual(movement.steps, 0)
        self.assertEqual(movement.direction, 0)
        self.assertEqual(movement.speed, 0.0)


# ----------------------------------------------------------------------
# Beide Quellen, eine Form
# ----------------------------------------------------------------------


class SameShapeTests(unittest.TestCase):
    """Virtuelle und Hardware-Quelle liefern dasselbe."""

    def setUp(self) -> None:
        self.layer = InputLayer()
        self.events: list = []
        self.layer.subscribe(self.events.append)

        self.virtual = VirtualSource(self.layer)
        self.scanner = JogScanner()
        self.hardware = HardwareSource(self.layer, mapping={}, jog=self.scanner)
        self.virtual.poll_jog()
        self.hardware.poll_jog()
        self.events.clear()

    def moves(self) -> list:
        return [
            e for e in self.events
            if e.control_id == ids.JOG_MOVE and e.event is EventType.MOVE
        ]

    def test_both_are_jog_inputs(self) -> None:
        self.assertIsInstance(self.virtual.jog, JogInput)
        self.assertIsInstance(self.hardware.jog_input, JogInput)
        self.assertIsInstance(self.virtual.jog, VirtualJogInput)
        self.assertIsInstance(self.hardware.jog_input, HardwareJogInput)

    def test_a_burst_becomes_one_event_on_both_paths(self) -> None:
        """Abschnitt 9: keine Warteschlange - auf keinem der beiden Wege.

        Gemessen vor dem Umbau: 120 Ereignisse virtuell gegen 1 in der
        Hardware. Jede Mausbewegung wurde einzeln abgearbeitet.
        """
        for _ in range(120):
            self.virtual.jog_rotate(4)
        self.virtual.poll_jog()
        virtual_moves = self.moves()

        self.events.clear()
        self.scanner.set_position(480)
        self.hardware.poll_jog()
        hardware_moves = self.moves()

        self.assertEqual(len(virtual_moves), 1)
        self.assertEqual(len(hardware_moves), 1)
        self.assertEqual(virtual_moves[0].delta, 480)
        self.assertEqual(hardware_moves[0].delta, 480)

    def test_both_report_the_resolution(self) -> None:
        self.virtual.jog_rotate(10)
        self.virtual.poll_jog()
        self.scanner.set_position(10)
        self.hardware.poll_jog()
        for event in self.moves():
            self.assertEqual(event.meta["ticks_per_rev"], STEPS_PER_REV)

    def test_nothing_is_sent_without_movement(self) -> None:
        self.assertEqual(self.virtual.poll_jog(), [])
        self.assertEqual(self.hardware.poll_jog(), [])

    def test_touch_is_reported_before_the_movement(self) -> None:
        self.virtual.jog_set_touch(True)
        self.virtual.jog_rotate(20)
        events = self.virtual.poll_jog()
        self.assertEqual(
            [e.control_id for e in events], [ids.JOG_TOUCH, ids.JOG_MOVE]
        )
        self.assertTrue(events[1].meta["touched"])

    def test_the_position_counter_never_wraps(self) -> None:
        """Abschnitt 18: beliebig viele Umdrehungen."""
        for _ in range(5):
            self.virtual.jog_rotate(STEPS_PER_REV)
            self.virtual.poll_jog()
        self.assertEqual(self.virtual.jog.position, 5 * STEPS_PER_REV)

    def test_slow_reading_loses_no_steps(self) -> None:
        """Wer seltener liest, bekommt alles - nur in einem Stueck."""
        for _ in range(50):
            self.virtual.jog_rotate(7)
        events = self.virtual.poll_jog()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].delta, 350)


# ----------------------------------------------------------------------
# Die Testfaelle aus Abschnitt 23
# ----------------------------------------------------------------------


class ScenarioTests(unittest.TestCase):
    """Zeiger -> Jog -> Deck, ueber die echte Kommandokette."""

    def setUp(self) -> None:
        self.clock = Clock()
        self.layer = InputLayer()
        self.deck = Deck(1, time_source=self.clock)
        self.deck.load_track(make_track())
        self.commands: list = []

        def sink(cmd):
            self.commands.append(cmd)
            self.deck.execute(cmd)

        self.mapper = InputMapper(1, sink)
        self.layer.subscribe(self.mapper.handle_event)
        self.source = VirtualSource(self.layer)
        self.source.jog = VirtualJogInput(time_source=self.clock)
        self.widget = self._jog_widget()
        self.frame()

    def _jog_widget(self):
        """Das echte Jog-Widget des Bedienfelds, ohne Fenster."""
        from virtual_cdj.ui.theme import TRANSFORM
        from virtual_cdj.ui.widgets import JogWidget

        jog = controls.get(ids.JOG_MOVE)
        touch = controls.get(ids.JOG_TOUCH)
        return JogWidget(jog, TRANSFORM, touch)

    # -- Hilfen --------------------------------------------------------

    def frame(self, seconds: float = 0.016) -> None:
        """Ein Bild: Zeit vergeht, gesammelte Bewegung geht weiter."""
        self.clock.advance(seconds)
        self.source.poll_jog()

    def point(self, radius: float, degrees: float) -> tuple[float, float]:
        a = math.radians(degrees)
        return (
            self.widget.cx + radius * math.sin(a),
            self.widget.cy - radius * math.cos(a),
        )

    def turn(self, radius: float, degrees: float, steps: int = 12) -> None:
        for i in range(1, steps + 1):
            self.widget.on_drag(self.source, *self.point(radius, degrees * i / steps))
        self.frame()

    def grab(self, radius: float) -> None:
        self.widget.on_press(self.source, *self.point(radius, 0.0), 1)
        self.frame()

    def let_go(self) -> None:
        self.widget.on_release(self.source, *self.point(self.rim, 0.0))
        self.frame()

    @property
    def platter(self) -> float:
        return self.widget.r_touch * 0.8

    @property
    def rim(self) -> float:
        return (self.widget.r_touch + self.widget.r_outer) / 2

    def jog_commands(self) -> list:
        return [c for c in self.commands if c.type is CommandType.JOG_MOVE]

    def play(self) -> None:
        from virtual_cdj.deck.commands import command

        self.deck.execute(command(CommandType.PLAY_PAUSE, 1))

    # -- TEST 1 --------------------------------------------------------

    def test_1_outer_ring_bends_without_touch(self) -> None:
        self.play()
        self.assertIs(self.deck.state.jog_mode, JogMode.VINYL)
        self.grab(self.rim)
        self.turn(self.rim, 60)
        self.assertFalse(self.deck.state.jog_touch)
        self.assertTrue(self.jog_commands())
        self.let_go()
        self.assertFalse(self.deck.state.jog_touch)

    # -- TEST 2 --------------------------------------------------------

    def test_2_pressing_the_platter_sets_touch(self) -> None:
        self.play()
        self.grab(self.platter)
        self.assertTrue(self.deck.state.jog_touch)

    # -- TEST 3 --------------------------------------------------------

    def test_3_holding_and_turning_moves_the_track(self) -> None:
        self.play()
        self.grab(self.platter)
        before = self.deck.state.position_s
        self.turn(self.platter, 180)
        self.assertTrue(self.deck.state.jog_touch)
        self.assertNotAlmostEqual(self.deck.state.position_s, before)
        # Eine halbe Umdrehung ist ein halber Beat - bei 120 BPM 0.25 s.
        moved = self.deck.state.position_s - before
        self.assertAlmostEqual(moved, 0.25, delta=0.03)

    # -- TEST 4 --------------------------------------------------------

    def test_4_releasing_clears_the_touch(self) -> None:
        self.play()
        self.grab(self.platter)
        self.turn(self.platter, 90)
        self.let_go()
        self.assertFalse(self.deck.state.jog_touch)
        self.assertIs(self.deck.state.play_state, PlayState.PLAYING)

    # -- TEST 5 --------------------------------------------------------

    def test_5_cdj_mode_bends_instead_of_scratching(self) -> None:
        from virtual_cdj.deck.commands import command

        self.deck.execute(command(CommandType.JOG_MODE_TOGGLE, 1))
        self.assertIs(self.deck.state.jog_mode, JogMode.CDJ)
        self.play()
        self.grab(self.platter)
        before = self.deck.state.position_s
        self.turn(self.platter, 180)
        moved = abs(self.deck.state.position_s - before)
        # Vinyl + Touch waere Scratch: eine halbe Umdrehung = ein halber
        # Beat = 0.25 s. Im CDJ-Modus greift stattdessen der Pitch Bend,
        # der deutlich weniger verschiebt.
        self.assertTrue(self.deck.state.jog_touch)
        self.assertLess(moved, 0.25 * 0.75)
        self.assertGreater(moved, 0.0)

    # -- TEST 6 --------------------------------------------------------

    def test_6_frame_search_while_paused(self) -> None:
        self.assertIsNot(self.deck.state.play_state, PlayState.PLAYING)
        self.grab(self.platter)
        before = self.deck.state.position_s
        self.turn(self.platter, 18, steps=6)      # ein Zwanzigstel Beat
        small = self.deck.state.position_s - before
        self.assertGreater(small, 0.0)
        self.assertLess(small, 0.1)

        before = self.deck.state.position_s
        self.turn(self.platter, 360, steps=36)    # ein ganzer Beat
        big = self.deck.state.position_s - before
        self.assertGreater(big, small * 5)

    # -- TEST 7 --------------------------------------------------------

    def test_7_several_full_turns_have_no_jumps(self) -> None:
        """Vier volle Umdrehungen vorwaerts - ohne 359-nach-0-Sprung.

        Ein Sprung am Winkeluebergang wuerde sich als **rueckwaerts**
        gezaehlte Bewegung zeigen: fast eine ganze Umdrehung in die falsche
        Richtung. Die Summe waere dann viel zu klein.
        """
        self.grab(self.platter)
        self.commands.clear()
        for _ in range(4):
            self.turn(self.platter, 360, steps=36)
        total = self.layer.state.jog(ids.JOG_MOVE).total
        self.assertAlmostEqual(total, 4 * TICKS_PER_REV, delta=20)
        deltas = [cmd.get("delta") for cmd in self.jog_commands()]
        self.assertTrue(deltas)
        self.assertTrue(
            all(delta > 0 for delta in deltas),
            f"Rueckwaertssprung am Winkeluebergang: {deltas}",
        )
        self.assertEqual(sum(deltas), total)

    # -- TEST 8 --------------------------------------------------------

    def test_8_a_fast_backspin_is_fast_and_negative(self) -> None:
        self.play()
        self.grab(self.platter)
        self.clock.advance(0.016)
        for i in range(1, 9):
            self.widget.on_drag(self.source, *self.point(self.platter, -720 * i / 8))
        movement = self.source.jog.poll()
        self.assertLess(movement.steps, 0)
        self.assertEqual(movement.direction, -1)
        self.assertTrue(movement.touch)
        self.assertLess(movement.speed, -1000)

    # -- TEST 9 --------------------------------------------------------

    def test_9_releasing_outside_the_jog_clears_the_touch(self) -> None:
        self.grab(self.platter)
        self.assertTrue(self.deck.state.jog_touch)
        self.widget.on_release(self.source, self.widget.cx + 9999, self.widget.cy)
        self.frame()
        self.assertFalse(self.deck.state.jog_touch)

    def test_9b_cancel_clears_the_touch(self) -> None:
        self.grab(self.platter)
        self.widget.cancel(self.source)
        self.frame()
        self.assertFalse(self.deck.state.jog_touch)

    # -- TEST 10 -------------------------------------------------------

    def test_10_a_fast_move_gives_one_command_per_frame(self) -> None:
        self.grab(self.platter)
        self.commands.clear()
        # 60 Zeigerereignisse innerhalb **eines** Bildes.
        for i in range(1, 61):
            self.widget.on_drag(self.source, *self.point(self.platter, 360 * i / 60))
        self.frame()
        self.assertEqual(len(self.jog_commands()), 1)
        self.assertAlmostEqual(
            self.jog_commands()[0].get("delta"), TICKS_PER_REV, delta=8
        )

    # -- TEST 11 -------------------------------------------------------

    def test_11_the_virtual_jog_drives_the_same_deck_state(self) -> None:
        """Dieselbe Jog-Engine wie spaeter die Hardware.

        Beide Wege erzeugen dasselbe Kommando; das Deck bewegt sich gleich
        weit.
        """
        from virtual_cdj.deck.commands import command

        virtual_deck = self.deck
        self.grab(self.platter)
        before = virtual_deck.state.position_s
        self.turn(self.platter, 360, steps=36)
        by_mouse = virtual_deck.state.position_s - before

        hardware_deck = Deck(2, time_source=self.clock)
        hardware_deck.load_track(make_track())
        hardware_deck.execute(
            command(CommandType.JOG_TOUCH, 2, pressed=True)
        )
        start = hardware_deck.state.position_s
        hardware_deck.execute(
            command(
                CommandType.JOG_MOVE, 2,
                delta=TICKS_PER_REV, ticks_per_rev=STEPS_PER_REV,
                touched=True,
            )
        )
        by_hardware = hardware_deck.state.position_s - start

        self.assertAlmostEqual(by_mouse, by_hardware, delta=0.02)

    # -- Abschnitt 21 ---------------------------------------------------

    def test_the_gui_never_touches_the_audio_position_itself(self) -> None:
        """Jede Bewegung geht als Kommando durch die Kette."""
        self.grab(self.platter)
        self.commands.clear()
        self.turn(self.platter, 90)
        self.assertTrue(
            self.jog_commands(),
            "die Bewegung muss als DeckCommand ankommen",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
