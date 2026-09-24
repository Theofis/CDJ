"""Nur-lesender localhost-TCP-Server des internen Simulators."""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import TYPE_CHECKING

from ..prolink.ipc import encode_beat_event, encode_player_state
from .fake_player import FakePlayer

if TYPE_CHECKING:
    from .scenarios import ScenarioRunner

LOG = logging.getLogger("SIMULATOR")


class SimulatorServer:
    """Sendet FakePlayer-State und BeatEvents an beliebig viele Clients."""

    def __init__(
        self,
        player: FakePlayer,
        host: str = "127.0.0.1",
        port: int = 17600,
        *,
        state_hz: float = 20.0,
        tick_hz: float = 100.0,
        scenario: ScenarioRunner | None = None,
    ) -> None:
        self.player = player
        self.host = host
        self.port = int(port)
        self.state_interval_s = 1.0 / max(1.0, state_hz)
        self.tick_interval_s = 1.0 / max(10.0, tick_hz)
        self.scenario = scenario
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._listener: socket.socket | None = None
        self._startup_error: OSError | None = None
        self._clients: list[socket.socket] = []
        self._clients_lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._ready.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._run, name="prolink-simulator-server", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=2.0):
            raise RuntimeError("Simulatorserver konnte nicht gestartet werden")
        if self._startup_error is not None:
            raise RuntimeError(
                f"Simulatorserver konnte {self.host}:{self.port} nicht binden"
            ) from self._startup_error

    def stop(self) -> None:
        self._stop.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        self._close_clients()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            try:
                listener.bind((self.host, self.port))
            except OSError as exc:
                self._startup_error = exc
                return
            self.port = int(listener.getsockname()[1])
            listener.listen()
            listener.setblocking(False)
            self._listener = listener
            self._ready.set()
            LOG.info("[SIMULATOR] Server listening on %s:%s", self.host, self.port)
            next_state = 0.0
            if self.scenario is not None:
                self.scenario.start(time.monotonic())
            while not self._stop.is_set():
                self._accept_all(listener)
                if self.scenario is not None:
                    self.scenario.tick(time.monotonic())
                state, events = self.player.advance()
                for event in events:
                    self._broadcast(encode_beat_event(event))
                now = time.monotonic()
                if now >= next_state:
                    self._broadcast(encode_player_state(state))
                    next_state = now + self.state_interval_s
                self._stop.wait(self.tick_interval_s)
        finally:
            self._ready.set()
            try:
                listener.close()
            except OSError:
                pass
            self._listener = None
            self._close_clients()

    def _accept_all(self, listener: socket.socket) -> None:
        while True:
            try:
                client, _address = listener.accept()
            except BlockingIOError:
                return
            except OSError:
                return
            client.settimeout(0.5)
            with self._clients_lock:
                self._clients.append(client)
            LOG.info("[SIMULATOR] Client connected")
            # Ein neuer Client wartet nicht erst auf den nächsten Status-Tick.
            try:
                client.sendall(encode_player_state(self.player.snapshot()))
            except OSError:
                self._drop(client)

    def _broadcast(self, payload: bytes) -> None:
        with self._clients_lock:
            clients = tuple(self._clients)
        for client in clients:
            try:
                client.sendall(payload)
            except OSError:
                self._drop(client)

    def _drop(self, client: socket.socket) -> None:
        with self._clients_lock:
            if client in self._clients:
                self._clients.remove(client)
        try:
            client.close()
        except OSError:
            pass

    def _close_clients(self) -> None:
        with self._clients_lock:
            clients = self._clients
            self._clients = []
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass
