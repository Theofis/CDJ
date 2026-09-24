"""Monotonic UDP PRO DJ LINK network emulator.

The emulator knows nothing about ``PlayerState`` or the application.  Its only
output is UDP, so every test using it crosses the same sockets and packet
parser as later hardware.
"""

from __future__ import annotations

import logging
import select
import socket
import threading
import time
from dataclasses import dataclass
from ipaddress import IPv4Address
from pathlib import Path

from ..capture import PacketJournal
from ..packets import ANNOUNCEMENT_PORT, BEAT_PORT, MAGIC, STATUS_PORT
from ..wire import (
    CDJ_NAME,
    discovery_packet_name,
    encode_beat,
    encode_cdj_status,
    encode_claim_ip,
    encode_claim_mac,
    encode_claim_number,
    encode_hello,
    encode_keep_alive,
    encode_number_conflict,
    format_mac,
    parse_mac,
)


LOG = logging.getLogger("PROLINK")


class PlayerNumberInUse(RuntimeError):
    """The emulator refused to collide with an observed player number."""


@dataclass(frozen=True, slots=True)
class EmulatedPlayerSnapshot:
    player_id: int
    name: str
    online: bool
    playing: bool
    bpm: float
    pitch_percent: float
    effective_bpm: float
    beat_number: int
    beat_in_bar: int
    position_ms: float
    master: bool
    sync: bool
    on_air: bool
    track_id: int
    revision: int


