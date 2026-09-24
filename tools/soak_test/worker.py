"""Production application + Tk event loop, controlled only through public commands."""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict
import faulthandler
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import threading
import time
import traceback

from .scenarios import Planner
from .safety import inside, install_write_guard, require_read_only
from .validation import authorize, snapshot, validate


def worker_main(config, connection, stop, dump, attempt, resume, action_ack):
    directory = Path(config["output"]) / f"attempt_{attempt:04d}"
    directory.mkdir()
    stack = (directory / "stacktraces.txt").open("w", encoding="utf-8")
    faulthandler.enable(stack, all_threads=True)
    logger = logging.getLogger()
    handler = RotatingFileHandler(directory / "player.log", maxBytes=1_000_000, backupCount=2)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        Worker(config, connection, stop, dump, directory, attempt, resume, stack, action_ack).run()
    except BaseException:
        text = traceback.format_exc()
        (directory / "exception.txt").write_text(text, encoding="utf-8")
        faulthandler.dump_traceback(file=stack, all_threads=True)
        try:
            connection.send({"kind": "fatal", "error": text})
        except (OSError, EOFError):
            pass
        raise
    finally:
        faulthandler.cancel_dump_traceback_later()
        faulthandler.disable()
        handler.close()
        logger.removeHandler(handler)
        stack.close()
        connection.close()


