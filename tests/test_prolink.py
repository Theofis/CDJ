"""Modelle, Provider-Wireformat und Anwendungsintegration von Phase 1."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from virtual_cdj.app import CdjApplication
from virtual_cdj.deck.mode_manager import OperatingMode
from virtual_cdj.prolink.ipc import MessageError, decode_message, encode_beat_event, encode_player_state
from virtual_cdj.prolink.models import BeatEvent, PlayerState
from virtual_cdj.prolink.provider import NullProLinkProvider


def player(**changes) -> PlayerState:
    values = dict(
        player_id=1,
        online=True,
        last_update_ns=10,
        playing=True,
        track_id="track-a",
        position_ms=12_000.0,
        position_valid=True,
        original_bpm=150.0,
        effective_bpm=154.2,
        pitch_percent=2.8,
        beat_number=193,
        beat_in_bar=1,
        sync_enabled=False,
        is_master=True,
        on_air=True,
        timing_source="test",
    )
    values.update(changes)
    return PlayerState(**values)


class TestModels(unittest.TestCase):
    def test_original_and_effective_bpm_are_independent(self) -> None:
        state = player()
        assert state.original_bpm == 150.0
        assert state.effective_bpm == 154.2
        assert state.original_bpm != state.effective_bpm

    def test_offline_and_missing_position_are_representable(self) -> None:
        state = player(online=False, position_ms=None, position_valid=False)
        assert not state.online
        assert state.position_ms is None
        assert not state.position_valid

    def test_master_and_sync_are_separate_flags(self) -> None:
        state = player(is_master=True, sync_enabled=False)
        assert state.is_master
        assert not state.sync_enabled


class TestWireFormat(unittest.TestCase):
    def test_player_state_round_trip(self) -> None:
        state = player()
        assert decode_message(encode_player_state(state)) == state

    def test_beat_event_round_trip(self) -> None:
        event = BeatEvent(1, 123, 194, 2, 154.2, 12_390.0, True)
        assert decode_message(encode_beat_event(event)) == event

    def test_malformed_message_is_rejected_without_process_failure(self) -> None:
        invalid = [
            b"not json",
            b'{"version":99,"type":"player_state","payload":{}}',
            b'{"version":1,"type":"unknown","payload":{}}',
            b'{"version":1,"type":"player_state","payload":{"player_id":0}}',
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(MessageError):
                decode_message(raw)


class ManualProvider(NullProLinkProvider):
    def __init__(self) -> None:
        self.players: dict[int, PlayerState] = {}
        self.states = []
        self.beats = []
        self.pending = []

    def get_players(self) -> tuple[PlayerState, ...]:
        return tuple(self.players.values())

    def subscribe_player_state(self, listener):
        self.states.append(listener)
        return lambda: self.states.remove(listener) if listener in self.states else None

    def subscribe_beat_events(self, listener):
        self.beats.append(listener)
        return lambda: self.beats.remove(listener) if listener in self.beats else None

    def emit(self, state: PlayerState) -> None:
        self.pending.append(state)

    def poll(self) -> None:
        while self.pending:
            state = self.pending.pop(0)
            self.players[state.player_id] = state
            for listener in tuple(self.states):
                listener(state)


class TestApplicationSlice(unittest.TestCase):
    @staticmethod
    def _settings(directory: str) -> Path:
        settings = Path(directory) / "settings.json"
        settings.write_text(
            json.dumps({"operating_mode": "CDJ"}), encoding="utf-8"
        )
        return settings

    def test_remote_master_reaches_existing_master_view(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = ManualProvider()
            app = CdjApplication(
                [3], start_audio=False, settings_path=self._settings(directory),
                prolink_provider=provider,
                operating_mode=OperatingMode.CDJ,
            )
            try:
                provider.emit(player())
                app.tick()
                master = app.master_state()
                view = app.master_view(3)
                self.assertIsNotNone(master)
                self.assertEqual(master.source_type, "prolink")
                self.assertEqual(master.player_id, 1)
                self.assertEqual(master.bpm, 154.2)
                self.assertIsNotNone(view)
                self.assertEqual(view.source_type, "prolink")
                self.assertEqual(view.player_id, 1)
                self.assertEqual(view.title, "track-a")
                self.assertEqual(view.bpm, 154.2)
                self.assertFalse(view.has_waveform)
            finally:
                app.close()

    def test_remote_player_loss_removes_master_without_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = ManualProvider()
            app = CdjApplication(
                [3], start_audio=False, settings_path=self._settings(directory),
                prolink_provider=provider,
                operating_mode=OperatingMode.CDJ,
            )
            try:
                provider.emit(player())
                app.tick()
                self.assertIsNotNone(app.master_state())
                provider.emit(
                    replace(
                        player(), online=False, playing=False,
                        position_valid=False, is_master=False,
                    )
                )
                app.tick()
                self.assertIsNone(app.master_state())
            finally:
                app.close()

    def test_provider_callbacks_are_delivered_by_polling_thread(self) -> None:
        provider = ManualProvider()
        called_on: list[int] = []
        provider.subscribe_player_state(lambda _state: called_on.append(threading.get_ident()))
        provider.emit(player())
        polling_thread = threading.get_ident()
        provider.poll()
        self.assertEqual(called_on, [polling_thread])
