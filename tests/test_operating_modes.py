"""Betriebsmodus, Mock-Backends und gemeinsame Controller-Kette."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from virtual_cdj.core import ids
from virtual_cdj.core.input_layer import InputLayer
from virtual_cdj.deck import (
    CdjBackend,
    Deck,
    DeckController,
    InputMapper,
    MidiBackend,
    ModeManager,
    OperatingMode,
    TrackInfo,
)
from virtual_cdj.shell import InputRouter, ModeController
from virtual_cdj.shell import ApplicationMode


class OperatingModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Path(self.directory.name) / "settings.json"
        self.output: list[str] = []
        self.cdj_deck = Deck(1)
        self.midi_deck = Deck(1)
        self.midi = MidiBackend({1: self.midi_deck}, output=self.output.append)
        self.cdj = CdjBackend({1: self.cdj_deck}, output=self.output.append)
        self.manager = ModeManager(
            self.midi, self.cdj, settings_path=self.settings
        )
        self.controller = DeckController(1, self.manager)

    def tearDown(self) -> None:
        self.controller.close()
        self.manager.close()
        self.directory.cleanup()

    def test_default_mode_routes_play_to_cdj(self) -> None:
        self.controller.play()
        self.assertEqual(self.output[-1], "[CDJ] PLAY")
        self.assertIs(self.manager.current_mode, OperatingMode.CDJ)

    def test_switch_routes_play_to_midi(self) -> None:
        self.assertTrue(self.manager.set_mode(OperatingMode.MIDI))
        self.controller.play()
        self.assertEqual(self.output[-1], "[MIDI] PLAY")
        self.assertEqual(self.cdj.lifecycle, ["START", "STOP"])
        self.assertEqual(self.midi.lifecycle, ["START"])

    def test_jog_payload_is_forwarded_without_losing_fields(self) -> None:
        self.manager.set_mode(OperatingMode.MIDI)
        self.controller.jog(4, 0.72, "CW", True)
        self.assertEqual(
            self.output[-1],
            "[MIDI] JOG delta=4 velocity=0.72 direction=CW touched=true",
        )

    def test_tempo_and_hotcue_have_readable_mock_output(self) -> None:
        self.controller.set_tempo(0.57)
        self.assertEqual(self.output[-1], "[CDJ] TEMPO +1.4 %")
        self.controller.set_hotcue(0)
        self.assertEqual(self.output[-1], "[CDJ] HOTCUE A")

    def test_mode_is_persisted_and_loaded_on_the_next_start(self) -> None:
        self.assertTrue(self.manager.set_mode(OperatingMode.MIDI))
        self.controller.close()
        self.manager.close()

        midi = MidiBackend({1: Deck(1)}, output=lambda _line: None)
        cdj = CdjBackend({1: Deck(1)}, output=lambda _line: None)
        restored = ModeManager(midi, cdj, settings_path=self.settings)
        try:
            self.assertIs(restored.current_mode, OperatingMode.MIDI)
            self.assertTrue(midi.running)
            self.assertFalse(cdj.running)
        finally:
            restored.close()
        # tearDown darf dieselben idempotenten close-Aufrufe wiederholen.
        self.controller = DeckController(1, self.manager)

    def test_persistence_keeps_unrelated_settings(self) -> None:
        self.settings.write_text(
            json.dumps({"display_brightness": 80}), encoding="utf-8"
        )
        self.manager.set_mode(OperatingMode.MIDI)
        saved = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(saved["display_brightness"], 80)
        self.assertEqual(saved["operating_mode"], "MIDI")

    def test_switch_is_blocked_while_a_track_is_playing(self) -> None:
        self.cdj_deck.load_track(
            TrackInfo(track_id="track", title="Track", duration_s=60.0)
        )
        self.controller.play()
        self.assertTrue(self.controller.get_state().is_playing)

        self.assertFalse(self.manager.set_mode(OperatingMode.MIDI))
        self.assertIs(self.manager.current_mode, OperatingMode.CDJ)
        self.assertIn("Wiedergabe", self.manager.last_error)
        self.assertFalse(self.midi.running)

    def test_controller_rebinds_state_and_listeners_on_switch(self) -> None:
        seen = []
        self.controller.subscribe(seen.append)
        self.assertEqual(self.controller.get_state().operating_mode, "CDJ")
        self.manager.set_mode(OperatingMode.MIDI)
        self.assertEqual(self.controller.get_state().operating_mode, "MIDI")
        self.assertEqual(self.controller.get_state().connection_state, "MOCK_READY")
        self.assertEqual(seen[-1].operating_mode, "MIDI")

    def test_hardware_jog_path_ends_at_the_active_backend(self) -> None:
        self.manager.set_mode(OperatingMode.MIDI)
        layer = InputLayer()
        application_modes = ModeController()
        mapper = InputMapper(1, self.controller.send)
        router = InputRouter(application_modes, performance_sink=mapper.handle_event)
        layer.subscribe(router.handle_event)

        layer.jog_touch(ids.JOG_TOUCH, True)
        layer.jog_move(ids.JOG_MOVE, 4, timestamp=1000.0)

        self.assertTrue(self.output[-1].startswith("[MIDI] JOG delta=4"))
        self.assertIn("direction=CW", self.output[-1])
        self.assertIn("touched=true", self.output[-1])

    def test_shift_menu_opens_settings_without_sticky_shift(self) -> None:
        from virtual_cdj.app import CdjApplication

        application = CdjApplication(
            [1],
            start_audio=False,
            settings_path=Path(self.directory.name) / "application.json",
            backend_output=lambda _line: None,
            operating_mode=OperatingMode.CDJ,
        )
        try:
            application.virtual_source.press(ids.SHIFT)
            application.virtual_source.press(ids.MENU)
            self.assertIs(application.mode, ApplicationMode.SETTINGS)
            self.assertFalse(application.mappers[1].shift)

            # Die nun gesperrten Release-Flanken duerfen den Zustand nicht
            # wieder in einen falschen Modifier-Zustand bringen.
            application.virtual_source.release(ids.MENU)
            application.virtual_source.release(ids.SHIFT)
            application.modes.to_performance()
            application.virtual_source.press(ids.MENU)
            self.assertIsNot(application.mode, ApplicationMode.SETTINGS)
        finally:
            application.close()

    def test_custom_button_output_replaces_the_backend_console_line(self) -> None:
        from virtual_cdj.app import CdjApplication

        output: list[str] = []
        application = CdjApplication(
            [1],
            start_audio=False,
            settings_path=Path(self.directory.name) / "application.json",
            button_settings_path=Path(self.directory.name) / "buttons.json",
            backend_output=output.append,
            operating_mode=OperatingMode.CDJ,
        )
        try:
            application.button_customizations.set(
                ids.PLAY,
                display_name="Start",
                console_output="MEIN STARTBEFEHL",
            )
            application.virtual_source.press(ids.PLAY)
            application.virtual_source.release(ids.PLAY)
            self.assertEqual(output, ["MEIN STARTBEFEHL"])
            self.assertEqual(
                application.cdj_backend.command_log[-1], "[CDJ] PLAY"
            )
        finally:
            application.close()

    def test_unmapped_button_can_have_a_custom_console_output(self) -> None:
        from virtual_cdj.app import CdjApplication

        output: list[str] = []
        application = CdjApplication(
            [1],
            start_audio=False,
            settings_path=Path(self.directory.name) / "application.json",
            button_settings_path=Path(self.directory.name) / "buttons.json",
            backend_output=output.append,
            operating_mode=OperatingMode.CDJ,
        )
        try:
            application.button_customizations.set(
                ids.TAG_LIST, console_output="TAGLISTE"
            )
            application.virtual_source.press(ids.TAG_LIST)
            application.virtual_source.release(ids.TAG_LIST)
            self.assertEqual(output, ["TAGLISTE"])
        finally:
            application.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
