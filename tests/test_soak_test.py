"""Harness contracts; real audio/GUI fault injection is a separate CLI check."""
from dataclasses import replace
from functools import partial
from collections import Counter, deque
import json
import math
import queue
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.soak_test.scenarios import Planner, WEIGHTS, STRESS, EXTREME
from tools.soak_test.worker import Worker
from tools.soak_test.safety import compare, inside, inventory, require_read_only
from tools.soak_test.supervisor import classify, write_json
from tools.soak_test.validation import HealthMonitor, authorize, snapshot, validate
from virtual_cdj.deck.commands import CommandType as C, command as deck_command
from virtual_cdj.deck.engine import Deck
from virtual_cdj.deck.state import BeatGrid, HotCue, TrackInfo

command = partial(deck_command, deck_id=1)


def deck():
    result = Deck(1, time_source=lambda: 0)
    result.load_track(TrackInfo(track_id="test", title="Test", duration_s=120,
        original_bpm=120, beat_grid=BeatGrid(bpm=120), hot_cues=(HotCue(index=0, position_s=8, color="#00ff00"),)))
    return result


class PlannerTests(unittest.TestCase):
    def test_seed_sequence_and_json_checkpoint(self):
        a, b = Planner(84), Planner(84)
        self.assertEqual([a.next() for _ in range(100)], [b.next() for _ in range(100)])
        c = Planner(9)
        c.restore(json.loads(json.dumps(a.checkpoint())))
        self.assertEqual([a.next() for _ in range(100)], [c.next() for _ in range(100)])

    def test_every_track_once_per_bag(self):
        p = Planner(2)
        tracks = ["a", "b", "c", "d"]
        for _ in range(20):
            self.assertEqual(set(tracks), {p.choose_track(tracks) for _ in tracks})

    def test_mixed_planned_time_ratio(self):
        p = Planner(3)
        totals = dict.fromkeys(("REALISTIC", "STRESS", "EXTREME"), 0)
        for _ in range(30000):
            action = p.next()
            totals[action["phase"]] += action["delay"]
        for name, fraction in (("REALISTIC", .7), ("STRESS", .2), ("EXTREME", .1)):
            self.assertAlmostEqual(totals[name] / sum(totals.values()), fraction, delta=.025)

    def test_invalid_weights(self):
        for weights in ({}, {"CUE": 0}, {"CUE": -1}, {"CUE": math.nan}, {"UNKNOWN": 1}):
            with self.assertRaises(ValueError):
                Planner(1, weights=weights)


