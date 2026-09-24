"""Separate process watchdog, evidence collection and final verdict."""
from __future__ import annotations

from collections import Counter, deque
import csv
from datetime import datetime, timezone
import gzip
import json
import multiprocessing as mp
from pathlib import Path
import time
import traceback

from .metrics import Sampler, trend
from .safety import compare, inside, inventory, require_read_only
from .validation import HealthMonitor, json_value
from .worker import worker_main


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(json_value(value), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def classify(sample):
    if sample.get("violations"):
        return "USB_WRITE_ATTEMPT"
    errors = sample.get("errors", [])
    if errors:
        if any("Traceback" in e for e in errors):
            return "EXCEPTION"
        return "INVALID_STATE"
    return None


class Supervisor:
    def __init__(self, config):
        self.config = config
        self.output = Path(config["output"]).resolve()
        self.actions = deque(maxlen=2000)
        self.rows = []
        self.failures = []
        self.counters = Counter()
        self.last_sample = {}
        self.checkpoint = {}
        self.correct_s = 0.0
        self.process_uptime = 0.0
        self.audio_totals = Counter()
        self.callback_max = 0.0
        self.warnings = []
        self.usb_modified = "NOT VERIFIED"
        self.read_only_evidence = None
        self.restarts = 0
        self.cycles = 0
        self.shutdown_observations = []

    def run(self):
        source = Path(self.config["usb_source"]).resolve() if self.config.get("usb_source") else None
        if source and (inside(self.output, source) or inside(self.config["cache"], source)
                       or inside(source, self.output)):
            raise ValueError("Results/cache and source must be disjoint; no output on USB")
        self.output.mkdir(parents=True, exist_ok=False)
        write_json(self.output / "config.json", {k: v for k, v in self.config.items() if k != "replay_actions"})
        before = None
        self.started = time.monotonic()
        self.elapsed = 0.0
        try:
            if source:
                print(f"Verifying OS read-only protection: {source}", flush=True)
                self.read_only_evidence = require_read_only(source)
                write_json(self.output / "read_only.json", self.read_only_evidence)
                print("Hashing USB inventory before playback (all regular files)...", flush=True)
                before = inventory(source)
                write_json(self.output / "usb_before.json", before)
            else:
                self.warnings.append("SELF TEST: synthetic demo tracks; no real USB tested")
                self.usb_modified = "NOT APPLICABLE (SELF TEST)"
            self.started = time.monotonic()
            self.sessions()
        except KeyboardInterrupt:
            self.package("INTERRUPTED", "Supervisor interrupted by user")
        except BaseException:
            self.package("PREFLIGHT_OR_SUPERVISOR_ERROR", traceback.format_exc())
        finally:
            self.elapsed = time.monotonic() - self.started
            if source and before is not None:
                print("Verifying USB after test...", flush=True)
                try:
                    after = inventory(source)
                    write_json(self.output / "usb_after.json", after)
                    changes = compare(before, after)
                    write_json(self.output / "usb_changes.json", changes)
                    self.usb_modified = "YES" if any(changes.values()) else "NO"
                    if self.usb_modified == "YES":
                        self.package("CRITICAL_USB_MODIFIED", json.dumps(changes))
                    if require_read_only(source) != self.read_only_evidence:
                        self.package("USB_IDENTITY_OR_PROTECTION_CHANGED", "Read-only evidence changed")
                except Exception:
                    self.usb_modified = "NOT VERIFIED"
                    self.package("USB_VERIFICATION_FAILED", traceback.format_exc())
            self.report()
        return 1 if self.failures else 0

    def sessions(self):
        ctx = mp.get_context("spawn")
        end = self.started + self.config["hours"] * 3600
        replay = gzip.open(self.output / "replay.jsonl.gz", "at", encoding="utf-8")
        metrics_file = (self.output / "system_metrics.csv").open("w", newline="", encoding="utf-8")
        writer = None
        resume = None
        attempt = 0
        try:
            while time.monotonic() < end:
                attempt += 1
                parent, child = ctx.Pipe(duplex=False)
                stop, dump = ctx.Event(), ctx.Event()
                action_ack = ctx.Event()
                process = ctx.Process(target=worker_main,
                    args=(self.config, child, stop, dump, attempt, resume, action_ack), name=f"cdj-soak-{attempt}")
                process.start()
                child.close()
                began = time.monotonic()
                health = HealthMonitor(began, self.config["watchdog_seconds"])
                sampler = Sampler(process.pid)
                last_message = began
                next_metric = began
                next_checkpoint = began
                next_protection = began + 30
                last_counted_heartbeat = None
                failure = None
                detail = ""
                shutdown = None
                completed = False
                sample = {}
                try:
                    while time.monotonic() < end:
                        now = time.monotonic()
                        # Bound reads so an overactive child cannot starve watchdog checks.
                        for _ in range(100):
                            try:
                                if not parent.poll(.01):
                                    break
                                message = parent.recv()
                            except (EOFError, OSError):
                                break
                            kind = message.get("kind")
                            if kind == "sample":
                                sample = message
                                self.last_sample = sample
                                last_message = now
                            elif kind == "action":
                                self.actions.append(message)
                                replay.write(json.dumps(message, ensure_ascii=False) + "\n")
                                replay.flush()
                                action_ack.set()
                            elif kind == "fatal":
                                failure, detail = "EXCEPTION", message["error"]
                            elif kind == "load_failure":
                                with (self.output / "track_load_failures.jsonl").open("a", encoding="utf-8") as f:
                                    f.write(json.dumps(message) + "\n")
                            elif kind == "shutdown":
                                shutdown = message
                            elif kind in ("cycle_complete", "replay_complete"):
                                completed = True
                        if sample:
                            failure = failure or classify(sample) or health.inspect(sample, now)
                            if sample.get("loading") and not sample.get("ready"):
                                if now - began > self.config["load_timeout"]:
                                    failure = failure or "TRACK_LOAD_TIMEOUT"
                            heartbeat = sample["gui_heartbeat"]
                            healthy = sample.get("ready") and not failure and not sample.get("loading")
                            if healthy and last_counted_heartbeat is not None and heartbeat > last_counted_heartbeat:
                                self.correct_s += min(heartbeat-last_counted_heartbeat, .75)
                            last_counted_heartbeat = heartbeat if healthy else None
                        if now - last_message > (self.config["watchdog_seconds"] if sample else self.config["startup_timeout"]):
                            failure = failure or ("RUNNER_HANG" if sample else "STARTUP_HANG")
                        if not process.is_alive():
                            if not completed or process.exitcode != 0:
                                failure = failure or "CRASH"
                            break
                        if now >= next_metric:
                            row = sampler.sample(now-self.started, attempt)
                            row.update(audio_underruns=sample.get("audio", {}).get("underruns", 0),
                                       decoded_audio_mb=sample.get("audio", {}).get("memory_mb", 0),
                                       actions_total=sample.get("counters", {}).get("actions_total", 0),
                                       tracks_loaded=sample.get("counters", {}).get("tracks_loaded", 0),
                                       gui_fps=sample.get("gui_fps"),
                                       callback_average_ms=sample.get("audio", {}).get("average_callback_ms", 0),
                                       callback_max_ms=sample.get("audio", {}).get("peak_callback_ms", 0))
                            self.rows.append(row)
                            if writer is None:
                                writer = csv.DictWriter(metrics_file, fieldnames=list(row))
                                writer.writeheader()
                            writer.writerow(row)
                            metrics_file.flush()
                            next_metric = now + 5
                            if row["ram_mb"] > self.config["max_rss_mb"]:
                                failure = failure or "CRITICAL_RESOURCE_LEAK"
                        if now >= next_checkpoint and sample:
                            self.checkpoint = {"timestamp": datetime.now(timezone.utc).isoformat(),
                                "elapsed_time": now-self.started, "seed": self.config["seed"],
                                "attempt": attempt, **sample, "system": self.rows[-1] if self.rows else {}}
                            write_json(self.output / "checkpoint.json", self.checkpoint)
                            next_checkpoint = now + 30
                        if now >= next_protection and self.config.get("usb_source"):
                            evidence = require_read_only(Path(self.config["usb_source"]))
                            if evidence != self.read_only_evidence:
                                failure = "USB_IDENTITY_OR_PROTECTION_CHANGED"
                            next_protection = now + 30
                        if failure or completed:
                            break
                        time.sleep(.05)
                finally:
                    dump.set()
                    time.sleep(.2)
                    if failure:
                        self.package(failure, detail or json.dumps(sample.get("errors", [])), attempt)
                    stop.set()
                    # Drain while joining: a full pipe must not itself prevent clean exit.
                    closing_deadline = time.monotonic() + 4
                    while process.is_alive() and time.monotonic() < closing_deadline:
                        for _ in range(100):
                            try:
                                if not parent.poll():
                                    break
                                remaining = parent.recv()
                            except (EOFError, OSError):
                                break
                            if remaining.get("kind") == "shutdown":
                                shutdown = remaining
                            elif remaining.get("kind") == "action":
                                self.actions.append(remaining)
                                replay.write(json.dumps(remaining) + "\n")
                            elif remaining.get("kind") == "sample":
                                sample = remaining
                        process.join(timeout=.05)
                    if process.is_alive():
                        if not failure:
                            failure = "SHUTDOWN_HANG"
                            self.package(failure, "Cooperative shutdown exceeded four seconds", attempt)
                        process.terminate()
                        process.join(timeout=3)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=2)
                    while True:
                        try:
                            if not parent.poll():
                                break
                            remaining = parent.recv()
                        except (EOFError, OSError):
                            break
                        if remaining.get("kind") == "shutdown":
                            shutdown = remaining
                        elif remaining.get("kind") == "action":
                            self.actions.append(remaining)
                            replay.write(json.dumps(remaining) + "\n")
                    if shutdown and shutdown.get("final_sample"):
                        sample = shutdown["final_sample"]
                        self.last_sample = sample
                        if not failure and classify(sample):
                            failure = classify(sample)
                            self.package(failure, json.dumps(sample.get("errors", [])), attempt)
                    # Aggregate only once, using final state where graceful exit allows it.
                    if sample:
                        resume = {"planner": sample["planner"], "action_number": max(sample["action_number"],
                            self.actions[-1]["action_number"] if self.actions else 0)}
                        self.counters.update(sample.get("counters", {}))
                        audio = sample.get("audio", {})
                        self.audio_totals.update({k: audio.get(k, 0) for k in ("underruns", "overruns", "callbacks")})
                        self.callback_max = max(self.callback_max, audio.get("peak_callback_ms", 0))
                    self.process_uptime += time.monotonic() - began
                    parent.close()
                    if not process.is_alive():
                        process.close()
                    # Re-copy stack dump after graceful/hard stop, now stable.
                    if failure and self.failures:
                        self.copy_diagnostics(Path(self.failures[-1]["path"]), attempt)
                    if not failure and (shutdown is None or shutdown.get("threads_remaining") or shutdown.get("audio_running")):
                        failure = "UNCLEAN_SHUTDOWN"
                        self.package(failure, repr(shutdown), attempt)
                    self.shutdown_observations.append({"attempt": attempt, "observation":
                        {k: v for k, v in shutdown.items() if k != "final_sample"} if shutdown else None})
                if completed and not failure:
                    self.cycles += 1
                    if self.config["replay"] or self.cycles >= self.config["lifecycle_cycles"]:
                        break
                if failure:
                    if failure.startswith("USB") or not self.config["continue_on_failure"]:
                        break
                    if self.restarts >= self.config["max_restarts"] or time.monotonic() >= end:
                        break
                    self.restarts += 1
                    print(f"{failure}; recovering in fresh CDJ process ({self.restarts})", flush=True)
                elif not completed:
                    break
        finally:
            replay.close()
            metrics_file.close()
        if self.counters["tracks_loaded"] == 0 or self.audio_totals["callbacks"] == 0:
            self.package("NO_VALID_COVERAGE", "No successfully loaded track and/or real audio callbacks")
        if self.config["lifecycle_cycles"] and self.cycles < self.config["lifecycle_cycles"]:
            self.package("INCOMPLETE_LIFECYCLE_TEST", "Time budget ended before requested startup/shutdown cycles")
        if self.config["replay"] and not completed:
            self.package("INCOMPLETE_REPLAY", "Replay did not reach the requested last action")

    def copy_diagnostics(self, folder, attempt):
        origin = self.output / f"attempt_{attempt:04d}"
        for name in ("stacktraces.txt", "exception.txt"):
            path = origin / name
            if path.exists():
                (folder / name).write_bytes(path.read_bytes())

    def package(self, kind, detail, attempt=0):
        folder = self.output / f"failure_{len(self.failures)+1:04d}"
        folder.mkdir(exist_ok=True)
        entry = {"type": kind, "path": str(folder), "attempt": attempt,
                 "action_number": self.actions[-1]["action_number"] if self.actions else 0}
        self.failures.append(entry)
        (folder / "summary.txt").write_text(json.dumps(entry, indent=2)+"\n"+detail, encoding="utf-8")
        (folder / "seed.txt").write_text(str(self.config["seed"]), encoding="utf-8")
        (folder / "actions.log").write_text("\n".join(json.dumps(row) for row in self.actions), encoding="utf-8")
        write_json(folder / "state.json", self.last_sample.get("state", {}))
        write_json(folder / "checkpoint.json", {**self.checkpoint, **self.last_sample})
        write_json(folder / "audio_metrics.json", self.last_sample.get("audio", {}))
        (folder / "exception.txt").write_text(detail, encoding="utf-8")
        (folder / "stacktraces.txt").write_text("No child stack available (preflight or hard crash).", encoding="utf-8")
        with (folder / "system_metrics.csv").open("w", newline="", encoding="utf-8") as f:
            if self.rows:
                writer = csv.DictWriter(f, fieldnames=list(self.rows[-1]))
                writer.writeheader()
                writer.writerows(self.rows[-1200:])
        self.copy_diagnostics(folder, attempt)
        print(f"Failure: {kind}: {folder.name}", flush=True)

    def report(self):
        resources = trend(self.rows)
        if any(p["warning"] for p in resources.get("process_trends", [])):
            self.warnings.append("RAM grows after warmup; inspect per-process slope and loaded-track sizes")
        if self.audio_totals["underruns"]:
            self.warnings.append("Audio underruns observed")
        if self.counters["track_load_failures"]:
            self.warnings.append("Some track loads failed; see track_load_failures.jsonl")
        if not self.counters["PAD"]:
            self.warnings.append("Existing hot-cue recall not covered: no loaded cues reached PAD")
        if not self.config["self_test"]:
            self.warnings.append("Production loader currently omits USB ANLZ cues/grid; they are not synthesized by the harness")
        status = "FAIL" if self.failures else "PASS WITH WARNINGS" if self.warnings else "PASS"
        report = {"final_status": status, "duration_s": self.elapsed,
            "requested_duration_s": self.config["hours"]*3600,
            "correct_operation_time_s": self.correct_s, "process_uptime_s": self.process_uptime,
            "startup_loading_recovery_s": max(0, self.elapsed-self.correct_s),
            "seed": self.config["seed"], "counters": dict(self.counters),
            "automatic_restarts": self.restarts, "lifecycle_cycles_completed": self.cycles,
            "shutdown_observations": self.shutdown_observations,
            "audio": dict(self.audio_totals), "max_callback_ms": self.callback_max,
            "resources": resources, "usb_files_modified": self.usb_modified,
            "failures": self.failures, "warnings": self.warnings,
            "injected_fault": self.config["inject"], "self_test": self.config["self_test"]}
        write_json(self.output / "report.json", report)
        kinds = Counter(f["type"] for f in self.failures)
        lines = ["# CDJ SOAK TEST REPORT", "", f"Final Status: {status}",
            f"Duration: {self.elapsed:.2f} s", f"Correct Operation Time: {self.correct_s:.2f} s",
            f"Process Uptime (summed): {self.process_uptime:.2f} s",
            f"Startup/Loading/Recovery: {max(0,self.elapsed-self.correct_s):.2f} s",
            f"Seed: {self.config['seed']}", f"USB Files Modified: {self.usb_modified}", "",
            f"Tracks Loaded: {self.counters['tracks_loaded']}", f"Actions Total: {self.counters['actions_total']}",
            f"Loops: {self.counters['BEAT_LOOP'] + self.counters['LOOP_OUT']}",
            f"Hot Cue Calls: {self.counters['hot_cue_calls']}",
            f"Search Actions: {self.counters['SEARCH']}", f"Track Searches: {self.counters['TRACK_SEARCH']}",
            f"Jog Actions: {self.counters['JOG_MOVE']}",
            f"Scratch Actions: {self.counters['scratch_actions']}",
            f"Backspins: {self.counters['backspins']}",
            f"Automatic Restarts: {self.restarts}", f"Crashes: {kinds['CRASH']}",
            f"Hangs: {sum(v for k,v in kinds.items() if 'HANG' in k)}",
            f"Audio Stalls: {kinds['PLAYBACK_STALL']}", f"Invalid States: {kinds['INVALID_STATE']}",
            f"Exceptions: {kinds['EXCEPTION']}",
            f"Audio Underruns: {self.audio_totals['underruns']}",
            f"Max Callback Time: {self.callback_max:.3f} ms", "", "## Resources", "",
            "```json", json.dumps(resources, indent=2), "```", "", "## Failure Packages", ""]
        lines += [f"- {f['type']}: {Path(f['path']).name}" for f in self.failures] or ["None"]
        lines += ["", "## Warnings / Coverage", ""] + [f"- {w}" for w in self.warnings]
        (self.output / "report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
        print(f"{status}: {self.output / 'report.md'}", flush=True)
