"""python -m tools.soak_test --hours .5 --usb-source D:\\ --seed 82746129"""
from __future__ import annotations

import argparse
from datetime import datetime
import gzip
import json
import math
from pathlib import Path
import secrets

from .supervisor import Supervisor


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Read-only CDJ soak test with external watchdog")
    parser.add_argument("--hours", type=float, default=.5)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--profile", type=str.upper, choices=("REALISTIC", "STRESS", "EXTREME", "MIXED"), default="MIXED")
    parser.add_argument("--usb-source", help="OS read-only mounted USB root; no automatic disk mutation")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--continue-on-failure", action="store_true")
    group.add_argument("--fail-fast", action="store_true", help="Stop on first failure (default)")
    parser.add_argument("--output", help="New output directory, must not be on source")
    parser.add_argument("--cache", default="cache/analysis", help="Local production analysis cache")
    parser.add_argument("--weights", help="JSON file overriding action weights")
    parser.add_argument("--visible", action="store_true", help="Show CDJ window; default runs real Tk window withdrawn")
    parser.add_argument("--watchdog-seconds", type=float, default=10)
    parser.add_argument("--startup-timeout", type=float, default=120)
    parser.add_argument("--load-timeout", type=float, default=600)
    parser.add_argument("--max-restarts", type=int, default=20)
    parser.add_argument("--max-rss-mb", type=float, default=4096)
    parser.add_argument("--lifecycle-cycles", type=int, default=0)
    parser.add_argument("--cycle-seconds", type=float, default=5)
    parser.add_argument("--replay", help="Recorded replay.jsonl.gz; original source still required")
    parser.add_argument("--until-action", type=int)
    parser.add_argument("--fast-replay", action="store_true", help="Omit waits; real audio callbacks remain asynchronous")
    parser.add_argument("--self-test", action="store_true", help="Synthetic demo tracks, always labeled; never a USB-soak PASS")
    parser.add_argument("--inject", choices=("gui-freeze", "audio-freeze", "runner-freeze", "crash"))
    parser.add_argument("--inject-after", type=float, default=5)
    args = parser.parse_args(argv)
    for field in ("hours", "watchdog_seconds", "startup_timeout", "load_timeout", "max_rss_mb", "cycle_seconds", "inject_after"):
        value = getattr(args, field)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{field.replace('_', '-')} must be finite and positive")
    if args.max_restarts < 0 or args.lifecycle_cycles < 0:
        parser.error("Counts cannot be negative")
    if args.until_action is not None and (args.until_action <= 0 or not args.replay):
        parser.error("--until-action needs a replay and a positive action number")
    if args.self_test and args.usb_source:
        parser.error("--self-test must not specify a real USB source")
    if not args.self_test and not args.usb_source:
        parser.error("--usb-source is required (or explicitly use --self-test)")
    if args.fast_replay and not args.replay:
        parser.error("--fast-replay requires --replay")
    args.seed = args.seed if args.seed is not None else secrets.randbits(32)
    args.output = str(Path(args.output or ("soak_results/" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))).resolve())
    args.cache = str(Path(args.cache).resolve())
    if args.usb_source:
        args.usb_source = str(Path(args.usb_source).resolve(strict=True))
    if args.weights:
        args.weights = json.loads(Path(args.weights).read_text(encoding="utf-8"))
    config = vars(args)
    if args.replay:
        opener = gzip.open if args.replay.endswith(".gz") else open
        with opener(args.replay, "rt", encoding="utf-8") as f:
            config["replay_actions"] = [json.loads(line) for line in f if line.strip()]
        config["replay_actions"] = [r for r in config["replay_actions"] if r.get("kind") == "action"
            and (args.until_action is None or r["action_number"] <= args.until_action)]
        if not config["replay_actions"]:
            parser.error("Replay contains no actions")
    return config


def main(argv=None):
    config = parse_args(argv)
    print(f"Seed: {config['seed']}; profile: {config['profile']}; results: {config['output']}", flush=True)
    return Supervisor(config).run()


if __name__ == "__main__":
    raise SystemExit(main())