class EmulatedPlayer:
    """Thread-safe controls with a monotonic transport clock."""

    def __init__(
        self,
        *,
        player_id: int = 1,
        name: str = CDJ_NAME,
        bpm: float = 154.0,
        pitch_percent: float = 0.0,
        playing: bool = False,
        master: bool = False,
        sync: bool = False,
        on_air: bool = False,
        track_id: int = 1,
        online: bool = True,
        clock_ns=time.monotonic_ns,
    ) -> None:
        if not 1 <= int(player_id) <= 4:
            raise ValueError("network emulator player number must be in 1..4")
        if not 0 < float(bpm) < 655.35:
            raise ValueError("BPM must be greater than 0 and below 655.35")
        if 1.0 + float(pitch_percent) / 100.0 <= 0:
            raise ValueError("pitch must result in a positive tempo")
        if not 1 <= int(track_id) <= 0xFFFF_FFFF:
            raise ValueError("track ID must be in 1..0xffffffff")
        self.player_id = int(player_id)
        self.name = name
        self._clock_ns = clock_ns
        self._lock = threading.RLock()
        self._online = bool(online)
        self._playing = bool(playing)
        self._bpm = float(bpm)
        self._pitch_percent = float(pitch_percent)
        self._master = bool(master)
        self._sync = bool(sync)
        self._on_air = bool(on_air)
        self._track_id = int(track_id)
        self._position_ms = 0.0
        self._anchor_ns = self._clock_ns()
        self._last_beat_number = 1
        self._next_beat_number = 1
        self._revision = 0
        self._manual_beats = 0

    def _position_at(self, now_ns: int) -> float:
        if not self._playing or now_ns <= self._anchor_ns:
            return self._position_ms
        multiplier = 1.0 + self._pitch_percent / 100.0
        return self._position_ms + (now_ns - self._anchor_ns) / 1_000_000 * multiplier

    def _anchor(self, now_ns: int) -> None:
        self._position_ms = self._position_at(now_ns)
        self._anchor_ns = now_ns

    def snapshot(self, now_ns: int | None = None) -> EmulatedPlayerSnapshot:
        now = self._clock_ns() if now_ns is None else int(now_ns)
        with self._lock:
            return EmulatedPlayerSnapshot(
                player_id=self.player_id,
                name=self.name,
                online=self._online,
                playing=self._playing,
                bpm=self._bpm,
                pitch_percent=self._pitch_percent,
                effective_bpm=self._bpm * (1.0 + self._pitch_percent / 100.0),
                beat_number=self._last_beat_number,
                beat_in_bar=((self._last_beat_number - 1) % 4) + 1,
                position_ms=self._position_at(now),
                master=self._master,
                sync=self._sync,
                on_air=self._on_air,
                track_id=self._track_id,
                revision=self._revision,
            )

    def set_online(self, online: bool) -> None:
        with self._lock:
            value = bool(online)
            if self._online != value:
                self._online = value
                self._revision += 1

    def set_playing(self, playing: bool) -> None:
        now = self._clock_ns()
        with self._lock:
            value = bool(playing)
            if self._playing != value:
                self._anchor(now)
                self._playing = value
                self._revision += 1

    def set_bpm(self, bpm: float) -> None:
        value = float(bpm)
        if not 0 < value < 655.35:
            raise ValueError("BPM must be greater than 0 and below 655.35")
        with self._lock:
            if self._bpm != value:
                self._bpm = value
                self._revision += 1

    def set_pitch(self, pitch_percent: float) -> None:
        value = float(pitch_percent)
        if 1.0 + value / 100.0 <= 0:
            raise ValueError("pitch must result in a positive tempo")
        now = self._clock_ns()
        with self._lock:
            if self._pitch_percent != value:
                self._anchor(now)
                self._pitch_percent = value
                self._revision += 1

    def set_master(self, enabled: bool) -> None:
        with self._lock:
            self._master = bool(enabled)

    def set_sync(self, enabled: bool) -> None:
        with self._lock:
            self._sync = bool(enabled)

    def set_on_air(self, enabled: bool) -> None:
        with self._lock:
            self._on_air = bool(enabled)

    def set_track_id(self, track_id: int) -> None:
        value = int(track_id)
        if not 1 <= value <= 0xFFFF_FFFF:
            raise ValueError("track ID must be in 1..0xffffffff")
        now = self._clock_ns()
        with self._lock:
            self._anchor(now)
            self._track_id = value
            self._position_ms = 0.0
            self._anchor_ns = now
            self._last_beat_number = 1
            self._next_beat_number = 1
            self._revision += 1

    def trigger_beat(self, beat_in_bar: int | None = None) -> None:
        with self._lock:
            if beat_in_bar is not None:
                if beat_in_bar not in (1, 2, 3, 4):
                    raise ValueError("beat in bar must be 1, 2, 3 or 4")
                current_bar = ((self._next_beat_number - 1) % 4) + 1
                self._next_beat_number += (beat_in_bar - current_bar) % 4
            self._manual_beats += 1

    def take_manual_beat(self) -> bool:
        with self._lock:
            if self._manual_beats <= 0:
                return False
            self._manual_beats -= 1
            return True

    def take_beat(self, now_ns: int) -> EmulatedPlayerSnapshot:
        with self._lock:
            self._last_beat_number = self._next_beat_number
            self._next_beat_number += 1
            return self.snapshot(now_ns)


@dataclass(slots=True)
class _Peer:
    player_id: int
    ip: str
    mac: bytes
    last_seen_ns: int


