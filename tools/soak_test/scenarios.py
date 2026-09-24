"""Seeded session planning. Timing is sampled once, never from wall-clock state."""
from __future__ import annotations

from collections import Counter, deque
import math
import random


WEIGHTS = {
    "PLAY_PAUSE": 12, "CUE": 8, "HOT_CUE": 12, "LOOP": 12,
    "MANUAL_LOOP": 5, "HALF_DOUBLE": 6, "RELOOP_EXIT": 5,
    "JOG": 12, "SCRATCH": 7, "SEARCH": 5, "TRACK_SEARCH": 3,
    "BEAT_JUMP": 5, "TEMPO": 5, "TEMPO_RESET": 2, "TEMPO_RANGE": 2,
    "SLIP": 2, "QUANTIZE": 3, "QUANTIZE_VALUE": 2, "JOG_MODE": 2,
    "REVERSE": 1, "BACKSPIN": 1, "ZOOM": 3, "BROWSE": 2,
}

STRESS = (
    ("LOOP4", "HALF", "HALF", "BACKSPIN", "DOUBLE", "SEARCH_FWD", "SCRATCH", "SEARCH_BACK", "EXIT"),
    ("LOOP8", "SLIP_ON", "SCRATCH", "DOUBLE", "BACKSPIN", "PAUSE", "PLAY", "HALF", "EXIT"),
    ("LOOP4", "JOG_MODE", "QUANTIZE", "SEARCH", "JOG", "REVERSE_ON", "REVERSE_OFF", "EXIT"),
    ("LOAD", "LOOP4", "TRACK_SEARCH", "LOAD"),
)
EXTREME = (
    ("SEEK_START", "LOOP_TINY", "HALF", "HALF", "HALF", "DOUBLE", "BACKSPIN", "EXIT"),
    ("SEEK_END", "LOOP4", "PAUSE", "PLAY", "EXIT"),
    ("SEEK_MIDDLE", "LOOP4", "SEEK_LOOP_IN", "SEEK_LOOP_END", "JOG", "SCRATCH", "EXIT"),
    ("SLIP_ON", "LOOP4", "SCRATCH", "PAUSE", "PLAY", "EXIT", "SLIP_OFF"),
    ("REVERSE_ON", "JOG", "JOG_MODE", "QUANTIZE", "REVERSE_OFF"),
    ("LOAD", "PLAY", "LOAD", "PLAY", "LOAD", "PLAY"),
)


def tuples(value):
    return tuple(tuples(v) for v in value) if isinstance(value, (tuple, list)) else value


class Planner:
    def __init__(self, seed, profile="MIXED", weights=None):
        self.rng = random.Random(seed)
        self.profile = profile
        self.weights = dict(WEIGHTS if weights is None else weights)
        if not self.weights or any(k not in WEIGHTS or not math.isfinite(v) or v < 0 for k, v in self.weights.items()) or not sum(self.weights.values()):
            raise ValueError("Action weights must name known actions and have positive total")
        self.pending = deque()
        self.phase_bag = []
        self.phase = ""
        self.phase_budget = 0.0
        self.track_bag = []
        self.phase_counts = Counter()

    def next(self):
        if self.phase_budget <= 0:
            self.pending.clear()
        if not self.pending:
            if self.phase_budget <= 0:
                if self.profile == "MIXED":
                    if not self.phase_bag:
                        self.phase_bag = ["REALISTIC"] * 7 + ["STRESS"] * 2 + ["EXTREME"]
                        self.rng.shuffle(self.phase_bag)
                    self.phase = self.phase_bag.pop()
                else:
                    self.phase = self.profile
                self.phase_counts[self.phase] += 1
                self.phase_budget = 180.0
            if self.phase == "REALISTIC":
                self.pending.append(("LOAD", 0.0))
                self.pending.append(("PLAY", self.rng.uniform(20, 180)))
                for _ in range(self.rng.randint(2, 7)):
                    name = self.rng.choices(list(self.weights), list(self.weights.values()))[0]
                    self.pending.append((name, self.rng.choice((.1, .5, 2, 10, 30, 120))))
                self.pending.extend((("EXIT", 0.1), ("NORMALIZE", 0.1), ("PLAY", self.rng.uniform(20, 60))))
            else:
                sequence = self.rng.choice(STRESS if self.phase == "STRESS" else EXTREME)
                self.pending.extend((name, self.rng.uniform(.1, .5)) for name in sequence)
                self.pending.extend((("NORMALIZE", .1), ("PLAY", self.rng.uniform(3, 10))))
        name, delay = self.pending.popleft()
        delay = min(delay, max(0.0, self.phase_budget))
        self.phase_budget -= delay
        # Random scalar payload freezes all parameter choices for replay.
        return {"name": name, "delay": delay, "phase": self.phase,
                "choice": self.rng.randrange(2**31), "value": self.rng.random()}

    def choose_track(self, tracks):
        if not self.track_bag:
            # Every track once per cycle. Sorted catalog makes seed independent
            # of filesystem enumeration order, without privileging first tracks.
            self.track_bag = sorted(tracks)
            self.rng.shuffle(self.track_bag)
        return self.track_bag.pop()

    def checkpoint(self):
        return {"random_generator_state": self.rng.getstate(), "pending": list(self.pending),
                "phase_bag": self.phase_bag, "phase": self.phase,
                "phase_budget": self.phase_budget, "track_bag": self.track_bag,
                "phase_counts": dict(self.phase_counts)}

    def restore(self, data):
        self.rng.setstate(tuples(data["random_generator_state"]))
        self.pending = deque(data["pending"])
        self.phase_bag = data["phase_bag"]
        self.phase = data["phase"]
        self.phase_budget = data["phase_budget"]
        self.track_bag = data["track_bag"]
        self.phase_counts = Counter(data["phase_counts"])
