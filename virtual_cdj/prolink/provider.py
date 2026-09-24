"""Provider-Vertrag für echte, simulierte und leere ProLink-Quellen."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from .models import BeatEvent, PlayerState, TrackAnalysis, TrackMetadata

PlayerStateListener = Callable[[PlayerState], None]
BeatEventListener = Callable[[BeatEvent], None]
Unsubscribe = Callable[[], None]


@runtime_checkable
class ProLinkProvider(Protocol):
    """Einheitliche, GUI-unabhängige Quelle externer Playerdaten."""

    def start(self) -> None:
        """Quelle starten. Muss mehrfach gefahrlos aufrufbar sein."""

    def stop(self) -> None:
        """Quelle und ihre Hintergrundarbeit beenden."""

    def poll(self) -> None:
        """Eingänge im aufrufenden (normalerweise GUI-)Thread zustellen."""

    def get_players(self) -> tuple[PlayerState, ...]:
        """Letzte bekannte Zustände, einschließlich offline gegangener."""

    def subscribe_player_state(self, listener: PlayerStateListener) -> Unsubscribe:
        ...

    def subscribe_beat_events(self, listener: BeatEventListener) -> Unsubscribe:
        ...

    def get_track_metadata(self, track_id: str) -> TrackMetadata | None:
        ...

    def get_track_analysis(self, track_id: str) -> TrackAnalysis | None:
        ...


class NullProLinkProvider:
    """Explizite Nullquelle für MIDI- und rein lokalen Betrieb."""

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def poll(self) -> None:
        pass

    def get_players(self) -> tuple[PlayerState, ...]:
        return ()

    def subscribe_player_state(self, listener: PlayerStateListener) -> Unsubscribe:
        return lambda: None

    def subscribe_beat_events(self, listener: BeatEventListener) -> Unsubscribe:
        return lambda: None

    def get_track_metadata(self, track_id: str) -> TrackMetadata | None:
        return None

    def get_track_analysis(self, track_id: str) -> TrackAnalysis | None:
        return None
