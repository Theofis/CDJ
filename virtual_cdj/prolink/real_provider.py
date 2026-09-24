"""Strictly passive, read-only PRO DJ LINK provider."""

from __future__ import annotations

import ipaddress
import logging
import queue
import select
import socket
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .capture import PacketJournal
from .models import BeatEvent, PlayerState, TrackAnalysis, TrackMetadata
from .network import broadcast_for_ip, mac_for_ip
from .packets import (
    ANNOUNCEMENT_PORT,
    BEAT_PORT,
    PROLINK_PORTS,
    STATUS_PORT,
    BeatPacket,
    CdjStatusPacket,
    DeviceAnnouncementPacket,
    PacketDecodeError,
    PrecisePositionPacket,
    UnknownPacket,
    parse_datagram,
)
from .provider import BeatEventListener, PlayerStateListener, Unsubscribe
from .wire import (
    CDJ_NAME,
    OBSERVER_PLAYER_ID,
    discovery_packet_name,
    encode_keep_alive,
    format_mac,
    parse_mac,
)

LOG = logging.getLogger("PROLINK")


@dataclass(frozen=True, slots=True)
class RawPacketDiagnostic:
    """Bounded raw evidence retained for known, unknown and malformed packets."""

    timestamp_ns: int
    local_port: int
    source_ip: str
    source_port: int
    packet_type: int | None
    outcome: str
    detail: str
    data: bytes


@dataclass(frozen=True, slots=True)
class _Datagram:
    data: bytes
    source: tuple[str, int]
    local_port: int
    received_ns: int


@dataclass(frozen=True, slots=True)
class _NetworkIssue:
    local_port: int
    detail: str
    received_ns: int


Incoming = _Datagram | _NetworkIssue


def _auto_bind_address() -> str:
    """Prefer one unambiguous link-local NIC, otherwise listen on all NICs."""

    candidates: set[str] = set()
    try:
        records = socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM
        )
    except OSError:
        records = []
    for record in records:
        address = record[4][0]
        try:
            parsed = ipaddress.IPv4Address(address)
        except ipaddress.AddressValueError:
            continue
        if not parsed.is_loopback and not parsed.is_unspecified:
            candidates.add(str(parsed))
    link_local = sorted(
        address
        for address in candidates
        if ipaddress.IPv4Address(address).is_link_local
    )
    return link_local[0] if len(link_local) == 1 else "0.0.0.0"


def resolve_bind_address(value: str | None) -> str:
    """Resolve ``auto`` or validate an explicit local IPv4 bind address."""

    if value is None or value.strip().lower() in ("", "auto"):
        return _auto_bind_address()
    try:
        return str(ipaddress.IPv4Address(value.strip()))
    except ipaddress.AddressValueError as exc:
        raise ValueError(
            "PRO DJ LINK interface must be 'auto' or a local IPv4 address"
        ) from exc


