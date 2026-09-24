"""Deterministische, wiederholbare Ein-Player-Szenarien.

Der Runner bekommt seine Zeit von außen. Im normalen Betrieb ruft ihn der
Server mit ``time.monotonic()`` auf; Tests können dieselben Abläufe ohne
Schlafen mit frei gewählten Zeitpunkten durchfahren.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .fake_player import FakePlayer


@dataclass(frozen=True, slots=True)
class ScenarioStep:
    at_s: float
    name: str
    action: Callable[[], None]


class ScenarioRunner:
    def __init__(self, steps: tuple[ScenarioStep, ...]) -> None:
        self.steps = tuple(sorted(steps, key=lambda step: step.at_s))
        self.started_at: float | None = None
        self.next_index = 0

    @property
    def done(self) -> bool:
        return self.next_index >= len(self.steps)

    def start(self, now_s: float) -> None:
        self.started_at = now_s
        self.next_index = 0
        self.tick(now_s)

    def tick(self, now_s: float) -> None:
        if self.started_at is None:
            self.start(now_s)
            return
        elapsed = max(0.0, now_s - self.started_at)
        while self.next_index < len(self.steps):
            step = self.steps[self.next_index]
            if step.at_s > elapsed:
                return
            step.action()
            self.next_index += 1


def build_scenario(name: str, player: FakePlayer) -> ScenarioRunner:
    """Ein benanntes reproduzierbares Phase-1-Szenario bauen."""
    common = (
        ScenarioStep(0.0, "online", lambda: player.set_online(True)),
        ScenarioStep(0.0, "master", lambda: player.set_master(True)),
        ScenarioStep(0.0, "154 BPM", lambda: player.set_effective_bpm(154.0)),
        ScenarioStep(0.0, "play", player.play),
    )
    variants: dict[str, tuple[ScenarioStep, ...]] = {
        "normal": common,
        "bpm-change": common + (
            ScenarioStep(10.0, "156 BPM", lambda: player.set_effective_bpm(156.0)),
        ),
        "disconnect": common + (
            ScenarioStep(5.0, "offline", lambda: player.set_online(False)),
            ScenarioStep(10.0, "online", lambda: player.set_online(True)),
        ),
        "track-change": common + (
            ScenarioStep(
                10.0,
                "Track B",
                lambda: player.set_track(
                    "test-track-02", original_bpm=128.0, effective_bpm=128.0
                ),
            ),
        ),
        "sync-toggle": common + (
            ScenarioStep(5.0, "sync on", lambda: player.set_sync(True)),
            ScenarioStep(10.0, "sync off", lambda: player.set_sync(False)),
        ),
    }
    try:
        return ScenarioRunner(variants[name])
    except KeyError as exc:
        raise ValueError(f"Unbekanntes Szenario: {name}") from exc
