"""Start mit ``python -m virtual_cdj.simulator``."""

from __future__ import annotations

import argparse
import logging
import signal
import time

from .fake_player import FakePlayer
from .server import SimulatorServer
from .scenarios import build_scenario


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interner ProLink-Simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17600)
    parser.add_argument("--player-id", type=int, default=1)
    parser.add_argument("--headless", action="store_true", help="ohne Tk-Fenster")
    parser.add_argument("--play", action="store_true", help="sofort abspielen")
    parser.add_argument("--master", action="store_true", help="sofort Master sein")
    parser.add_argument("--bpm", type=float, default=154.0)
    parser.add_argument(
        "--scenario",
        choices=("normal", "bpm-change", "disconnect", "track-change", "sync-toggle"),
        default=None,
        help="reproduzierbares automatisches Szenario",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    player = FakePlayer(player_id=args.player_id, effective_bpm=args.bpm)
    player.set_master(args.master)
    if args.play:
        player.play()
    scenario = build_scenario(args.scenario, player) if args.scenario else None
    server = SimulatorServer(player, args.host, args.port, scenario=scenario)
    server.start()
    try:
        if args.headless:
            signal.signal(signal.SIGINT, lambda *_args: server.stop())
            while server._thread is not None and server._thread.is_alive():  # noqa: SLF001
                time.sleep(0.2)
        else:
            from .ui import SimulatorWindow

            window = SimulatorWindow(player, f"{args.host}:{server.port}")
            window.mainloop()
    finally:
        server.stop()


if __name__ == "__main__":
    main()