class ProLinkNetworkEmulator:
    """Represent one isolated virtual player using only PRO DJ LINK UDP."""

    def __init__(
        self,
        player: EmulatedPlayer,
        *,
        bind_ip: str,
        broadcast_ip: str,
        mac: str | bytes,
        target_ip: str | None = None,
        claim_number: bool = True,
        claim_prescan_s: float = 2.5,
        discovery_interval_s: float = 0.3,
        keepalive_interval_s: float = 2.0,
        status_interval_s: float = 0.2,
        generation: int = 0,
        capture_path: str | Path | None = None,
        raw_dump: bool = False,
        clock_ns=time.monotonic_ns,
    ) -> None:
        self.player = player
        self.bind_ip = str(IPv4Address(bind_ip))
        self.broadcast_ip = str(IPv4Address(broadcast_ip))
        self.target_ip = str(IPv4Address(target_ip)) if target_ip else None
        self.mac = parse_mac(mac)
        self.claim_number = bool(claim_number)
        self.claim_prescan_s = max(0.0, float(claim_prescan_s))
        self.discovery_interval_s = max(0.001, float(discovery_interval_s))
        self.keepalive_interval_ns = int(max(0.02, keepalive_interval_s) * 1e9)
        self.status_interval_ns = int(max(0.01, status_interval_s) * 1e9)
        self.generation = int(generation)
        self.raw_dump = raw_dump
        self._clock_ns = clock_ns
        self._journal = (
            PacketJournal(capture_path, raw_dump=raw_dump) if capture_path else None
        )
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._discovery_socket: socket.socket | None = None
        self._beat_socket: socket.socket | None = None
        self._status_socket: socket.socket | None = None
        self._peers: dict[tuple[int, bytes], _Peer] = {}
        self._claim_conflict: str | None = None
        self._was_first = True
        self._announced_online = False
        self._last_online_revision = -1
        self._packet_counter = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._open_sockets()
        if self.player.snapshot().online and self.claim_number:
            try:
                self._claim()
            except Exception:
                self._close_sockets()
                raise
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"prolink-network-emulator-{self.player.player_id}",
            daemon=True,
        )
        self._thread.start()
        self._log(
            logging.INFO,
            "emulator_started",
            bind=self.bind_ip,
            broadcast=self.broadcast_ip,
            mac=format_mac(self.mac),
            player=self.player.player_id,
            target=self.target_ip or "discovered_peers",
        )

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._thread = None
        self._close_sockets()

    def wake(self) -> None:
        self._wake.set()

    def _open_sockets(self) -> None:
        discovery = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        beat = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        status = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            discovery.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            discovery.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            beat.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            discovery.bind((self.bind_ip, ANNOUNCEMENT_PORT))
            beat.bind((self.bind_ip, 0))
            status.bind((self.bind_ip, 0))
            discovery.setblocking(False)
            beat.setblocking(False)
            status.setblocking(False)
        except Exception:
            for candidate in (discovery, beat, status):
                candidate.close()
            raise
        self._discovery_socket = discovery
        self._beat_socket = beat
        self._status_socket = status

    def _close_sockets(self) -> None:
        for attribute in ("_discovery_socket", "_beat_socket", "_status_socket"):
            sock = getattr(self, attribute)
            setattr(self, attribute, None)
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def _claim(self) -> None:
        """Run the captured 3/3/3/N claim chain and refuse collisions."""

        self._claim_conflict = None
        prescan_deadline = time.monotonic() + self.claim_prescan_s
        while time.monotonic() < prescan_deadline and not self._stop.is_set():
            self._receive_discovery(min(0.05, prescan_deadline - time.monotonic()))
        occupied = [
            peer
            for peer in self._peers.values()
            if peer.player_id == self.player.player_id and peer.mac != self.mac
        ]
        if occupied:
            raise PlayerNumberInUse(
                f"player {self.player.player_id} already announced by {occupied[0].ip}"
            )
        self._was_first = not self._peers

        stages: list[bytes] = [encode_hello(name=self.player.name) for _ in range(3)]
        stages.extend(
            encode_claim_mac(iteration=i, mac=self.mac, name=self.player.name)
            for i in range(1, 4)
        )
        stages.extend(
            encode_claim_ip(
                ip=self.bind_ip,
                mac=self.mac,
                player_id=self.player.player_id,
                iteration=i,
                name=self.player.name,
            )
            for i in range(1, 4)
        )
        final_count = 3 if self._was_first else 1
        stages.extend(
            encode_claim_number(
                player_id=self.player.player_id,
                iteration=i,
                name=self.player.name,
            )
            for i in range(1, final_count + 1)
        )
        for packet in stages:
            self._send(
                self._require_socket("_discovery_socket"),
                packet,
                self.broadcast_ip,
                ANNOUNCEMENT_PORT,
                discovery_packet_name(packet),
            )
            deadline = time.monotonic() + self.discovery_interval_s
            while time.monotonic() < deadline and not self._stop.is_set():
                self._receive_discovery(min(0.05, deadline - time.monotonic()))
                if self._claim_conflict:
                    raise PlayerNumberInUse(self._claim_conflict)
        self._announced_online = True
        self._log(
            logging.INFO,
            "player_number_claimed",
            player=self.player.player_id,
            final_packets=final_count,
        )

    def _run(self) -> None:
        now = self._clock_ns()
        next_keepalive = now
        next_status = now
        next_beat = now
        timing_revision = -1
        initial = self.player.snapshot(now)
        last_online = initial.online
        self._last_online_revision = initial.revision
        while not self._stop.is_set():
            self._receive_discovery(0.0)
            now = self._clock_ns()
            snapshot = self.player.snapshot(now)
            if snapshot.online != last_online:
                if snapshot.online and last_online is False and self._last_online_revision >= 0:
                    if self.claim_number:
                        try:
                            self._claim()
                        except PlayerNumberInUse as exc:
                            self.player.set_online(False)
                            self._log(logging.ERROR, "reconnect_claim_failed", reason=str(exc))
                            continue
                    next_keepalive = now
                    next_status = now
                    next_beat = now
                elif not snapshot.online:
                    self._announced_online = False
                    self._log(logging.INFO, "emulator_offline", player=snapshot.player_id)
                last_online = snapshot.online
                self._last_online_revision = snapshot.revision

            if snapshot.online:
                if now >= next_keepalive:
                    self._emit_keepalive(snapshot)
                    next_keepalive = now + self.keepalive_interval_ns
                if now >= next_status:
                    self._emit_status(snapshot)
                    next_status = now + self.status_interval_ns
                if snapshot.revision != timing_revision:
                    timing_revision = snapshot.revision
                    next_beat = now
                manual = self.player.take_manual_beat()
                if manual or (snapshot.playing and now >= next_beat):
                    beat = self.player.take_beat(now)
                    self._emit_beat(beat)
                    period_ns = int(60.0 / beat.effective_bpm * 1_000_000_000)
                    next_beat = now + max(1, period_ns)

            self._expire_peers(now)
            self._wake.wait(0.005)
            self._wake.clear()

    def _emit_keepalive(self, snapshot: EmulatedPlayerSnapshot) -> None:
        packet = encode_keep_alive(
            player_id=snapshot.player_id,
            ip=self.bind_ip,
            mac=self.mac,
            name=snapshot.name,
            peer_count=min(255, len(self._peers) + 1),
            was_first_on_network=self._was_first,
            generation=self.generation,
        )
        self._send(
            self._require_socket("_discovery_socket"),
            packet,
            self.broadcast_ip,
            ANNOUNCEMENT_PORT,
            "keep_alive",
        )
        self._announced_online = True

    def _emit_beat(self, snapshot: EmulatedPlayerSnapshot) -> None:
        packet = encode_beat(
            player_id=snapshot.player_id,
            bpm=snapshot.bpm,
            pitch_percent=snapshot.pitch_percent,
            beat_in_bar=snapshot.beat_in_bar,
            name=snapshot.name,
        )
        self._send(
            self._require_socket("_beat_socket"),
            packet,
            self.broadcast_ip,
            BEAT_PORT,
            "beat",
        )
        self._log(
            logging.INFO,
            "beat_sent",
            beat=snapshot.beat_in_bar,
            bpm=f"{snapshot.effective_bpm:.3f}",
            player=snapshot.player_id,
        )

    def _emit_status(self, snapshot: EmulatedPlayerSnapshot) -> None:
        targets = {peer.ip for peer in self._peers.values()}
        if self.target_ip:
            targets.add(self.target_ip)
        targets.discard(self.bind_ip)
        if not targets:
            return
        packet = encode_cdj_status(
            player_id=snapshot.player_id,
            bpm=snapshot.bpm,
            pitch_percent=snapshot.pitch_percent,
            beat_number=snapshot.beat_number,
            beat_in_bar=snapshot.beat_in_bar,
            playing=snapshot.playing,
            master=snapshot.master,
            sync=snapshot.sync,
            on_air=snapshot.on_air,
            track_id=snapshot.track_id,
            packet_counter=self._packet_counter,
            name=snapshot.name,
        )
        self._packet_counter = (self._packet_counter + 1) & 0xFFFF_FFFF
        for target in sorted(targets):
            self._send(
                self._require_socket("_status_socket"),
                packet,
                target,
                STATUS_PORT,
                "cdj_status",
            )

    def _receive_discovery(self, timeout: float) -> None:
        sock = self._discovery_socket
        if sock is None:
            return
        try:
            readable, _, _ = select.select((sock,), (), (), max(0.0, timeout))
        except (OSError, ValueError):
            return
        if not readable:
            return
        while True:
            try:
                data, source = sock.recvfrom(65_535)
            except BlockingIOError:
                return
            except OSError:
                return
            self._observe_discovery(bytes(data), str(source[0]))

    def _observe_discovery(self, data: bytes, source_ip: str) -> None:
        if len(data) < 0x25 or data[:10] != MAGIC:
            return
        kind = data[0x0A]
        now = self._clock_ns()
        if kind == 0x06 and len(data) == 0x36:
            mac = bytes(data[0x26:0x2C])
            player_id = data[0x24]
            if mac == self.mac:
                return
            announced_ip = str(IPv4Address(data[0x2C:0x30]))
            ip = announced_ip if announced_ip != "0.0.0.0" else source_ip
            self._peers[(player_id, mac)] = _Peer(player_id, ip, mac, now)
            if player_id == self.player.player_id:
                self._claim_conflict = (
                    f"player {player_id} announced by {ip} ({format_mac(mac)})"
                )
            return
        if kind == 0x08 and len(data) == 0x29 and data[0x24] == self.player.player_id:
            holder = str(IPv4Address(data[0x25:0x29]))
            self._claim_conflict = f"player {self.player.player_id} defended by {holder}"
            return
        proposed: int | None = None
        other_mac: bytes | None = None
        if kind == 0x02 and len(data) == 0x32:
            proposed = data[0x2E]
            other_mac = bytes(data[0x28:0x2E])
        elif kind == 0x04 and len(data) == 0x26:
            proposed = data[0x24]
        if proposed == self.player.player_id and other_mac != self.mac:
            if source_ip == self.bind_ip:
                return
            packet = encode_number_conflict(
                player_id=self.player.player_id,
                ip=self.bind_ip,
                name=self.player.name,
            )
            self._send(
                self._require_socket("_discovery_socket"),
                packet,
                source_ip,
                ANNOUNCEMENT_PORT,
                "number_conflict",
            )

    def _expire_peers(self, now_ns: int) -> None:
        for key, peer in tuple(self._peers.items()):
            if now_ns - peer.last_seen_ns >= 10_000_000_000:
                self._peers.pop(key, None)

    def _send(
        self,
        sock: socket.socket,
        data: bytes,
        destination_ip: str,
        destination_port: int,
        packet_name: str,
    ) -> None:
        try:
            sock.sendto(data, (destination_ip, destination_port))
            source_port = int(sock.getsockname()[1])
        except OSError as exc:
            self._log(
                logging.WARNING,
                "send_failed",
                destination=f"{destination_ip}:{destination_port}",
                packet=packet_name,
                reason=str(exc),
            )
            return
        now = self._clock_ns()
        if self._journal is not None:
            self._journal.record(
                timestamp_ns=now,
                direction="tx",
                source_ip=self.bind_ip,
                source_port=source_port,
                destination_ip=destination_ip,
                destination_port=destination_port,
                data=data,
                packet_name=packet_name,
            )
        elif self.raw_dump:
            LOG.debug(
                "[PROLINK TX] timestamp_ns=%d source=%s:%d destination=%s:%d "
                "type=%s length=%d hex=%s",
                now,
                self.bind_ip,
                source_port,
                destination_ip,
                destination_port,
                packet_name,
                len(data),
                data.hex(),
            )

    def _require_socket(self, attribute: str) -> socket.socket:
        sock = getattr(self, attribute)
        if sock is None:
            raise RuntimeError("network emulator is not started")
        return sock

    @staticmethod
    def _log(level: int, event: str, **fields: object) -> None:
        rendered = " ".join(f"{key}={value}" for key, value in sorted(fields.items()))
        LOG.log(
            level,
            "[PROLINK TX] event=%s%s",
            event,
            f" {rendered}" if rendered else "",
        )