class ValidatorTests(unittest.TestCase):
    def test_valid_state_and_serialization(self):
        d = deck()
        self.assertEqual(validate(d.state, loaded=True), [])
        self.assertEqual(json.loads(json.dumps(snapshot(d.state)))["track"]["track_id"], "test")

    def test_nan_and_invalid_position(self):
        for position in (math.nan, math.inf, -1, 121):
            self.assertTrue(validate(replace(deck().state, position_s=position)))

    def test_real_loop_stay_and_exit(self):
        d = deck()
        d.execute(command(C.BEAT_LOOP, beats=4))
        for kind in (C.LOOP_HALVE, C.LOOP_DOUBLE, C.QUANTIZE_TOGGLE, C.JOG_MODE_TOGGLE, C.PLAY_PAUSE, C.RELOOP_EXIT):
            before = d.state
            cmd = command(kind)
            d.execute(cmd)
            self.assertEqual(validate(d.state, before, cmd), [], kind)
        self.assertFalse(d.state.loop.active)

    def test_lost_loop_detected(self):
        d = deck()
        d.execute(command(C.BEAT_LOOP, beats=4))
        before = d.state
        invalid = replace(before, loop=replace(before.loop, active=False))
        self.assertTrue(any("STAY" in e for e in validate(invalid, before, command(C.LOOP_HALVE))))

    def test_load_must_clear_loop(self):
        d = deck()
        d.execute(command(C.BEAT_LOOP, beats=4))
        self.assertTrue(validate(d.state, loaded=True))

    def test_forbid_empty_hotcue_and_persistent_commands(self):
        s = deck().state
        authorize(command(C.PAD, index=0, pressed=True), s)
        for cmd in (command(C.PAD, index=1, pressed=True), command(C.DELETE), command(C.MEMORY), command(C.BEATGRID_SHIFT)):
            with self.assertRaises(PermissionError):
                authorize(cmd, s)

    def test_changed_existing_hotcue_detected(self):
        before = deck().state
        after = replace(before, track=replace(before.track, hot_cues=()))
        self.assertIn("existing hot cues changed", validate(after, before, command(C.PLAY_PAUSE)))

    def test_nan_failure_package_is_writable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            write_json(path, {"nan": math.nan, "inf": math.inf})
            self.assertEqual(json.loads(path.read_text())["nan"], "nan")

    def test_seek_matrix_and_hotcue_outside_loop(self):
        d = deck()
        d.execute(command(C.BEAT_LOOP, beats=4))
        for cmd in (command(C.SEEK, position_s=1), command(C.PAD, index=0, pressed=True)):
            before = d.state
            d.execute(cmd)
            self.assertEqual(validate(d.state, before, cmd), [])
        self.assertFalse(d.state.loop.active)

    def test_all_generated_actions_are_known_and_authorized(self):
        worker = object.__new__(Worker)
        worker.planner = Planner(1)
        worker.tracks = {"test": "demo://test"}
        d = deck()
        worker.state = lambda: d.state
        names = set(WEIGHTS) | {name for seq in STRESS + EXTREME for name in seq}
        for name in names:
            for kind, params, delay, scenario in worker.expand({"name": name, "choice": 2, "value": .5}):
                cmd = command(C(kind), **params)
                authorize(cmd, d.state)
                self.assertGreaterEqual(delay, 0)


class ApplicationHookTests(unittest.TestCase):
    def test_load_request_and_failed_result_observers(self):
        from virtual_cdj.app import CdjApplication
        from virtual_cdj.audio.worker import LoadRequest, LoadResult
        from virtual_cdj.deck.mode_manager import OperatingMode
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = CdjApplication(start_audio=False, demo=False, usb=False,
                settings_path=root / "settings.json", button_settings_path=root / "buttons.json",
                cache_directory=root / "cache", backend_output=lambda _: None,
                operating_mode=OperatingMode.CDJ)
            try:
                requests, results = [], []
                app.on_load_requested.append(lambda deck_id, path: requests.append((deck_id, path)))
                app.on_load_finished.append(results.append)
                with patch.object(app.worker, "request"):
                    app.load_track(1, root / "missing.wav")
                self.assertEqual(requests, [(1, str(root / "missing.wav"))])
                failure = LoadResult(LoadRequest(1, "missing"), error="decoder failure")
                with patch.object(app.worker, "drain", return_value=[failure]):
                    app.poll_worker()
                self.assertEqual(results, [failure])
                missing = app.load_track_now(1, root / "missing.wav")
                self.assertFalse(missing.ok)
                self.assertIs(results[-1], missing)
            finally:
                app.close()

    def test_failed_track_schedules_next_load(self):
        from virtual_cdj.audio.worker import LoadRequest, LoadResult
        worker = object.__new__(Worker)
        worker.counters = Counter()
        worker.events = queue.Queue()
        worker.pending = deque([("CUE", {}, .1, "old session")])
        worker.loading = True
        worker.expand = lambda _: [("LOAD", {"track_id": "next"}, .1, "LOAD")]
        worker.load_finished(LoadResult(LoadRequest(1, "broken"), error="bad audio"))
        self.assertFalse(worker.loading)
        self.assertEqual(worker.counters["track_load_failures"], 1)
        self.assertEqual(worker.pending[0][0], "LOAD")
        self.assertEqual(worker.events.get_nowait()["kind"], "load_failure")


