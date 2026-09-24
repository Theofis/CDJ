"""Read-only observations and the production loop contract (docs/LOOP.md)."""
from __future__ import annotations

from dataclasses import asdict, fields
from enum import Enum
import math

from virtual_cdj.deck.commands import CommandType as C
from virtual_cdj.deck.state import DeckState, PadMode


STAY = {
    C.PLAY_PAUSE, C.LOOP_HALVE, C.LOOP_DOUBLE, C.BEAT_LOOP,
    C.SEARCH, C.JOG_MOVE, C.JOG_TOUCH, C.JOG_MODE_TOGGLE,
    C.QUANTIZE_TOGGLE, C.QUANTIZE_BEATS, C.SLIP_TOGGLE,
    C.TEMPO_SET, C.TEMPO_RANGE_CYCLE, C.TEMPO_RESET_TOGGLE,
    C.DIRECTION, C.BEAT_JUMP, C.LOOP_IN, C.LOOP_OUT,
}
FORBIDDEN = {
    C.DELETE, C.MEMORY, C.BEATGRID_SHIFT, C.BEATGRID_RESET,
    C.TAG_TRACK_TOGGLE, C.TAG_MENU_ACTION, C.USB_STOP, C.OPEN_SETTINGS,
}
ALLOWED = STAY | {
    C.CUE, C.SEEK, C.TRACK_SEARCH, C.PAD, C.PAD_MODE, C.RELOOP_EXIT,
    C.LOAD, C.WAVEFORM_ZOOM, C.VIEW, C.BROWSE_ROTATE,
}


def json_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [json_value(v) for v in value]
    return value


def snapshot(state: DeckState) -> dict:
    # Do not deepcopy megabytes of waveform arrays on every heartbeat.
    result = {f.name: getattr(state, f.name) for f in fields(state) if f.name != "track"}
    result["loop"] = asdict(state.loop)
    result["slip_state"] = asdict(state.slip_state)
    track = state.track
    result["track"] = None if track is None else {
        "track_id": track.track_id, "title": track.title, "file_path": track.file_path,
        "duration_s": track.duration_s, "bpm": track.original_bpm,
        "key": track.key, "genre": track.genre,
        "hot_cues": [asdict(cue) for cue in track.hot_cues],
        "memory_cues": [asdict(cue) for cue in track.memory_cues],
        "has_waveform": state.has_waveform, "has_beat_grid": state.has_beat_grid,
    }
    result.update(playing=state.is_playing, slipActive=state.slip_active)
    return json_value(result)


def authorize(cmd, state):
    if cmd.type not in ALLOWED or cmd.type in FORBIDDEN:
        raise PermissionError(f"Persistent/source command disabled: {cmd.type}")
    if cmd.type is C.PAD and cmd.pressed:
        if state.pad_mode is not PadMode.HOT_CUE or state.track is None:
            raise PermissionError("Soak PAD calls require HOT_CUE mode and a loaded track")
        if state.track.hot_cue(int(cmd.get("index", -1))) is None:
            raise PermissionError("Empty hot cue pads must never be invoked by the soak test")