class Worker:
    def __init__(self, config, connection, stop, dump, directory, attempt, resume, stack, action_ack):
        self.config, self.connection, self.stop, self.dump = config, connection, stop, dump
        self.directory, self.attempt, self.stack = directory, attempt, stack
        self.planner = Planner(config["seed"], config["profile"], config.get("weights"))
        if resume:
            self.planner.restore(resume["planner"])
        self.action_number = resume.get("action_number", 0) if resume else 0
        self.events = queue.Queue(maxsize=4000)
        self.counters = Counter()
        self.violations = []
        self.errors = []
        self.loading = False
        self.load_started = 0.0
        self.next_action = 0.0
        self.pending = deque()
        self.started = time.monotonic()
        self.gui_heartbeat = self.started
        self.runner_heartbeat = self.started
        self.telemetry = None
        self.ready = False
        self.action_ack = action_ack
        self.injected = False
        self.finished = threading.Event()
        self.source = Path(config["usb_source"]).resolve() if config.get("usb_source") else None
        self.replay = deque(config.get("replay_actions", []))
        self.replay_mode = bool(config.get("replay"))
        self.replay_previous_time = None

    def run(self):
        if self.source:
            require_read_only(self.source)
            install_write_guard(self.source, self.violations)
        # Install protection before importing/constructing the production app.
        from virtual_cdj.app import CdjApplication
        from virtual_cdj.cdj_ui.window import CdjDisplayApp
        from run_cdj import wire_display
        from virtual_cdj.deck.library import TrackListLibrary, SourceInfo, SourceKind
        from virtual_cdj.media_library.rekordbox.reader import read_library
        from virtual_cdj.media_library.sources import to_track_info

        self.app = CdjApplication(
            start_audio=True, demo=self.config["self_test"], usb=False,
            cache_directory=self.config["cache"],
            settings_path=self.directory / "settings.json",
            button_settings_path=self.directory / "buttons.json",
            backend_output=lambda _line: None,
        )
        self.root = CdjDisplayApp()
        try:
            if not self.app.audio_started:
                raise RuntimeError("Real audio output required: " + self.app.audio_status_text)
            self.window = self.root.add_display(self.app.providers[1], modes=self.app.modes,
                operating_modes=self.app.mode_manager, hardware=self.app.hardware,
                calibration=self.app.calibration, audio_status=lambda: self.app.audio_status_text)
            if not self.config["visible"]:
                self.window.withdraw()
            wire_display(self.app, 1, self.window.screen)
            self.root.report_callback_exception = self.callback_exception
            self.app.on_load_finished.append(self.load_finished)
            self.app.on_load_requested.append(self.load_requested)
            if self.source:
                library = read_library(str(self.source / "PIONEER" / "rekordbox" / "export.pdb"),
                    root_path=str(self.source), volume_id="SOAK", label=self.source.name)
                infos = [to_track_info(t, volume_id=library.volume_id,
                                      root_path=library.root_path) for t in library.tracks]
                for info in infos:
                    if not info.file_path or not inside(info.file_path, self.source):
                        raise RuntimeError(f"Track path escapes selected USB: {info.file_path}")
                self.app.library.add(TrackListLibrary(
                    SourceInfo("SOAK", "Soak USB (read only)", SourceKind.USB, has_library=True), infos))
                self.window.screen.set_model(self.app.library)
                self.tracks = {info.track_id: info.file_path for info in infos}
                self.catalog = [{"id": i.track_id, "path": i.file_path, "bpm": i.original_bpm,
                    "duration": i.duration_s, "key": i.key, "genre": i.genre} for i in infos]
            else:
                self.tracks = {tid: f"demo://{tid}" for tid in self.app.demo_track_ids()}
                self.catalog = [{"id": tid, "path": path} for tid, path in self.tracks.items()]
            if not self.tracks:
                raise RuntimeError("No tracks in selected library")
            (self.directory / "catalog.json").write_text(json.dumps(self.catalog, indent=2), encoding="utf-8")
            threading.Thread(target=self.transmit, name="soak-telemetry", daemon=True).start()
            self.root.after(0, self.pump)
            self.root.after(0, self.heartbeat)
            self.root.mainloop()
        finally:
            faulthandler.dump_traceback(file=self.stack, all_threads=True)
            final_sample = self.capture_sample(time.monotonic())
            self.app.close()
            self.root.destroy()
            # Worker/PortAudio resources must have closed before reporting clean exit.
            live = [t.name for t in threading.enumerate()
                    if t.name in ("analysis-worker", "usb-library-reader")]
            self.events.put({"kind": "shutdown", "threads_remaining": live,
                             "audio_running": self.app.audio.is_running,
                             "final_sample": final_sample})
            self.finished.set()
            time.sleep(.6)

    def callback_exception(self, typ, value, tb):
        self.errors.append("".join(traceback.format_exception(typ, value, tb)))

    def load_requested(self, deck_id, path):
        if deck_id == 1:
            self.loading = True
            self.load_started = time.monotonic()

    def load_finished(self, result):
        self.loading = False
        if not result.ok:
            self.counters["track_load_failures"] += 1
            self.events.put_nowait({"kind": "load_failure", "path": result.request.path,
                                   "error": result.error, "traceback": result.traceback_text})
            self.pending.clear()
            self.pending.extend(self.expand({"name": "LOAD", "choice": 0, "value": 0}))
            self.next_action = 0
            return
        self.counters["tracks_loaded"] += 1
        self.errors.extend(validate(self.state(), loaded=True))
        if self.state().track is not result.track.info:
            self.errors.append("track load did not install new metadata/waveform/grid/cues")
        self.counters["loaded_with_hot_cues"] += bool(self.state().track.hot_cues)
        self.counters["loaded_with_memory_cues"] += bool(self.state().track.memory_cues)
        self.counters["loaded_with_beat_grid"] += self.state().has_beat_grid
        self.counters["loaded_with_waveform"] += self.state().has_waveform
        self.ready = True
        self.events.put_nowait({"kind": "loaded", "path": result.request.path,
                               "track_id": self.state().track.track_id})
        if self.config["lifecycle_cycles"] and self.counters["tracks_loaded"] == 1:
            self.cycle_finish = time.monotonic() + self.config["cycle_seconds"]
            self.pending.clear()
            self.pending.append(("PLAY_PAUSE", {}, self.config["cycle_seconds"], "LIFECYCLE_PLAY"))
            self.next_action = 0

    def state(self):
        return self.app.controllers[1].get_state()

    def heartbeat(self):
        now = time.monotonic()
        self.gui_heartbeat = now
        self.telemetry = self.capture_sample(now)
        # Native faulthandler watchdog can still dump when the Python GUI locks.
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(self.config["watchdog_seconds"] + 2,
                                         file=self.stack, repeat=True)
        self.root.after(500, self.heartbeat)

    def capture_sample(self, now):
        voice = self.app.audio.voice(1)
        return {
            "kind": "sample", "attempt": self.attempt,
            "gui_heartbeat": self.gui_heartbeat, "runner_heartbeat": self.runner_heartbeat,
            "state": snapshot(self.state()), "audio": asdict(self.app.audio.metrics),
            "gui_fps": self.window.screen.debug.fps if hasattr(self, "window") else None,
            "voice_position": voice.position_seconds, "loop_wraps": voice.loop_wraps,
            "loading": self.loading, "ready": self.ready,
            "counters": dict(self.counters), "errors": list(self.errors),
            "violations": list(self.violations), "action_number": self.action_number,
            "planner": self.planner.checkpoint(),
            "pending_commands": list(self.pending),
            "next_action_in_s": max(0, self.next_action - now),
        }
    def transmit(self):
        while not self.finished.is_set():
            if self.dump.is_set():
                faulthandler.dump_traceback(file=self.stack, all_threads=True)
                self.dump.clear()
            try:
                while True:
                    self.connection.send(self.events.get_nowait())
            except queue.Empty:
                pass
            if self.telemetry:
                self.connection.send(self.telemetry)
            self.finished.wait(.1)
        try:
            while True:
                self.connection.send(self.events.get_nowait())
        except queue.Empty:
            pass

    def pump(self):
        now = time.monotonic()
        if self.stop.is_set():
            self.root.quit()
            return
        try:
            self.app.tick()
            injection = self.config.get("inject")
            if injection and self.attempt == 1 and self.ready and not self.injected:
                if now - self.started >= self.config["inject_after"]:
                    self.injected = True
                    if injection == "gui-freeze":
                        self.stop.wait()  # supervisor remains independent
                    elif injection == "audio-freeze":
                        self.app.audio.stop()
                    elif injection == "crash":
                        import os
                        os._exit(97)
            runner_frozen = injection == "runner-freeze" and self.injected
            if not runner_frozen:
                self.runner_heartbeat = now
                if not self.errors:
                    self.errors.extend(validate(self.state()))
                if self.loading and now - self.load_started > self.config["load_timeout"]:
                    self.errors.append("TRACK_LOAD_TIMEOUT")
                if not self.errors and not self.violations and not self.loading and now >= self.next_action:
                    self.step(now)
            if getattr(self, "cycle_finish", float("inf")) <= now:
                self.events.put_nowait({"kind": "cycle_complete"})
                self.root.quit()
                return
            if self.errors or self.violations:
                # Stop issuing actions; heartbeat continues for failure capture.
                self.next_action = now + 86400
        except BaseException:
            self.errors.append(traceback.format_exc())
            self.next_action = now + 86400
        self.root.after(16, self.pump)

    def step(self, now):
        if self.replay_mode:
            if not self.replay:
                self.events.put_nowait({"kind": "replay_complete"})
                self.root.quit()
                return
            row = self.replay.popleft()
            delay = 0 if self.config["fast_replay"] else row.get("delay_after_s", 0)
            self.execute(row["command"], row["parameters"], row.get("scenario", "REPLAY"), delay)
            self.next_action = now + delay
            return
        if not self.pending:
            if not self.state().has_track:
                plan = {"name": "LOAD", "delay": 0, "choice": 0, "value": .5, "phase": "STARTUP"}
            else:
                plan = self.planner.next()
            self.pending.extend(self.expand(plan))
            if not self.pending:
                self.counters["skipped_" + plan["name"]] += 1
                self.next_action = now + plan["delay"]
                return
            kind, params, delay, scenario = self.pending[-1]
            self.pending[-1] = kind, params, delay + plan["delay"], scenario
        kind, params, delay, scenario = self.pending.popleft()
        self.execute(kind, params, scenario, delay)
        self.next_action = now + delay

    def execute(self, kind, params, scenario, delay=0):
        from virtual_cdj.deck.commands import CommandType, command
        cmd = command(CommandType(kind), 1, "SOAK", **params)
        before = self.state()
        authorize(cmd, before)
        if kind == "LOAD":
            target = str(params["track_id"])
            if target not in self.tracks:
                # Recorded LOAD commands may use only catalog IDs or exact catalog paths.
                if target not in self.tracks.values():
                    raise PermissionError("Replay LOAD outside selected library")
            self.loading = True
            self.load_started = time.monotonic()
        elif kind == "TRACK_SEARCH":
            self.load_started = time.monotonic()
        self.action_number += 1
        row = {"kind": "action", "action_number": self.action_number,
               "attempt": self.attempt, "phase": self.planner.phase,
               "timestamp": time.time(), "monotonic": time.monotonic(),
               "track_id": before.track.track_id if before.track else None,
               "current_position": before.position_s, "command": kind,
               "parameters": params, "scenario": scenario, "delay_after_s": delay}
        # Persist intent before dispatch so the action that hangs is retained.
        self.action_ack.clear()
        self.events.put_nowait(row)
        deadline = time.monotonic() + self.config["watchdog_seconds"]
        while not self.action_ack.wait(.02):
            if self.stop.is_set():
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("Supervisor did not acknowledge action log")
        self.app.dispatch(cmd)
        self.counters[kind] += 1
        if kind == "JOG_MOVE" and scenario == "SCRATCH":
            self.counters["scratch_actions"] += 1
        if kind == "JOG_MOVE" and scenario == "BACKSPIN":
            self.counters["backspins"] += 1
        if kind == "PAD" and params.get("pressed"):
            self.counters["hot_cue_calls"] += 1
        self.counters["actions_total"] += 1
        self.errors.extend(validate(self.state(), before, cmd))

    def expand(self, plan):
        name, choice, value = plan["name"], plan["choice"], plan["value"]
        s = self.state()
        rows = []
        def add(kind, delay=.1, **params):
            rows.append((kind, params, delay, name))
        if name == "LOAD":
            add("JOG_TOUCH", pressed=False)
            add("SEARCH", direction=1, pressed=False)
            add("LOAD", track_id=self.planner.choose_track(self.tracks))
        elif name in ("PLAY", "PAUSE"):
            if s.is_playing != (name == "PLAY"):
                add("PLAY_PAUSE")
        elif name == "CUE":
            add("CUE", delay=.4, pressed=True)
            add("CUE", pressed=False)
        elif name == "HOT_CUE":
            if s.track and s.track.hot_cues:
                index = s.track.hot_cues[choice % len(s.track.hot_cues)].index
                add("PAD_MODE", mode="HOT_CUE")
                add("PAD", delay=.5, index=index, pressed=True)
                add("PAD", index=index, pressed=False)
        elif name in ("LOOP", "LOOP4", "LOOP8", "LOOP_TINY"):
            beats = {"LOOP4": 4, "LOOP8": 8, "LOOP_TINY": .25}.get(name, (.25, .5, 1, 2, 4, 8, 16, 32)[choice % 8])
            add("BEAT_LOOP", beats=beats)
        elif name == "MANUAL_LOOP":
            if s.loop.active:
                add("RELOOP_EXIT")
            add("LOOP_IN", delay=1.0)
            add("LOOP_OUT")
        elif name == "HALF_DOUBLE":
            add("LOOP_HALVE" if choice % 2 else "LOOP_DOUBLE")
        elif name in ("HALF", "DOUBLE"):
            add("LOOP_HALVE" if name == "HALF" else "LOOP_DOUBLE")
        elif name in ("JOG", "SCRATCH", "BACKSPIN"):
            touched = name != "JOG"
            if touched:
                if s.jog_mode.value != "VINYL":
                    add("JOG_MODE_TOGGLE")
                add("JOG_TOUCH", pressed=True)
            ticks = -3200 if name == "BACKSPIN" else (1 if choice % 2 else -1) * (20 + choice % 300)
            add("JOG_MOVE", delta=ticks, ticks_per_rev=800, velocity=abs(ticks)*5,
                touched=touched, direction="CW" if ticks > 0 else "CCW")
            if touched:
                add("JOG_TOUCH", pressed=False)
        elif name.startswith("SEARCH"):
            direction = 1 if name == "SEARCH_FWD" else -1 if name == "SEARCH_BACK" else (1 if choice % 2 else -1)
            add("SEARCH", delay=.5, direction=direction, pressed=True)
            add("SEARCH", direction=direction, pressed=False)
        elif name == "TRACK_SEARCH":
            add("TRACK_SEARCH", direction=1 if choice % 2 else -1)
        elif name == "BEAT_JUMP":
            add("BEAT_JUMP", direction=1 if choice % 2 else -1, beats=(.5, 1, 4, 16)[choice % 4])
        elif name.startswith("SEEK_"):
            positions = {"SEEK_START": 0.0, "SEEK_END": max(0, s.duration_s - .001),
                         "SEEK_MIDDLE": s.duration_s * .5}
            if s.loop.is_set:
                positions.update(SEEK_LOOP_IN=s.loop.in_s, SEEK_LOOP_END=s.loop.in_s+s.loop.length_s*.999)
            if name in positions:
                add("SEEK", position_s=positions[name])
        elif name == "EXIT":
            if s.loop.active:
                add("RELOOP_EXIT")
        elif name in ("SLIP_ON", "SLIP_OFF"):
            if s.slip != (name == "SLIP_ON"):
                add("SLIP_TOGGLE")
        elif name in ("REVERSE", "REVERSE_ON", "REVERSE_OFF"):
            direction = "FWD" if name == "REVERSE_OFF" or (name == "REVERSE" and s.direction.value != "FWD") else "REV"
            add("DIRECTION", position=direction)
        elif name == "NORMALIZE":
            add("JOG_TOUCH", pressed=False)
            add("SEARCH", direction=1, pressed=False)
            add("DIRECTION", position="FWD")
            add("TEMPO_SET", value=.5)
            if s.slip:
                add("SLIP_TOGGLE")
        elif name == "TEMPO":
            add("TEMPO_SET", value=.1 + .8*value)
        elif name == "QUANTIZE_VALUE":
            add("QUANTIZE_BEATS", beats=(.125, .25, .5, 1)[choice % 4])
        elif name == "ZOOM":
            add("WAVEFORM_ZOOM", delta=1 if choice % 2 else -1)
        elif name == "BROWSE":
            add("VIEW", view="SOURCE")
            add("VIEW", view="BROWSE")
            add("BROWSE_ROTATE", delta=1)
            add("VIEW", view="WAVEFORM")
        else:
            mapped = {"SLIP": "SLIP_TOGGLE", "QUANTIZE": "QUANTIZE_TOGGLE",
                      "JOG_MODE": "JOG_MODE_TOGGLE", "TEMPO_RESET": "TEMPO_RESET_TOGGLE",
                      "TEMPO_RANGE": "TEMPO_RANGE_CYCLE"}
            add(mapped.get(name, name))
        return rows
