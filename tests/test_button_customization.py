"""Persistente Anpassung von Buttonnamen und Konsolenausgaben."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from virtual_cdj.core import controls, ids
from virtual_cdj.core.button_customization import (
    ButtonCustomizationStore,
    ButtonLedColor,
)
from virtual_cdj.core.input_layer import InputLayer


class ButtonCustomizationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "buttons.json"
        self.output: list[str] = []
        self.store = ButtonCustomizationStore(
            self.path, output=self.output.append
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_name_and_output_survive_a_restart(self) -> None:
        self.store.set(
            ids.PLAY,
            display_name="Start",
            console_output="PLAYER STARTET",
            led_color=ButtonLedColor.ORANGE,
        )
        restored = ButtonCustomizationStore(self.path)
        entry = restored.get(ids.PLAY)
        self.assertEqual(entry.display_name, "Start")
        self.assertEqual(entry.console_output, "PLAYER STARTET")
        self.assertEqual(entry.led_color, ButtonLedColor.ORANGE.value)

    def test_display_copy_keeps_the_technical_identity_and_geometry(self) -> None:
        original = controls.get(ids.PLAY)
        self.store.set(ids.PLAY, display_name="Start")
        displayed = self.store.display_control(original)
        self.assertEqual(displayed.id, ids.PLAY)
        self.assertEqual(displayed.short, "Start")
        self.assertEqual(displayed.label, "Start")
        self.assertEqual((displayed.x, displayed.y), (original.x, original.y))
        self.assertIs(displayed.type, original.type)

    def test_custom_output_is_emitted_once_on_press_only(self) -> None:
        self.store.set(ids.CUE, console_output="MEIN CUE")
        layer = InputLayer()
        layer.subscribe(self.store.emit)
        layer.press(ids.CUE)
        layer.release(ids.CUE)
        self.assertEqual(self.output, ["MEIN CUE"])

    def test_empty_values_restore_the_defaults(self) -> None:
        self.store.set(ids.PLAY, display_name="Start", console_output="LOS")
        self.store.set(ids.PLAY, display_name="", console_output="")
        self.assertEqual(
            self.store.display_name(controls.get(ids.PLAY)),
            controls.get(ids.PLAY).short or controls.get(ids.PLAY).label,
        )
        self.assertIsNone(self.store.console_output(ids.PLAY))
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn(ids.PLAY, saved["buttons"])

    def test_non_button_controls_cannot_be_customized(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set(ids.TEMPO_FADER, display_name="Tempo")

    def test_led_can_be_removed_from_or_added_to_a_button(self) -> None:
        self.store.set(ids.PLAY, led_color=ButtonLedColor.NONE)
        self.assertFalse(
            self.store.display_control(controls.get(ids.PLAY)).has_led
        )

        self.store.set(ids.TAG_LIST, led_color=ButtonLedColor.BLUE)
        tag = self.store.display_control(controls.get(ids.TAG_LIST))
        self.assertTrue(tag.has_led)
        self.assertIs(
            self.store.led_color(tag), ButtonLedColor.BLUE
        )

    def test_unknown_led_color_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set(ids.PLAY, led_color="VIOLET")

    def test_unknown_and_broken_entries_are_ignored_when_loading(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "buttons": {
                        "DOES_NOT_EXIST": {
                            "display_name": "Falsch",
                            "console_output": "Falsch",
                        },
                        ids.PLAY: {"display_name": 42},
                    }
                }
            ),
            encoding="utf-8",
        )
        restored = ButtonCustomizationStore(self.path)
        self.assertEqual(restored.get(ids.PLAY).display_name, "")
        self.assertEqual(restored.get("DOES_NOT_EXIST").display_name, "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