def validate(state, before=None, cmd=None, *, loaded=False) -> list[str]:
    errors = []
    for field in ("position_s", "duration_s", "original_bpm", "current_bpm",
                  "tempo_percent", "beat_phase", "quantize_beats"):
        if not math.isfinite(getattr(state, field)):
            errors.append(f"nonfinite {field}")
    if state.has_track and state.duration_s <= 0:
        errors.append("loaded track duration <= 0")
    if state.position_s < -0.001 or state.position_s > state.duration_s + 0.1:
        errors.append("playhead outside track")
    loop = state.loop
    for field in ("in_s", "out_s", "beats", "last_in_s", "last_out_s"):
        value = getattr(loop, field)
        if value is not None and not math.isfinite(value):
            errors.append(f"nonfinite loop.{field}")
    if loop.active:
        if not loop.is_set or not (0 <= loop.in_s < loop.out_s <= state.duration_s + 0.1):
            errors.append("active loop has invalid boundaries")
    slip = state.slip_state
    if slip.active and (slip.position_s is None or not math.isfinite(slip.position_s)
                        or not 0 <= slip.position_s <= state.duration_s + 0.1):
        errors.append("invalid slip background")
    reasons = {r.value for r in slip.reasons}
    if "LOOP" in reasons and not loop.active:
        errors.append("slip LOOP reason without loop")
    if "SCRATCH" in reasons and not (state.jog_touch and state.jog_mode.value == "VINYL"):
        errors.append("slip SCRATCH reason without vinyl touch")
    if "REVERSE" in reasons and state.direction.value == "FWD":
        errors.append("slip REVERSE reason while forward")
    if loaded and (loop.active or loop.is_set or loop.has_last or slip.active):
        errors.append("track load retained loop/slip operation")
    if before is not None and cmd is not None:
        same = before.track is not None and state.track is not None and before.track.track_id == state.track.track_id
        if same and before.track.hot_cues != state.track.hot_cues:
            errors.append("existing hot cues changed")
        if same and before.track.memory_cues != state.track.memory_cues:
            errors.append("memory cues changed")
        if same and (before.track.beat_grid != state.track.beat_grid or
                     before.track.original_bpm != state.track.original_bpm or
                     before.track.key != state.track.key):
            errors.append("loaded track analysis metadata changed")
        if same and before.loop.active and cmd.type in STAY and not loop.active:
            errors.append(f"STAY contract violated: {cmd.type.value}")
        if before.loop.active and cmd.type is C.RELOOP_EXIT and loop.active:
            errors.append("RELOOP_EXIT did not exit")
        if before.loop.active and cmd.type is C.SEEK:
            target = float(cmd.get("position_s", 0))
            expected = before.loop.in_s <= target < before.loop.out_s
            if loop.active != expected:
                errors.append("SEEK loop EXIT/STAY contract violated")
        if before.loop.active and cmd.type is C.PAD and cmd.pressed and same:
            cue = before.track.hot_cue(int(cmd.get("index", -1)))
            if cue:
                expected = (cue.kind.value == "LOOP" and cue.loop_end_s is not None) or (
                    before.loop.in_s <= cue.position_s < before.loop.out_s)
                if loop.active != expected:
                    errors.append("HOT_CUE loop EXIT/STAY contract violated")
        if cmd.type is C.PLAY_PAUSE and state.has_track and state.is_playing == before.is_playing:
            errors.append("PLAY_PAUSE did not toggle transport")
    return errors


class HealthMonitor:
    """Supervisor-side progress detection; no calls into the child."""
    def __init__(self, now, timeout=10.0):
        self.timeout = timeout
        self.last_audio = now
        self.last_progress = now
        self.callbacks = None
        self.token = None

    def inspect(self, sample, now):
        if now - sample["gui_heartbeat"] > self.timeout:
            return "GUI_HANG"
        if now - sample["runner_heartbeat"] > self.timeout:
            return "RUNNER_HANG"
        callbacks = sample["audio"].get("callbacks", 0)
        if callbacks != self.callbacks:
            self.last_audio, self.callbacks = now, callbacks
        if now - self.last_audio > self.timeout:
            return "AUDIO_HANG"
        s = sample.get("state", {})
        track = s.get("track") or {}
        # Wrap count prevents false stalls when observation interval aliases a short loop.
        token = (track.get("track_id"), sample.get("voice_position"), sample.get("loop_wraps"))
        held = s.get("jog_touch") and s.get("jog_mode") == "VINYL"
        reverse = s.get("direction") in ("REV", "SLIP_REV")
        boundary = not s.get("loop", {}).get("active") and (
            (reverse and s.get("position_s", 0) < .01) or
            (not reverse and s.get("position_s", 0) > s.get("duration_s", 0) - .01))
        expect = s.get("playing") and not held and not boundary and (
            s.get("tempo_reset") or s.get("tempo_percent", 0) > -99.99
        )
        if token != self.token or not expect or sample.get("loading"):
            self.token, self.last_progress = token, now
        if now - self.last_progress > self.timeout:
            return "PLAYBACK_STALL"
        return None
