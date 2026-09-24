"""Reconnectender TCP-Client für den internen ProLink-Simulator."""

from __future__ import annotations

import logging
import queue
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from .ipc import MAX_MESSAGE_BYTES, MessageError, decode_message
from .models import BeatEvent, PlayerState, TrackAnalysis, TrackMetadata
from .provider import BeatEventListener, PlayerStateListener, Unsubscribe

LOG = logging.getLogger("IPC")


class SimulatorProLinkProvider:
    """Adapter vom Simulatorprozess auf die normale Provider-API.

    Der Netzwerkthread legt ausschließlich rohe Nachrichten und
    Verbindungsereignisse in eine Queue. ``poll()`` dekodiert und benachrichtigt
    Listener im aufrufenden Thread; dadurch berührt der Netzwerkthread weder
    GUI noch Master-/Sync-Zustand.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 17600,
        *,
        reconnect_s: float = 0.25,
        connect_timeout_s: float = 0.25,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.reconnect_s = max(0.01, reconnect_s)
        self.connect_timeout_s = max(0.01, connect_timeout_s)
        self._players: dict[int, PlayerState] = {}
        self._state_listeners: list[PlayerStateListener] = []
        self._beat_listeners: list[BeatEventListener] = []
        self._incoming: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._socket_lock = threading.Lock()
        self.connected = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="prolink-simulator-client",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._socket_lock:
            sock = self._socket
            self._socket = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        # Den Offline-Zustand noch im aufrufenden Thread zustellen.
        self._incoming.put(None)
        self.poll()

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

    def poll(self) -> None:
        while True:
            try:
                item = self._incoming.get_nowait()
            except queue.Empty:
                return
            if item is None:
                self._mark_disconnected()
                continue
            try:
                message = decode_message(item)
            except MessageError as exc:
                LOG.warning("[IPC] Ungültige Simulatornachricht verworfen: %s", exc)
                continue

            received_ns = time.monotonic_ns()
            if isinstance(message, PlayerState):
                previous = self._players.get(message.player_id)
                self._players[message.player_id] = message
                if previous is None or previous.online != message.online:
                    status = "connected" if message.online else "disconnected"
                    LOG.info("[PROLINK] Player %s %s", message.player_id, status)
                for listener in tuple(self._state_listeners):
                    listener(message)
            else:
                # Zeitdomänen verschiedener Prozesse nicht vermischen. Der
                # Simulatorzeitstempel wurde beim Empfang diagnostisch
                # geprüft; intern gilt die lokale monotone Ankunftszeit.
                event = replace(message, timestamp_ns=received_ns)
                for listener in tuple(self._beat_listeners):
                    listener(event)

    def _mark_disconnected(self) -> None:
        if self.connected:
            LOG.info("[IPC] Simulatorverbindung beendet")
        self.connected = False
        now = time.monotonic_ns()
        for player_id, state in tuple(self._players.items()):
            if not state.online:
                continue
            offline = replace(
                state,
                online=False,
                playing=False,
                paused=True,
                position_valid=False,
                is_master=False,
                last_update_ns=now,
                timing_source="simulator-disconnected",
            )
            self._players[player_id] = offline
            for listener in tuple(self._state_listeners):
                listener(offline)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                sock = socket.create_connection(
                    (self.host, self.port), timeout=self.connect_timeout_s
                )
                sock.settimeout(0.25)
                with self._socket_lock:
                    self._socket = sock
                self.connected = True
                LOG.info("[IPC] Mit Simulator %s:%s verbunden", self.host, self.port)
                self._read_socket(sock)
            except OSError:
                pass
            finally:
                with self._socket_lock:
                    sock = self._socket
                    self._socket = None
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
                self._incoming.put(None)
            self._stop.wait(self.reconnect_s)

    def _read_socket(self, sock: socket.socket) -> None:
        buffer = bytearray()
        while not self._stop.is_set():
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                return
            buffer.extend(chunk)
            if len(buffer) > MAX_MESSAGE_BYTES * 2:
                LOG.warning("[IPC] Empfangspuffer verworfen: keine gültige Zeilengrenze")
                buffer.clear()
                continue
            while b"\n" in buffer:
                raw, _, rest = buffer.partition(b"\n")
                buffer = bytearray(rest)
                if raw:
                    self._incoming.put(bytes(raw))
