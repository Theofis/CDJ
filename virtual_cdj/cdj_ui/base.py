"""Basis der Bildschirmbereiche.

Jeder Bereich ist ein Tk-Canvas, der sich aus einem ``DeckState`` zeichnet.
Ein Bereich haelt keinen eigenen Zustand ausser dem, was zum Zeichnen noetig
ist, und ruft niemals die Deck-Engine direkt auf - Bedienung geht immer als
``DeckCommand`` ueber die ``DeckStateProvider``-Schnittstelle.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from ..deck.commands import DeckCommand
from ..deck.state import DeckState
from . import theme
from .theme import Metrics

CommandSink = Callable[[DeckCommand], None]

#: Signatur, die zu keinem echten Zustand passt - erzwingt ein Neuzeichnen.
_NEVER = object()


class Region(tk.Canvas):
    """Ein Bereich der CDJ-Oberflaeche."""

    #: Hintergrundfarbe des Bereichs.
    background = theme.PANEL

    def __init__(
        self,
        master: tk.Misc,
        send: CommandSink,
        *,
        height: int | None = None,
    ) -> None:
        super().__init__(
            master,
            bg=self.background,
            highlightthickness=0,
            bd=0,
            height=height or 1,
        )
        self.send = send
        self.metrics: Metrics = theme.DEFAULT_METRICS
        self._last_generation = -1
        self._last_signature: object = _NEVER
        self._last_size = (0, 0)
        self.bind("<Configure>", self._on_configure)

    # ------------------------------------------------------------------

    def _on_configure(self, event: tk.Event) -> None:
        size = (event.width, event.height)
        if size == self._last_size:
            return
        self._last_size = size
        # Neu zeichnen erzwingen, die Geometrie hat sich geaendert.
        self.invalidate()

    def apply_metrics(self, metrics: Metrics) -> None:
        self.metrics = metrics
        self.invalidate()

    # ------------------------------------------------------------------

    def signature(self, state: DeckState) -> tuple:
        """Was dieser Bereich sichtbar macht - als vergleichbarer Wert.

        Standard ist die Zustandsnummer, also "bei jeder Aenderung neu".
        Bereiche, die sich seltener aendern als das Deck, geben eine
        groebere Signatur zurueck und werden dadurch nicht 60-mal je
        Sekunde umsonst neu gezeichnet. Die Wellenform gibt die Position
        zurueck und laeuft damit voll mit.
        """
        return (state.generation,)

    def invalidate(self) -> None:
        """Naechstes ``update_state`` zeichnet auf jeden Fall neu."""
        self._last_signature = _NEVER

    def update_state(self, state: DeckState, *, force: bool = False) -> bool:
        """Zeichnen, wenn sich sichtbar etwas geaendert hat.

        Rueckgabe: ob tatsaechlich gezeichnet wurde.
        """
        signature = self.signature(state)
        if not force and signature == self._last_signature:
            return False
        self._last_signature = signature
        self._last_generation = state.generation
        self.delete("all")
        self.render(state)
        return True

    def render(self, state: DeckState) -> None:  # pragma: no cover - GUI
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Zeichenhilfen
    # ------------------------------------------------------------------

    @property
    def w(self) -> int:
        return max(1, self.winfo_width())

    @property
    def h(self) -> int:
        return max(1, self.winfo_height())

    def text(
        self,
        x: float,
        y: float,
        content: str,
        *,
        size: int = 11,
        weight: str = "normal",
        fill: str = theme.TEXT,
        anchor: str = "nw",
        mono: bool = False,
        tags: str = "",
    ) -> int:
        font = (
            self.metrics.mono(size, weight)
            if mono
            else self.metrics.font(size, weight)
        )
        return self.create_text(
            x, y, text=content, fill=fill, font=font, anchor=anchor, tags=tags
        )

    def box(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        fill: str = theme.PANEL_HI,
        outline: str = "",
        width: int = 1,
        tags: str = "",
    ) -> int:
        return self.create_rectangle(
            x0, y0, x1, y1, fill=fill, outline=outline, width=width, tags=tags
        )

    def separator(self, y: float, *, strong: bool = False) -> int:
        """Feine Trennlinie, pixelgenau.

        Eine Linie ist immer 1 px und dezent. Wo eine Flaeche abgegrenzt
        werden muss, tut das der Helligkeitsunterschied - nicht ein Rahmen.
        """
        snapped = self.metrics.snap(y)
        return self.create_line(
            0, snapped, self.w, snapped,
            fill=theme.BORDER if strong else theme.LINE_SUBTLE, width=1,
        )

    def placeholder(self, message: str, detail: str = "") -> None:
        """Ehrlicher Leerzustand statt erfundener Daten."""
        cx, cy = self.w / 2, self.h / 2
        offset = self.metrics.px(9) if detail else 0
        self.text(
            cx, cy - offset, message,
            size=10, fill=theme.TEXT_MUTED, anchor="center",
        )
        if detail:
            self.text(
                cx, cy + offset, detail,
                size=8, fill=theme.TEXT_MUTED, anchor="center",
            )
