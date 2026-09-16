"""Schnittstelle vom Deck zur Audioausgabe.

Das Deck kennt die Audio-Engine nicht. Es kennt nur diesen Vertrag.
``virtual_cdj.audio.engine.DeckVoice`` erfuellt ihn strukturell - es braucht
keinen Adapter.

Ohne angehaengte Ausgabe bleibt ``NullPlaybackPort`` aktiv: das Deck fuehrt
dann seinen Transport ueber die Wanduhr und gibt keinen Ton aus.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class PlaybackPort(Protocol):
    """Was das Deck von einer Audioausgabe braucht."""

    @property
    def is_ready(self) -> bool:
        """Ob ein Track geladen und abspielbar ist."""

    @property
    def position_seconds(self) -> float:
        """Aktuelle Abspielposition. Sample-genau aus dem Audio-Thread."""

    @property
    def duration_seconds(self) -> float:
        ...

    @property
    def reached_end(self) -> bool:
        """Ob die Wiedergabe das Trackende erreicht hat."""

    def load(self, samples: np.ndarray) -> None:
        ...

    def unload(self) -> None:
        ...

    def set_playing(self, playing: bool) -> None:
        ...

    def seek_seconds(self, seconds: float) -> None:
        ...

    def nudge_seconds(self, seconds: float) -> None:
        """Position relativ verschieben - Jog und Pitch Bend."""

    def set_speed(self, speed: float) -> None:
        """Geschwindigkeitsfaktor, negativ = rueckwaerts."""

    def set_loop(
        self, active: bool, start_s: float | None, end_s: float | None
    ) -> None:
        ...


class NullPlaybackPort:
    """Keine Audioausgabe. Der Transport laeuft ueber die Wanduhr."""

    is_ready = False
    position_seconds = 0.0
    duration_seconds = 0.0
    reached_end = False

    def load(self, samples: np.ndarray) -> None:
        ...

    def unload(self) -> None:
        ...

    def set_playing(self, playing: bool) -> None:
        ...

    def seek_seconds(self, seconds: float) -> None:
        ...

    def nudge_seconds(self, seconds: float) -> None:
        ...

    def set_speed(self, speed: float) -> None:
        ...

    def set_loop(
        self, active: bool, start_s: float | None, end_s: float | None
    ) -> None:
        ...
