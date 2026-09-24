"""Command line entry point for the UDP PRO DJ LINK network emulator."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from ..network import resolve_network_interface
from .emulator import EmulatedPlayer, ProLinkNetworkEmulator


def _on_off(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in ("on", "1", "true", "yes"):
        return True
    if lowered in ("off", "0", "false", "no"):
        return False
    raise ValueError("expected on or off")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="UDP-only, evidence-backed PRO DJ LINK network emulator"
    )
    parser.add_argument("--interface", default="auto", help="interface name or IPv4")
    parser.add_argument("--bind-ip", default=None, help="local Ethernet IPv4")
    parser.add_argument(
        "--broadcast-ip",
        default=None,
        help="override interface broadcast (useful for localhost tests)",
    )
    parser.add_argument(
        "--target-ip",
        default=None,
        help="unicast PlayerStatus destination (Laptop A)",
    )
    parser.add_argument("--mac", default=None, help="emulated six-byte MAC address")
    parser.add_argument("--player-number", type=int, default=1, choices=range(1, 5))
    parser.add_argument("--name", default="CDJ-2000nexus")
    parser.add_argument("--bpm", type=float, default=154.0)
    parser.add_argument("--pitch", type=float, default=0.0, metavar="PERCENT")
    parser.add_argument("--track-id", type=int, default=1)
    parser.add_argument("--play", dest="playing", action="store_true")
    parser.add_argument("--pause", dest="playing", action="store_false")
    parser.add_argument("--master", action="store_true")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--on-air", action="store_true")
    parser.add_argument("--offline", dest="online", action="store_false")
    parser.add_argument(
        "--no-claim",
        dest="claim",
        action="store_false",
        help="skip boot claim (only for isolated parser/localhost tests)",
    )
    parser.add_argument("--capture", default=None, metavar="JSONL")
    parser.add_argument("--raw-dump", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--duration", type=float, default=None, metavar="SECONDS")
    parser.add_argument("--debug", action="store_true")
    parser.set_defaults(playing=False, online=True, claim=True)
    return parser.parse_args(argv)


def _print_state(player: EmulatedPlayer) -> None:
    state = player.snapshot()
    print(
        f"Player {state.player_id} {'ONLINE' if state.online else 'OFFLINE'} "
        f"{'PLAY' if state.playing else 'PAUSE'} BPM={state.bpm:.2f} "
        f"pitch={state.pitch_percent:+.2f}% effective={state.effective_bpm:.3f} "
        f"beat={state.beat_in_bar} position={state.position_ms / 1000:.3f}s "
        f"master={state.master} sync={state.sync} on_air={state.on_air} "
        f"track={state.track_id}",
        flush=True,
    )


def _interactive(player: EmulatedPlayer, emulator: ProLinkNetworkEmulator) -> None:
    print(
        "Commands: online | offline | play | pause | bpm N | pitch N | "
        "beat [1-4] | master on/off | sync on/off | onair on/off | "
        "track ID | status | quit",
        flush=True,
    )
    while True:
        try:
            parts = input("prolink> ").strip().split()
            if not parts:
                continue
            command = parts[0].lower()
            if command in ("quit", "exit"):
                return
            if command == "online":
                player.set_online(True)
            elif command == "offline":
                player.set_online(False)
            elif command == "play":
                player.set_playing(True)
            elif command == "pause":
                player.set_playing(False)
            elif command == "bpm" and len(parts) == 2:
                player.set_bpm(float(parts[1]))
            elif command == "pitch" and len(parts) == 2:
                player.set_pitch(float(parts[1]))
            elif command == "beat" and len(parts) <= 2:
                player.trigger_beat(int(parts[1]) if len(parts) == 2 else None)
            elif command == "master" and len(parts) == 2:
                player.set_master(_on_off(parts[1]))
            elif command == "sync" and len(parts) == 2:
                player.set_sync(_on_off(parts[1]))
            elif command == "onair" and len(parts) == 2:
                player.set_on_air(_on_off(parts[1]))
            elif command == "track" and len(parts) == 2:
                player.set_track_id(int(parts[1]))
            elif command != "status":
                print("Invalid command; type status to show the current state.")
                continue
            emulator.wake()
            _print_state(player)
        except (EOFError, KeyboardInterrupt):
            return
        except ValueError as exc:
            print(f"Error: {exc}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug or args.raw_dump else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        interface = resolve_network_interface(
            interface=args.interface,
            bind_ip=args.bind_ip,
            broadcast_ip=args.broadcast_ip,
            mac=args.mac,
        )
        if not interface.mac:
            raise ValueError(
                "could not determine the selected NIC MAC; pass --mac explicitly"
            )
        player = EmulatedPlayer(
            player_id=args.player_number,
            name=args.name,
            bpm=args.bpm,
            pitch_percent=args.pitch,
            playing=args.playing,
            master=args.master,
            sync=args.sync,
            on_air=args.on_air,
            track_id=args.track_id,
            online=args.online,
        )
        emulator = ProLinkNetworkEmulator(
            player,
            bind_ip=interface.ip,
            broadcast_ip=interface.broadcast,
            mac=interface.mac,
            target_ip=args.target_ip,
            claim_number=args.claim,
            capture_path=args.capture,
            raw_dump=args.raw_dump,
        )
        emulator.start()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Cannot start network emulator: {exc}", file=sys.stderr)
        return 2

    try:
        _print_state(player)
        if args.duration is not None:
            deadline = time.monotonic() + max(0.0, args.duration)
            while time.monotonic() < deadline:
                time.sleep(min(0.1, deadline - time.monotonic()))
        elif args.headless or not sys.stdin.isatty():
            while True:
                time.sleep(0.5)
        else:
            _interactive(player, emulator)
    except KeyboardInterrupt:
        pass
    finally:
        emulator.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
