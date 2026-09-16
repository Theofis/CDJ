"""Detailanzeige des geladenen Tracks.

Handbuch S. 22, Element 6: das ⓘ-Symbol oeffnet die ausfuehrlichen
Trackinformationen. Ein zweiter Touch schliesst es wieder.

Zeigt ausschliesslich Werte aus ``TrackInfo`` - inklusive der Angabe, wie
verlaesslich Tonart und Downbeat erkannt wurden. Bei Demo-Tracks steht dort
die Quelle ``DEMO``, damit die Herkunft nicht verschleiert wird.
"""

from __future__ import annotations

import tkinter as tk

from ..deck.commands import CommandType, command
from ..deck.display_state import CdjDisplayState, TouchPanel
from ..deck.state import DeckState
from . import icons, theme
from .base import Region


class CdjTrackInfoPopup(Region):
    background = theme.PANEL

    def __init__(self, master: tk.Misc, send, deck_id: int) -> None:
        super().__init__(master, send)
        self.deck_id = deck_id
        self.display: CdjDisplayState | None = None
        self.bind("<Button-1>", self._on_press)

    def set_display(self, display: CdjDisplayState) -> None:
        # Kein blindes Ungueltigmachen: was sichtbar wird, steht in
        # ``signature`` - sonst zeichnete jeder Bereich in jedem Bild neu.
        self.display = display

    def _on_press(self, event: tk.Event) -> None:
        self.send(
            command(
                CommandType.PANEL, self.deck_id, "TOUCH",
                panel=TouchPanel.TRACK_INFO,
            )
        )

    # ------------------------------------------------------------------

    def render(self, state: DeckState) -> None:
        m = self.metrics
        self.box(0, 0, self.w, self.h, fill=theme.PANEL, outline="")
        self.create_line(
            0, m.snap(0), self.w, m.snap(0), fill=theme.ACCENT, width=1
        )
        self.text(
            m.px(8), m.px(5), "TRACK INFO",
            size=8, weight="bold", fill=theme.ACCENT,
        )
        self.text(
            self.w - m.px(24), m.px(5), "schliessen",
            size=7, fill=theme.TEXT_DIM, anchor="ne",
        )
        icons.close(
            self, self.w - m.px(11), m.px(9), m.px(7), theme.TEXT_DIM
        )

        track = state.track
        if track is None:
            self.text(
                self.w / 2, self.h / 2, "kein Track geladen",
                size=9, fill=theme.TEXT_MUTED, anchor="center",
            )
            return

        rows: list[tuple[str, str]] = [
            ("Titel", track.title or "—"),
            ("Interpret", track.artist or "—"),
            ("Album", track.album or "—"),
            ("Genre", track.genre or "—"),
            ("Label", track.label or "—"),
            ("Laenge", theme.format_time(track.duration_s)),
            ("Original BPM", f"{track.original_bpm:.2f}"),
            ("Tonart", track.key or "—"),
            ("Quelle", track.source or "—"),
        ]
        if track.file_path:
            rows.append(("Datei", track.file_path.rsplit("/", 1)[-1]))

        y = m.px(20)
        line = m.px(13)
        for label, value in rows:
            self.text(m.px(10), y, label, size=7, fill=theme.TEXT_DIM)
            self.text(
                m.px(96), y, value, size=8, fill=theme.TEXT,
            )
            y += line

        # Verlaesslichkeit der Analyse - ehrlich statt stillschweigend.
        notes: list[str] = []
        if track.source == "DEMO":
            notes.append("Demo-Track: synthetische Analysedaten")
        else:
            if track.key and track.key_confidence:
                notes.append(f"Tonart-Konfidenz {track.key_confidence:.2f}")
            if track.downbeat_confidence:
                notes.append(
                    f"Downbeat-Konfidenz {track.downbeat_confidence:.2f}"
                )
        if state.key_shift:
            notes.append(
                f"Key Shift {state.key_shift:+d} (nur Anzeige, kein Audio)"
            )
        if notes:
            self.text(
                m.px(10), self.h - m.px(12), "   ·   ".join(notes),
                size=7, fill=theme.ORANGE,
            )
