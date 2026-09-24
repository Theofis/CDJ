"""Monoton getakteter, threadsicherer Fake-Player."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from ..prolink.models import BeatEvent, PlayerState


class FakePlayer:
    """Simuliert genau einen externen Player ohne GUI-Frame als Zeitquelle."""

    def __init__(
        self,
        player_id: int = 1,
        *,
        device_name: str = "CDJ-3000 SIM",
        track_id: str = "test-track-01",
        original_bpm: float = 150.0,
        effective_bpm: float = 154.0,
        duration_ms: int = 300_000,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if player_id <= 0:
            raise ValueError("player_id muss positiv sein")
        if original_bpm <= 0 or effective_bpm <= 0:
            raise ValueError("BPM muss positiv sein")
        self.player_id = player_id
        self.device_name = device_name
        self.device_type = "SIMULATOR"
        self.track_id = track_id
        self.original_bpm = float(original_bpm)
        self.effective_bpm = float(effective_bpm)
        self.duration_ms = int(duration_ms)
        self.position_ms = 0.0
        self.online = True
        self.playing = False
        self.is_master = False
        self.sync_enabled = False
        self.on_air = True
        self.key: str | None = "8A"
        self.reverse = False

        self._clock_ns = clock_ns
        self._lock = threading.RLock()
        self._last_tick_ns = clock_ns()
        self._beat_number = 1
        self._beat_progress = 0.0
        self._pending_beat = False
        self._queued_events: list[BeatEvent] = []

    @property
    def pitch_percent(self) -> float:
        return (self.effective_bpm / self.original_bpm - 1.0) * 100.0

    @property
    def beat_number(self) -> int:
        with self._lock:
            return self._beat_number

    @property
    def beat_in_bar(self) -> int:
        with self._lock:
            return (self._beat_number - 1) % 4 + 1

    def set_playing(self, playing: bool, *, now_ns: int | None = None) -> None:
        now = self._now(now_ns)
        with self._lock:
            self._queue_motion(now)
            target = bool(playing) and self.online
            if target and not self.playing:
                self._pending_beat = True
            self.playing = target
            self._last_tick_ns = now

    def play(self, *, now_ns: int | None = None) -> None:
        self.set_playing(True, now_ns=now_ns)

    def pause(self, *, now_ns: int | None = None) -> None:
        self.set_playing(False, now_ns=now_ns)

    def set_effective_bpm(self, bpm: float, *, now_ns: int | None = None) -> None:
        if bpm <= 0:
            raise ValueError("BPM muss positiv sein")
        now = self._now(now_ns)
        with self._lock:
            self._queue_motion(now)
            self.effective_bpm = float(bpm)
            self._last_tick_ns = now

    def set_position_ms(self, position_ms: float, *, now_ns: int | None = None) -> None:
        now = self._now(now_ns)
        with self._lock:
            self.position_ms = max(0.0, min(float(position_ms), self.duration_ms))
            beat_position = self.position_ms / (60_000.0 / self.original_bpm)
            whole = int(beat_position)
            self._beat_number = whole + 1
            self._beat_progress = beat_position - whole
            self._pending_beat = False
            self._last_tick_ns = now

    def set_online(self, online: bool, *, now_ns: int | None = None) -> None:
        now = self._now(now_ns)
        with self._lock:
            self._queue_motion(now)
            self.online = bool(online)
            if not self.online:
                self.playing = False
                self.is_master = False
                self._pending_beat = False
            self._last_tick_ns = now

    def set_master(self, master: bool) -> None:
        with self._lock:
            self.is_master = bool(master) and self.online

    def set_sync(self, enabled: bool) -> None:
        with self._lock:
            self.sync_enabled = bool(enabled)

    def set_on_air(self, on_air: bool) -> None:
        with self._lock:
            self.on_air = bool(on_air)

    def set_track(
        self,
        track_id: str,
        *,
        original_bpm: float | None = None,
        effective_bpm: float | None = None,
        duration_ms: int | None = None,
        now_ns: int | None = None,
    ) -> None:
        if original_bpm is not None and original_bpm <= 0:
            raise ValueError("original_bpm muss positiv sein")
        if effective_bpm is not None and effective_bpm <= 0:
            raise ValueError("effective_bpm muss positiv sein")
        with self._lock:
            self.track_id = track_id
            if original_bpm is not None:
                self.original_bpm = float(original_bpm)
            if effective_bpm is not None:
                self.effective_bpm = float(effective_bpm)
            if duration_ms is not None:
                self.duration_ms = int(duration_ms)
            self.position_ms = 0.0
            self._beat_number = 1
            self._beat_progress = 0.0
            self._pending_beat = self.playing
            self._last_tick_ns = self._now(now_ns)

    def advance(self, *, now_ns: int | None = None) -> tuple[PlayerState, tuple[BeatEvent, ...]]:
        """Bis ``now_ns`` fortschreiben und fällige Beat-Flanken liefern."""
        now = self._now(now_ns)
        with self._lock:
            events = self._queued_events
            self._queued_events = []
            events.extend(self._advance_motion(now))
            return self._snapshot(now), tuple(events)

    def snapshot(self, *, now_ns: int | None = None) -> PlayerState:
        # Nur der Server-Takt konsumiert BeatEvents. Diagnose/UI darf einen
        # Snapshot lesen, ohne dabei Flanken aus der Queue zu entfernen.
        now = self._now(now_ns)
        with self._lock:
            return self._snapshot(now)

    def _queue_motion(self, now_ns: int) -> None:
        self._queued_events.extend(self._advance_motion(now_ns))

    def _advance_motion(self, now_ns: int) -> list[BeatEvent]:
        start_ns = self._last_tick_ns
        dt_s = max(0.0, (now_ns - start_ns) / 1_000_000_000)
        self._last_tick_ns = now_ns
        events: list[BeatEvent] = []
        if not self.online or not self.playing:
            return events

        if self._pending_beat:
            events.append(self._event(start_ns))
            self._pending_beat = False

        speed = self.effective_bpm / self.original_bpm
        self.position_ms = min(
            float(self.duration_ms), self.position_ms + dt_s * 1000.0 * speed
        )

        beats_advanced = dt_s * self.effective_bpm / 60.0
        previous_progress = self._beat_progress
        total = previous_progress + beats_advanced
        crossings = int(total)
        self._beat_progress = total - crossings
        for crossing in range(1, crossings + 1):
            if beats_advanced > 0:
                fraction = (crossing - previous_progress) / beats_advanced
            else:  # pragma: no cover - BPM ist immer positiv
                fraction = 1.0
            timestamp = start_ns + int(max(0.0, min(1.0, fraction)) * dt_s * 1e9)
            self._beat_number += 1
            events.append(self._event(timestamp))

        if self.position_ms >= self.duration_ms:
            self.playing = False
        return events

    def _event(self, timestamp_ns: int) -> BeatEvent:
        return BeatEvent(
            player_id=self.player_id,
            timestamp_ns=timestamp_ns,
            beat_number=self._beat_number,
            beat_in_bar=(self._beat_number - 1) % 4 + 1,
            bpm=self.effective_bpm,
            position_ms=self.position_ms,
            is_master=self.is_master,
        )

    def _snapshot(self, now_ns: int) -> PlayerState:
        return PlayerState(
            player_id=self.player_id,
            device_name=self.device_name,
            device_type=self.device_type,
            ip_address="127.0.0.1",
            online=self.online,
            last_update_ns=now_ns,
            playing=self.playing,
            paused=self.online and not self.playing,
            reverse=self.reverse,
            track_id=self.track_id,
            track_source_player=self.player_id,
            duration_ms=self.duration_ms,
            position_ms=self.position_ms,
            position_valid=self.online,
            original_bpm=self.original_bpm,
            effective_bpm=self.effective_bpm,
            pitch_percent=self.pitch_percent,
            beat_number=self._beat_number,
            beat_in_bar=(self._beat_number - 1) % 4 + 1,
            sync_enabled=self.sync_enabled,
            is_master=self.is_master,
            on_air=self.on_air,
            key=self.key,
            timing_source="simulator-monotonic",
        )

    def _now(self, value: int | None) -> int:
        return self._clock_ns() if value is None else int(value)
