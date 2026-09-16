"""Tests der Eingabequellen.

Der Kernpunkt: virtuelle Quelle und Hardware-Quelle erzeugen fuer dasselbe
Bedienelement dieselben Ereignisse - nur ``source`` unterscheidet sich.
"""

from __future__ import annotations

import unittest

from virtual_cdj.core import ids
from virtual_cdj.core.input_layer import InputLayer
from virtual_cdj.core.model import Direction, EventType, Source
from virtual_cdj.sources.hardware import HardwareSource, ProtocolError
from virtual_cdj.sources.virtual import VirtualSource


class VirtualSourceTests(unittest.TestCase):
    def test_virtual_source_marks_events_as_virtual(self) -> None:
        layer = InputLayer()
        src = VirtualSource(layer)
        event = src.press(ids.PLAY)
        assert event is not None
        self.assertIs(event.source, Source.VIRTUAL)


class HardwareSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = InputLayer()
        self.events: list = []
        self.layer.subscribe(self.events.append)
        self.hw = HardwareSource(self.layer)

    def test_digital_frame(self) -> None:
        self.hw.feed("D PLAY 1\nD PLAY 0\n")
        self.assertEqual(
            [(e.control_id, e.event.value, e.source.value) for e in self.events],
            [
                (ids.PLAY, "PRESS", "HARDWARE"),
                (ids.PLAY, "RELEASE", "HARDWARE"),
            ],
        )

    def test_analog_frame_is_normalised(self) -> None:
        self.hw.feed_line("A TEMPO_FADER 4095")
        event = self.events[-1]
        self.assertIs(event.event, EventType.VALUE)
        self.assertAlmostEqual(event.value, 1.0)
        self.assertEqual(event.raw, 4095.0)

    def test_encoder_and_jog_frames(self) -> None:
        self.hw.feed("E BROWSE_ROTATE -1\nJ JOG_MOVE 3\n")
        rotate, move = self.events
        self.assertEqual(rotate.delta, -1)
        self.assertIs(rotate.direction, Direction.CCW)
        self.assertEqual(move.delta, 3)
        self.assertIs(move.direction, Direction.CW)

    def test_touch_and_switch_frames(self) -> None:
        self.hw.feed("T JOG_TOUCH 1\nS DIRECTION REV\n")
        touch, switch = self.events
        self.assertIs(touch.event, EventType.PRESS)
        self.assertEqual(switch.position, "REV")

    def test_comments_and_blank_lines_are_ignored(self) -> None:
        self.assertEqual(self.hw.feed("# Kommentar\n\n   \n"), [])

    def test_malformed_frame_raises(self) -> None:
        with self.assertRaises(ProtocolError):
            self.hw.feed_line("D PLAY")
        with self.assertRaises(ProtocolError):
            self.hw.feed_line("X PLAY 1")
        with self.assertRaises(ProtocolError):
            self.hw.feed_line("D PLAY maybe")


class SourceEquivalenceTests(unittest.TestCase):
    """Mausklick und Hardware-Taster muessen identisch ankommen."""

    def test_same_event_from_both_sources(self) -> None:
        virtual_layer = InputLayer()
        virtual_events: list = []
        virtual_layer.subscribe(virtual_events.append)
        VirtualSource(virtual_layer).press(ids.PLAY)

        hardware_layer = InputLayer()
        hardware_events: list = []
        hardware_layer.subscribe(hardware_events.append)
        HardwareSource(hardware_layer).feed_line("D PLAY 1")

        v, h = virtual_events[0], hardware_events[0]
        self.assertEqual(v.control_id, h.control_id)
        self.assertEqual(v.control_type, h.control_type)
        self.assertEqual(v.event, h.event)
        self.assertEqual(v.value, h.value)
        self.assertIs(v.source, Source.VIRTUAL)
        self.assertIs(h.source, Source.HARDWARE)

    def test_both_sources_share_one_input_layer(self) -> None:
        layer = InputLayer()
        virtual = VirtualSource(layer)
        hardware = HardwareSource(layer)

        virtual.press(ids.TAG_TRACK_REMOVE)
        hardware.feed_line("D PAD_A 1")

        # Eine Kombination aus virtueller und echter Taste bleibt erhalten.
        self.assertEqual(
            layer.state.pressed_ids(), (ids.PAD_A, ids.TAG_TRACK_REMOVE)
        )


if __name__ == "__main__":
    unittest.main()
