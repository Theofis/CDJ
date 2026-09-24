"""Monotone Simulatorzeit und echte localhost-IPC-Verbindung."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from virtual_cdj.app import CdjApplication
from virtual_cdj.deck.commands import CommandType, command
from virtual_cdj.deck.mode_manager import OperatingMode
from virtual_cdj.prolink import SimulatorProLinkProvider
from virtual_cdj.simulator import FakePlayer, SimulatorServer, build_scenario


class Clock:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        return self.value

    def advance(self, seconds: float) -> int:
        self.value += int(seconds * 1_000_000_000)
        return self.value


class TestFakePlayer(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.player = FakePlayer(
            original_bpm=120.0,
            effective_bpm=120.0,
            clock_ns=self.clock,
        )

    def test_position_runs_from_monotonic_time(self) -> None:
        self.player.play(now_ns=0)
        state, _events = self.player.advance(now_ns=self.clock.advance(1.0))
        assert state.position_ms == 1000.0

    def test_pause_stops_position(self) -> None:
        self.player.play(now_ns=0)
        self.player.advance(now_ns=self.clock.advance(1.0))
        self.player.pause(now_ns=self.clock.value)
        before = self.player.snapshot().position_ms
        state, events = self.player.advance(now_ns=self.clock.advance(5.0))
        assert state.position_ms == before
        assert events == ()

    def test_beats_count_one_to_four_and_continue(self) -> None:
        self.player.play(now_ns=0)
        numbers = []
        in_bar = []
        for step in range(5):
            _state, events = self.player.advance(now_ns=step * 500_000_000)
            numbers.extend(event.beat_number for event in events)
            in_bar.extend(event.beat_in_bar for event in events)
        assert numbers == [1, 2, 3, 4, 5]
        assert in_bar == [1, 2, 3, 4, 1]

    def test_bpm_change_changes_future_beat_period(self) -> None:
        self.player.play(now_ns=0)
        self.player.advance(now_ns=0)  # Beat 1
        _state, old = self.player.advance(now_ns=500_000_000)
        self.player.set_effective_bpm(240.0, now_ns=500_000_000)
        _state, early = self.player.advance(now_ns=749_000_000)
        _state, changed = self.player.advance(now_ns=750_000_000)
        assert [event.beat_number for event in old] == [2]
        assert early == ()
        assert [event.beat_number for event in changed] == [3]

    def test_disconnect_and_reconnect(self) -> None:
        self.player.set_master(True)
        self.player.set_online(False, now_ns=0)
        assert not self.player.snapshot().online
        assert not self.player.snapshot().is_master
        self.player.set_online(True, now_ns=1)
        assert self.player.snapshot().online

    def test_track_change_resets_position_and_beat(self) -> None:
        self.player.play(now_ns=0)
        self.player.advance(now_ns=self.clock.advance(2.0))
        self.player.set_track("track-b", now_ns=self.clock.value)
        state = self.player.snapshot()
        assert state.track_id == "track-b"
        assert state.position_ms == 0.0
        assert state.beat_number == 1

    def test_bpm_change_scenario_is_deterministic_without_sleep(self) -> None:
        scenario = build_scenario("bpm-change", self.player)
        scenario.start(100.0)
        assert self.player.playing
        assert self.player.effective_bpm == 154.0
        scenario.tick(109.9)
        assert self.player.effective_bpm == 154.0
        scenario.tick(110.0)
        assert self.player.effective_bpm == 156.0


def wait_for(predicate, provider: SimulatorProLinkProvider, timeout: float = 3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        provider.poll()
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("Bedingung nicht rechtzeitig erreicht")


class TestSimulatorIpc(unittest.TestCase):
    @staticmethod
    def _settings(directory: str) -> Path:
        path = Path(directory) / "settings.json"
        path.write_text(json.dumps({"operating_mode": "CDJ"}), encoding="utf-8")
        return path

    def test_connect_state_beats_disconnect_and_reconnect(self) -> None:
        player = FakePlayer(original_bpm=120.0, effective_bpm=240.0)
        player.set_master(True)
        player.play()
        server = SimulatorServer(player, port=0, state_hz=30, tick_hz=100)
        server.start()
        port = server.port
        provider = SimulatorProLinkProvider(port=port, reconnect_s=0.05)
        beats = []
        provider.subscribe_beat_events(beats.append)
        provider.start()
        try:
            state = wait_for(
                lambda: next((p for p in provider.get_players() if p.online), None),
                provider,
            )
            assert state.player_id == 1
            assert state.is_master
            assert state.original_bpm == 120.0
            assert state.effective_bpm == 240.0
            wait_for(lambda: len(beats) >= 2, provider)
            assert beats[-1].beat_number > beats[0].beat_number

            server.stop()
            wait_for(
                lambda: provider.get_players() and not provider.get_players()[0].online,
                provider,
            )

            replacement = SimulatorServer(player, port=port, state_hz=30)
            replacement.start()
            server = replacement
            online = wait_for(
                lambda: provider.get_players() and provider.get_players()[0].online,
                provider,
            )
            assert online
        finally:
            provider.stop()
            server.stop()

    def test_malformed_message_does_not_break_later_messages(self) -> None:
        provider = SimulatorProLinkProvider()
        provider._incoming.put(b"broken")  # noqa: SLF001 - gezielter Adaptertest
        good = FakePlayer().snapshot()
        from virtual_cdj.prolink.ipc import encode_player_state

        provider._incoming.put(encode_player_state(good).rstrip(b"\n"))  # noqa: SLF001
        provider.poll()
        assert provider.get_players() == (good,)

    def test_simulator_ipc_reaches_master_sync_engine_and_gui_state(self) -> None:
        player = FakePlayer(original_bpm=120.0, effective_bpm=128.0)
        player.set_master(True)
        player.play()
        server = SimulatorServer(player, port=0, state_hz=30, tick_hz=100)
        server.start()
        provider = SimulatorProLinkProvider(port=server.port, reconnect_s=0.05)
        with tempfile.TemporaryDirectory() as directory:
            app = CdjApplication(
                [3],
                start_audio=False,
                settings_path=self._settings(directory),
                prolink_provider=provider,
                operating_mode=OperatingMode.CDJ,
            )
            try:
                end = time.monotonic() + 3.0
                master = None
                while time.monotonic() < end:
                    app.tick()
                    master = app.master_state()
                    if master is not None and master.source_type == "prolink":
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(master)
                self.assertEqual(master.player_id, 1)
                self.assertEqual(master.bpm, 128.0)

                view = app.master_view(3)
                self.assertIsNotNone(view)
                self.assertEqual(view.source_type, "prolink")
                self.assertEqual(view.player_id, 1)
                self.assertEqual(view.bpm, 128.0)

                app.decks[3].execute(command(CommandType.SYNC_TOGGLE, 3))
                target = app.sync_target(3)
                self.assertTrue(target.enabled)
                self.assertEqual(target.master_player_id, 1)
                self.assertEqual(target.target_bpm, 128.0)
            finally:
                app.close()
                server.stop()