class HealthTests(unittest.TestCase):
    def sample(self, now=0, callbacks=1, **state):
        return {"gui_heartbeat": now, "runner_heartbeat": now, "audio": {"callbacks": callbacks},
                "voice_position": 4, "loop_wraps": 0, "loading": False,
                "state": {"playing": True, "position_s": 4, "duration_s": 100, **state}}

    def test_separate_heartbeats(self):
        for field, expected in (("gui_heartbeat", "GUI_HANG"), ("runner_heartbeat", "RUNNER_HANG")):
            monitor = HealthMonitor(0)
            sample = self.sample(11)
            sample[field] = 0
            self.assertEqual(monitor.inspect(sample, 11), expected)

    def test_audio_freeze(self):
        monitor = HealthMonitor(0)
        monitor.inspect(self.sample(), 0)
        self.assertEqual(monitor.inspect(self.sample(11), 11), "AUDIO_HANG")

    def test_playback_stall(self):
        monitor = HealthMonitor(0)
        monitor.inspect(self.sample(), 0)
        self.assertEqual(monitor.inspect(self.sample(11, 2), 11), "PLAYBACK_STALL")

    def test_legitimate_holds_and_end(self):
        for state in ({"playing": False}, {"jog_touch": True, "jog_mode": "VINYL"},
                      {"position_s": 100}, {"tempo_percent": -100}):
            monitor = HealthMonitor(0)
            monitor.inspect(self.sample(**state), 0)
            self.assertIsNone(monitor.inspect(self.sample(11, 2, **state), 11))

    def test_forward_start_is_not_legitimate_hold(self):
        monitor = HealthMonitor(0)
        monitor.inspect(self.sample(position_s=0), 0)
        self.assertEqual(monitor.inspect(self.sample(11, 2, position_s=0), 11), "PLAYBACK_STALL")

    def test_loop_wrap_is_progress(self):
        monitor = HealthMonitor(0)
        monitor.inspect(self.sample(), 0)
        sample = self.sample(11, 2)
        sample["loop_wraps"] = 100
        self.assertIsNone(monitor.inspect(sample, 11))

    def test_failure_classification(self):
        self.assertEqual(classify({"violations": ["write"]}), "USB_WRITE_ATTEMPT")
        self.assertEqual(classify({"errors": ["nonfinite"]}), "INVALID_STATE")


class SafetyTests(unittest.TestCase):
    def test_hash_inventory_and_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").write_bytes(b"old")
            before = inventory(root)
            (root / "a").write_bytes(b"new")
            (root / "b").write_bytes(b"new")
            diff = compare(before, inventory(root))
            self.assertEqual(diff, {"added": ["b"], "removed": [], "changed": ["a"]})

    def test_containment_not_string_prefix(self):
        self.assertFalse(inside(Path.cwd() / "source-other", Path.cwd() / "source"))

    @unittest.skipUnless(sys.platform == "win32", "Windows disk evidence")
    def test_refuse_writable_disk(self):
        with patch("tools.soak_test.safety.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, '{"IsReadOnly": false}', '')
            with self.assertRaisesRegex(RuntimeError, "NOT OS read-only"):
                require_read_only(Path.cwd())

    def test_audit_guard_isolated_in_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = """
import sys
from pathlib import Path
from tools.soak_test.safety import install_write_guard
root = Path(sys.argv[1]); violations = []
install_write_guard(root, violations)
try:
    (root / 'blocked.txt').write_text('must not exist')
except PermissionError:
    assert violations
else:
    raise AssertionError('write was not blocked')
"""
            subprocess.run([sys.executable, "-c", script, tmp], check=True, capture_output=True, timeout=20)
            self.assertFalse((Path(tmp) / "blocked.txt").exists())


if __name__ == "__main__":
    unittest.main()