class RealProLinkProvider:
    """Receive PRO DJ LINK UDP traffic in passive or observer-presence mode.

    The socket thread only timestamps datagrams and queues bytes. Decoding,
    state mutation and listener delivery happen in :meth:`poll`, normally on
    the application's GUI thread, exactly like ``SimulatorProLinkProvider``.

    ``presence="passive"`` is the Phase-2 default and sends no packet at all.
    ``presence="peer"`` adds only the evidenced observer-number-7 keepalive;
    it performs no player-slot claim and sends no status or control packet.
    """

    def __init__(
        self,
        bind_address: str | None = "auto",
        *,
        stale_after_s: float = 10.0,
        forget_after_s: float = 30.0,
        reconnect_s: float = 1.0,
        raw_history_size: int = 256,
        presence: str = "passive",
        presence_mac: str | None = None,
        presence_target_ip: str | None = None,
        presence_prescan_s: float = 2.5,
        presence_interval_s: float = 2.0,
        presence_generation: int = 0x64,
        capture_path: str | Path | None = None,
        raw_dump: bool = False,
        socket_factory: Callable[..., socket.socket] = socket.socket,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        enable_network: bool = True,
    ) -> None:
        self.bind_address = resolve_bind_address(bind_address)
        self.stale_after_ns = int(max(0.05, stale_after_s) * 1_000_000_000)
        self.forget_after_ns = int(
            max(stale_after_s + 0.05, forget_after_s) * 1_000_000_000
        )
        self.reconnect_s = max(0.05, reconnect_s)
        self._socket_factory = socket_factory
        self._clock_ns = clock_ns
        self._enable_network = enable_network
        selected_presence = presence.strip().lower()
        if selected_presence not in ("passive", "peer"):
            raise ValueError("PRO DJ LINK presence must be 'passive' or 'peer'")
        self.presence = selected_presence
        self._presence_prescan_ns = int(max(0.0, presence_prescan_s) * 1e9)
        self._presence_interval_ns = int(max(0.05, presence_interval_s) * 1e9)
        if not 0 <= int(presence_generation) <= 255:
            raise ValueError("presence generation must fit in one byte")
        self._presence_generation = int(presence_generation)
        self._presence_mac: bytes | None = None
        self._presence_error: str | None = None
        self._presence_target_ip: str | None = None
        if self.presence == "peer":
            if self.bind_address == "0.0.0.0":
                self._presence_error = (
                    "peer presence requires an explicit --prolink-interface IPv4"
                )
            else:
                selected_mac = mac_for_ip(self.bind_address, presence_mac)
                if selected_mac is None:
                    self._presence_error = (
                        "could not determine selected NIC MAC; pass --prolink-mac"
                    )
                else:
                    self._presence_mac = parse_mac(selected_mac)
                self._presence_target_ip = (
                    str(ipaddress.IPv4Address(presence_target_ip))
                    if presence_target_ip
                    else broadcast_for_ip(self.bind_address)
                )
        self._presence_peers: dict[bytes, tuple[int, str, int]] = {}
        self._presence_collision = threading.Event()
        self._presence_collision_reported = False
        self._journal = (
            PacketJournal(capture_path, raw_dump=raw_dump) if capture_path else None
        )
        self._raw_dump = bool(raw_dump)

        self._players: dict[int, PlayerState] = {}
        self._last_seen: dict[int, int] = {}
        self._event_beat_numbers: dict[int, int] = {}
        self._state_listeners: list[PlayerStateListener] = []
        self._beat_listeners: list[BeatEventListener] = []
        self._incoming: queue.SimpleQueue[Incoming] = queue.SimpleQueue()
        self._raw_packets: deque[RawPacketDiagnostic] = deque(
            maxlen=max(1, int(raw_history_size))
        )
        self._raw_lock = threading.Lock()

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sockets: dict[int, socket.socket] = {}
        self._sockets_lock = threading.Lock()

    def start(self) -> None:
        if not self._enable_network:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="prolink-read-only-receiver",
            daemon=True,
        )
        self._thread.start()
        self._log(
            logging.INFO,
            "receiver_started",
            bind=self.bind_address,
            ports=",".join(str(port) for port in PROLINK_PORTS),
            mode=("passive_read_only" if self.presence == "passive" else "peer_observer"),
        )
        if self._presence_error:
            self._incoming.put(
                _NetworkIssue(ANNOUNCEMENT_PORT, self._presence_error, self._clock_ns())
            )

    def stop(self) -> None:
        self._stop.set()
        self._close_sockets()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def get_players(self) -> tuple[PlayerState, ...]:
        return tuple(self._players[key] for key in sorted(self._players))

    def subscribe_player_state(self, listener: PlayerStateListener) -> Unsubscribe:
        return self._subscribe(self._state_listeners, listener)

    def subscribe_beat_events(self, listener: BeatEventListener) -> Unsubscribe:
        return self._subscribe(self._beat_listeners, listener)

    @staticmethod
    def _subscribe(listeners: list, listener: Callable) -> Unsubscribe:
        listeners.append(listener)

        def unsubscribe() -> None:
            if listener in listeners:
                listeners.remove(listener)

        return unsubscribe

    def get_track_metadata(self, track_id: str) -> TrackMetadata | None:
        return None

    def get_track_analysis(self, track_id: str) -> TrackAnalysis | None:
        return None

    def get_raw_diagnostics(self) -> tuple[RawPacketDiagnostic, ...]:
        """Return the bounded packet journal, including full original bytes."""

        with self._raw_lock:
            return tuple(self._raw_packets)

    def ingest_datagram(
        self,
        data: bytes,
        *,
        local_port: int,
        source: tuple[str, int] = ("0.0.0.0", 0),
        received_ns: int | None = None,
    ) -> None:
        """Queue a captured datagram for deterministic replay and diagnostics."""

        self._incoming.put(
            _Datagram(
                data=bytes(data),
                source=(str(source[0]), int(source[1])),
                local_port=int(local_port),
                received_ns=(
                    self._clock_ns() if received_ns is None else int(received_ns)
                ),
            )
        )

    def poll(self) -> None:
        # Bound work per GUI tick if a busy network produces a burst.
        for _ in range(512):
            try:
                item = self._incoming.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, _NetworkIssue):
                self._record_network_issue(item)
            else:
                self._process_datagram(item)
        self._expire_players(self._clock_ns())

    def _process_datagram(self, datagram: _Datagram) -> None:
        packet_type = datagram.data[0x0A] if len(datagram.data) > 0x0A else None
        packet_name = self._packet_name(datagram.local_port, datagram.data)
        if self._journal is not None:
            self._journal.record(
                timestamp_ns=datagram.received_ns,
                direction="rx",
                source_ip=datagram.source[0],
                source_port=datagram.source[1],
                destination_ip=(
                    self.bind_address
                    if self.bind_address != "0.0.0.0"
                    else "unknown"
                ),
                destination_port=datagram.local_port,
                data=datagram.data,
                packet_name=packet_name,
            )
        elif self._raw_dump:
            LOG.debug(
                "[PROLINK RX] timestamp_ns=%d source=%s:%d destination=%s:%d "
                "type=%s length=%d hex=%s",
                datagram.received_ns,
                datagram.source[0],
                datagram.source[1],
                self.bind_address,
                datagram.local_port,
                packet_name,
                len(datagram.data),
                datagram.data.hex(),
            )
        try:
            packet = parse_datagram(datagram.local_port, datagram.data)
        except (PacketDecodeError, ValueError) as exc:
            self._record_raw(datagram, packet_type, "malformed", str(exc))
            self._log(
                logging.WARNING,
                "packet_rejected",
                port=datagram.local_port,
                source=datagram.source[0],
                size=len(datagram.data),
                reason=str(exc),
            )
            return

        if isinstance(packet, UnknownPacket):
            self._record_raw(
                datagram, packet.packet_type, "unknown", "unsupported packet kind"
            )
            self._log(
                logging.DEBUG,
                "packet_unknown",
                port=datagram.local_port,
                packet_type=f"0x{packet.packet_type:02x}",
                size=len(datagram.data),
            )
            return

        self._record_raw(datagram, packet_type, "parsed", type(packet).__name__)
        if isinstance(packet, DeviceAnnouncementPacket):
            self._apply_announcement(packet, datagram)
        elif isinstance(packet, BeatPacket):
            self._apply_beat(packet, datagram)
        elif isinstance(packet, PrecisePositionPacket):
            self._apply_precise_position(packet, datagram)
        elif isinstance(packet, CdjStatusPacket):
            self._apply_status(packet, datagram)

    def _state_for(
        self, player_id: int, datagram: _Datagram, *, name: str | None = None
    ) -> PlayerState:
        current = self._players.get(player_id)
        if current is None:
            return PlayerState(
                player_id=player_id,
                device_name=name,
                ip_address=datagram.source[0],
                online=True,
                last_update_ns=datagram.received_ns,
                timing_source="prolink-observed",
            )
        projected = self._project_position(current, datagram.received_ns)
        return replace(
            projected,
            device_name=name or current.device_name,
            ip_address=(
                datagram.source[0]
                if datagram.source[0] != "0.0.0.0"
                else current.ip_address
            ),
            online=True,
            last_update_ns=datagram.received_ns,
        )

    @staticmethod
    def _project_position(state: PlayerState, now_ns: int) -> PlayerState:
        if (
            not state.position_valid
            or state.position_ms is None
            or not state.playing
            or state.original_bpm is None
            or state.effective_bpm is None
            or state.original_bpm <= 0
            or now_ns <= state.last_update_ns
        ):
            return state
        elapsed_ms = (now_ns - state.last_update_ns) / 1_000_000.0
        position = state.position_ms + (
            elapsed_ms * state.effective_bpm / state.original_bpm
        )
        if state.duration_ms is not None:
            position = min(position, float(state.duration_ms))
        return replace(state, position_ms=position)

    def _apply_announcement(
        self, packet: DeviceAnnouncementPacket, datagram: _Datagram
    ) -> None:
        if (
            self._presence_mac is not None
            and packet.player_id == OBSERVER_PLAYER_ID
            and packet.hardware_address == format_mac(self._presence_mac)
        ):
            return
        state = self._state_for(
            packet.player_id, datagram, name=packet.device_name
        )
        announced_ip = packet.announced_ip
        if announced_ip != "0.0.0.0":
            state = replace(state, ip_address=announced_ip)
        state = replace(
            state,
            device_type=packet.device_type or state.device_type,
            timing_source="prolink-announcement",
        )
        self._publish_state(state, datagram.received_ns)

    def _apply_beat(self, packet: BeatPacket, datagram: _Datagram) -> None:
        state = self._state_for(
            packet.player_id, datagram, name=packet.device_name
        )
        # Captured Nexus CDJs emit beats only while playing an analysed track.
        # Mixers emit them continuously, so never infer transport for a mixer.
        from_player = state.device_type != "MIXER" and packet.player_id < 33
        state = replace(
            state,
            playing=True if from_player else state.playing,
            paused=False if from_player else state.paused,
            original_bpm=packet.original_bpm,
            effective_bpm=packet.effective_bpm,
            pitch_percent=packet.pitch_percent,
            beat_in_bar=(packet.beat_in_bar if from_player else state.beat_in_bar),
            timing_source="prolink-beat",
        )

        if (
            from_player
            and packet.effective_bpm is not None
            and packet.beat_in_bar is not None
        ):
            last_event = self._event_beat_numbers.get(packet.player_id)
            observed = state.beat_number
            if observed is not None and (last_event is None or observed > last_event):
                event_number = observed
            else:
                # Type-0x28 has bar phase but no absolute track beat. The
                # provider boundary requires an integer, so absent status this
                # is explicitly a session-local event ordinal, never a decoded
                # packet field.
                event_number = 1 if last_event is None else last_event + 1
            self._event_beat_numbers[packet.player_id] = event_number
            state = replace(state, beat_number=event_number)
            self._publish_state(state, datagram.received_ns)
            event = BeatEvent(
                player_id=packet.player_id,
                timestamp_ns=datagram.received_ns,
                beat_number=event_number,
                beat_in_bar=packet.beat_in_bar,
                bpm=packet.effective_bpm,
                position_ms=state.position_ms if state.position_valid else None,
                is_master=state.is_master,
            )
            for listener in tuple(self._beat_listeners):
                listener(event)
        else:
            self._publish_state(state, datagram.received_ns)
            self._log(
                logging.DEBUG,
                "beat_without_usable_grid",
                player=packet.player_id,
            )

    def _apply_precise_position(
        self, packet: PrecisePositionPacket, datagram: _Datagram
    ) -> None:
        state = self._state_for(
            packet.player_id, datagram, name=packet.device_name
        )
        state = replace(
            state,
            device_type=state.device_type or "CDJ",
            duration_ms=packet.duration_ms,
            position_ms=float(packet.position_ms),
            position_valid=True,
            original_bpm=packet.original_bpm,
            effective_bpm=packet.effective_bpm,
            pitch_percent=packet.pitch_percent,
            timing_source="prolink-precise-position",
        )
        self._publish_state(state, datagram.received_ns)

    def _apply_status(self, packet: CdjStatusPacket, datagram: _Datagram) -> None:
        previous = self._players.get(packet.player_id)
        state = self._state_for(
            packet.player_id, datagram, name=packet.device_name
        )
        track_id = None
        if packet.rekordbox_track_id and packet.track_source_player:
            track_id = (
                f"prolink:{packet.track_source_player}:"
                f"{packet.track_source_slot}:{packet.track_type}:"
                f"{packet.rekordbox_track_id}"
            )
        track_changed = previous is not None and previous.track_id != track_id
        if track_changed:
            self._event_beat_numbers.pop(packet.player_id, None)

        state = replace(
            state,
            device_type=state.device_type or "CDJ",
            playing=packet.playing,
            paused=packet.paused,
            reverse=packet.reverse,
            track_id=track_id,
            track_source_player=packet.track_source_player,
            position_ms=None if track_changed else state.position_ms,
            position_valid=False if track_changed else state.position_valid,
            duration_ms=None if track_changed else state.duration_ms,
            original_bpm=packet.original_bpm,
            effective_bpm=packet.effective_bpm,
            pitch_percent=packet.pitch_percent,
            beat_number=packet.beat_number,
            beat_in_bar=packet.beat_in_bar,
            sync_enabled=packet.sync_enabled,
            is_master=packet.is_master,
            on_air=packet.on_air,
            timing_source="prolink-status",
        )
        if packet.beat_number is not None:
            current = self._event_beat_numbers.get(packet.player_id)
            if current is None or packet.beat_number > current:
                self._event_beat_numbers[packet.player_id] = packet.beat_number
        if packet.repeated_player_id not in (0, packet.player_id):
            self._log(
                logging.WARNING,
                "status_device_number_mismatch",
                header=packet.player_id,
                body=packet.repeated_player_id,
            )
        self._publish_state(state, datagram.received_ns)

    def _publish_state(self, state: PlayerState, seen_ns: int) -> None:
        previous = self._players.get(state.player_id)
        self._players[state.player_id] = state
        self._last_seen[state.player_id] = seen_ns
        if previous is None or previous.online != state.online:
            self._log(
                logging.INFO,
                "device_online" if state.online else "device_offline",
                player=state.player_id,
                name=state.device_name or "unknown",
                ip=state.ip_address or "unknown",
            )
        for listener in tuple(self._state_listeners):
            listener(state)

    def _expire_players(self, now_ns: int) -> None:
        for player_id, last_seen in tuple(self._last_seen.items()):
            age = now_ns - last_seen
            state = self._players.get(player_id)
            if state is not None and state.online and age >= self.stale_after_ns:
                stale = replace(
                    state,
                    online=False,
                    playing=False,
                    paused=True,
                    position_valid=False,
                    is_master=False,
                    timing_source="prolink-stale",
                )
                self._players[player_id] = stale
                self._event_beat_numbers.pop(player_id, None)
                self._log(
                    logging.INFO,
                    "device_stale",
                    player=player_id,
                    age_ms=age // 1_000_000,
                )
                for listener in tuple(self._state_listeners):
                    listener(stale)
            if age >= self.forget_after_ns:
                self._players.pop(player_id, None)
                self._last_seen.pop(player_id, None)
                self._event_beat_numbers.pop(player_id, None)
                self._log(logging.INFO, "device_forgotten", player=player_id)

    def _record_raw(
        self,
        datagram: _Datagram,
        packet_type: int | None,
        outcome: str,
        detail: str,
    ) -> None:
        diagnostic = RawPacketDiagnostic(
            timestamp_ns=datagram.received_ns,
            local_port=datagram.local_port,
            source_ip=datagram.source[0],
            source_port=datagram.source[1],
            packet_type=packet_type,
            outcome=outcome,
            detail=detail,
            data=datagram.data,
        )
        with self._raw_lock:
            self._raw_packets.append(diagnostic)

    def _record_network_issue(self, issue: _NetworkIssue) -> None:
        diagnostic = RawPacketDiagnostic(
            timestamp_ns=issue.received_ns,
            local_port=issue.local_port,
            source_ip="",
            source_port=0,
            packet_type=None,
            outcome="network_error",
            detail=issue.detail,
            data=b"",
        )
        with self._raw_lock:
            self._raw_packets.append(diagnostic)
        self._log(
            logging.WARNING,
            "socket_error",
            port=issue.local_port,
            reason=issue.detail,
        )

    def _run(self) -> None:
        sockets: dict[int, socket.socket] = {}
        next_open = 0.0
        presence_bound_ns: int | None = None
        presence_next_ns = 0
        presence_was_first = False
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_open:
                for port in PROLINK_PORTS:
                    if port in sockets:
                        continue
                    try:
                        sock = self._open_socket(port)
                    except Exception as exc:
                        self._incoming.put(
                            _NetworkIssue(port, str(exc), self._clock_ns())
                        )
                    else:
                        sockets[port] = sock
                        with self._sockets_lock:
                            self._sockets[port] = sock
                        self._log(
                            logging.INFO,
                            "socket_bound",
                            bind=self.bind_address,
                            port=port,
                        )
                        if (
                            port == ANNOUNCEMENT_PORT
                            and self.presence == "peer"
                            and self._presence_error is None
                        ):
                            presence_bound_ns = self._clock_ns()
                            presence_next_ns = (
                                presence_bound_ns + self._presence_prescan_ns
                            )
                next_open = now + self.reconnect_s

            if not sockets:
                self._stop.wait(min(self.reconnect_s, 0.25))
                continue
            try:
                ready, _, _ = select.select(tuple(sockets.values()), (), (), 0.25)
            except (OSError, ValueError) as exc:
                self._incoming.put(
                    _NetworkIssue(0, f"select failed: {exc}", self._clock_ns())
                )
                self._close_socket_map(sockets)
                sockets.clear()
                continue
            for sock in ready:
                port = next(
                    (number for number, candidate in sockets.items() if candidate is sock),
                    0,
                )
                try:
                    data, source = sock.recvfrom(65_535)
                except OSError as exc:
                    self._incoming.put(
                        _NetworkIssue(port, str(exc), self._clock_ns())
                    )
                    self._drop_socket(sockets, port)
                    continue
                self._incoming.put(
                    _Datagram(
                        data=bytes(data),
                        source=(str(source[0]), int(source[1])),
                        local_port=port,
                        received_ns=self._clock_ns(),
                    )
                )
                if port == ANNOUNCEMENT_PORT:
                    self._observe_presence(data, str(source[0]))

            current_ns = self._clock_ns()
            if (
                self.presence == "peer"
                and self._presence_error is None
                and not self._presence_collision.is_set()
                and presence_bound_ns is not None
                and current_ns >= presence_next_ns
                and ANNOUNCEMENT_PORT in sockets
            ):
                if presence_next_ns == presence_bound_ns + self._presence_prescan_ns:
                    self._expire_presence_peers(current_ns)
                    presence_was_first = not self._presence_peers
                self._send_presence(
                    sockets[ANNOUNCEMENT_PORT],
                    current_ns,
                    presence_was_first,
                )
                presence_next_ns = current_ns + self._presence_interval_ns
        self._close_socket_map(sockets)

    def _open_socket(self, port: int) -> socket.socket:
        sock = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if port == ANNOUNCEMENT_PORT:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            if hasattr(socket, "SIO_UDP_CONNRESET") and hasattr(sock, "ioctl"):
                try:
                    sock.ioctl(socket.SIO_UDP_CONNRESET, False)
                except OSError:
                    pass
            sock.bind((self.bind_address, port))
            sock.setblocking(False)
            return sock
        except Exception:
            try:
                sock.close()
            except OSError:
                pass
            raise

    def _observe_presence(self, data: bytes, source_ip: str) -> None:
        """Track announced peers and stop safely if observer 7 is occupied."""

        if len(data) != 0x36 or data[:10] != b"Qspt1WmJOL" or data[0x0A] != 0x06:
            return
        mac = bytes(data[0x26:0x2C])
        if self._presence_mac is not None and mac == self._presence_mac:
            return
        player_id = data[0x24]
        announced_ip = str(ipaddress.IPv4Address(data[0x2C:0x30]))
        peer_ip = announced_ip if announced_ip != "0.0.0.0" else source_ip
        self._presence_peers[mac] = (player_id, peer_ip, self._clock_ns())
        if (
            self.presence == "peer"
            and player_id == OBSERVER_PLAYER_ID
            and not self._presence_collision.is_set()
        ):
            self._presence_collision.set()
            if not self._presence_collision_reported:
                self._presence_collision_reported = True
                self._incoming.put(
                    _NetworkIssue(
                        ANNOUNCEMENT_PORT,
                        f"observer number 7 already announced by {peer_ip}; "
                        "presence disabled",
                        self._clock_ns(),
                    )
                )

    def _expire_presence_peers(self, now_ns: int) -> None:
        for mac, (_, _, seen_ns) in tuple(self._presence_peers.items()):
            if now_ns - seen_ns >= 10_000_000_000:
                self._presence_peers.pop(mac, None)

    def _send_presence(
        self, sock: socket.socket, timestamp_ns: int, was_first: bool
    ) -> None:
        if self._presence_mac is None or self._presence_target_ip is None:
            return
        self._expire_presence_peers(timestamp_ns)
        packet = encode_keep_alive(
            player_id=OBSERVER_PLAYER_ID,
            ip=self.bind_address,
            mac=self._presence_mac,
            name=CDJ_NAME,
            peer_count=min(255, len(self._presence_peers) + 1),
            was_first_on_network=was_first,
            generation=self._presence_generation,
        )
        try:
            sock.sendto(packet, (self._presence_target_ip, ANNOUNCEMENT_PORT))
        except OSError as exc:
            self._incoming.put(
                _NetworkIssue(
                    ANNOUNCEMENT_PORT,
                    f"presence send failed: {exc}",
                    timestamp_ns,
                )
            )
            return
        self._log(
            logging.INFO,
            "presence_sent",
            destination=self._presence_target_ip,
            mode="observer",
            player=OBSERVER_PLAYER_ID,
        )
        if self._journal is not None:
            self._journal.record(
                timestamp_ns=timestamp_ns,
                direction="tx",
                source_ip=self.bind_address,
                source_port=ANNOUNCEMENT_PORT,
                destination_ip=self._presence_target_ip,
                destination_port=ANNOUNCEMENT_PORT,
                data=packet,
                packet_name="observer_keep_alive",
            )
        elif self._raw_dump:
            LOG.debug(
                "[PROLINK TX] timestamp_ns=%d source=%s:%d destination=%s:%d "
                "type=observer_keep_alive length=%d hex=%s",
                timestamp_ns,
                self.bind_address,
                ANNOUNCEMENT_PORT,
                self._presence_target_ip,
                ANNOUNCEMENT_PORT,
                len(packet),
                packet.hex(),
            )

    @staticmethod
    def _packet_name(port: int, data: bytes) -> str:
        if port == ANNOUNCEMENT_PORT:
            return discovery_packet_name(data)
        packet_type = data[0x0A] if len(data) > 0x0A else None
        return {
            (BEAT_PORT, 0x28): "beat",
            (BEAT_PORT, 0x0B): "precise_position",
            (STATUS_PORT, 0x0A): "cdj_status",
        }.get((port, packet_type), "malformed" if packet_type is None else f"unknown_0x{packet_type:02x}")

    def _drop_socket(self, sockets: dict[int, socket.socket], port: int) -> None:
        sock = sockets.pop(port, None)
        with self._sockets_lock:
            self._sockets.pop(port, None)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _close_socket_map(self, sockets: dict[int, socket.socket]) -> None:
        for port in tuple(sockets):
            self._drop_socket(sockets, port)

    def _close_sockets(self) -> None:
        with self._sockets_lock:
            sockets = tuple(self._sockets.values())
            self._sockets.clear()
        for sock in sockets:
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _log(level: int, event: str, **fields: object) -> None:
        rendered = " ".join(
            f"{key}={value}" for key, value in sorted(fields.items())
        )
        LOG.log(level, "[PROLINK] event=%s%s", event, f" {rendered}" if rendered else "")
