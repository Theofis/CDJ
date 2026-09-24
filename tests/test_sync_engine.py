"""MasterManager und noch audiofreie Sync-Zielberechnung."""

from __future__ import annotations

from dataclasses import replace
import unittest

from virtual_cdj.deck.state import DeckState, TrackInfo
from virtual_cdj.prolink.models import BeatEvent, PlayerState
from virtual_cdj.sync import MasterManager, MasterState, SyncEngine


def remote(**changes) -> PlayerState:
    values = dict(
        player_id=1,
        online=True,
        last_update_ns=123,
        playing=True,
        track_id="remote-track",
        position_ms=1000.0,
        position_valid=True,
        original_bpm=150.0,
        effective_bpm=154.0,
        beat_number=10,
        beat_in_bar=2,
        is_master=True,
        timing_source="test",
    )
    values.update(changes)
    return PlayerState(**values)


def local(**changes) -> DeckState:
    values = dict(
        deck_id=3,
        track=TrackInfo(track_id="local-track", original_bpm=153.95),
        original_bpm=153.95,
        current_bpm=153.95,
        beat_phase=0.0,
        sync=True,
    )
    values.update(changes)
    return DeckState(**values)


class TestMasterManager(unittest.TestCase):
    def test_local_master(self) -> None:
        manager = MasterManager()
        manager.update_local(local(is_master=True), observed_ns=10)
        state = manager.get_master(now_ns=10)
        assert state is not None
        assert state.source_type == "local"
        assert state.player_id == 3

    def test_remote_master(self) -> None:
        manager = MasterManager()
        manager.update_remote(remote(), received_ns=10)
        state = manager.get_master(now_ns=10)
        assert state is not None
        assert state.source_type == "prolink"
        assert state.bpm == 154.0

    def test_remote_master_takes_precedence_then_falls_back_to_local(self) -> None:
        manager = MasterManager()
        manager.update_local(local(is_master=True), observed_ns=10)
        manager.update_remote(remote(), received_ns=20)
        assert manager.get_master(now_ns=20).source_type == "prolink"
        manager.update_remote(remote(online=False, is_master=False), received_ns=30)
        assert manager.get_master(now_ns=30).source_type == "local"

    def test_master_disappears_when_offline(self) -> None:
        manager = MasterManager()
        manager.update_remote(remote(), received_ns=10)
        manager.update_remote(remote(online=False), received_ns=20)
        assert manager.get_master(now_ns=20) is None

    def test_stale_remote_master_is_ignored(self) -> None:
        manager = MasterManager(stale_after_s=1.0)
        manager.update_remote(remote(), received_ns=0)
        assert manager.get_master(now_ns=1_000_000_001) is None

    def test_beat_event_advances_phase_from_monotonic_timestamp(self) -> None:
        manager = MasterManager()
        manager.update_remote(remote(effective_bpm=120.0), received_ns=0)
        manager.update_beat(BeatEvent(1, 0, 5, 1, 120.0, 0.0, True), received_ns=0)
        state = manager.get_master(now_ns=125_000_000)
        assert state is not None
        self.assertAlmostEqual(state.phase, 0.25)
        assert state.beat_number == 5


class TestSyncEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SyncEngine()
        self.master = MasterState(
            source_type="prolink", player_id=1, bpm=154.0, phase=0.0
        )

    def test_sync_disabled(self) -> None:
        target = self.engine.target_for(local(sync=False), self.master)
        assert not target.enabled
        assert target.target_bpm is None

    def test_no_master(self) -> None:
        target = self.engine.target_for(local(), None)
        assert target.enabled
        assert target.master_player_id is None
        assert not target.synchronized

    def test_bpm_difference_creates_target_and_correction(self) -> None:
        target = self.engine.target_for(local(current_bpm=150.0), self.master)
        assert target.target_bpm == 154.0
        self.assertAlmostEqual(target.beat_period_ms, 60_000 / 154.0)
        assert target.correction > 0

    def test_positive_phase_error(self) -> None:
        master = replace(self.master, phase=0.30)
        target = self.engine.target_for(local(current_bpm=154.0, beat_phase=0.10), master)
        self.assertAlmostEqual(target.phase_error_beats, 0.20)

    def test_negative_phase_error(self) -> None:
        master = replace(self.master, phase=0.31)
        target = self.engine.target_for(local(current_bpm=154.0, beat_phase=0.77), master)
        self.assertAlmostEqual(target.phase_error_beats, -0.46)

    def test_exact_sync(self) -> None:
        master = replace(self.master, phase=0.25)
        target = self.engine.target_for(local(current_bpm=154.0, beat_phase=0.25), master)
        assert target.phase_error_beats == 0.0
        self.assertAlmostEqual(target.correction, 0.0)
        assert target.synchronized
