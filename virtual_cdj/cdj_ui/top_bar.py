"""Kopfzeile des Wiedergabebildschirms.

Links Artwork und Trackinformationen mit ⓘ fuer die Detailanzeige, rechts die
drei Panel-Schaltflaechen BEAT LOOP, KEY SHIFT und BEAT JUMP (Handbuch S. 22,
Elemente 5-9; Positionen nach der VirtualDJ-Dokumentation).

Alle drei Schaltflaechen sind echte Umschalter: sie oeffnen und schliessen das
zugehoerige Panel und zeigen deutlich, welches offen ist.
"""

from __future__ import annotations

import tkinter as tk

from ..deck.commands import CommandType, command
from ..deck.display_state import CdjDisplayState, TouchPanel
from ..deck.state import DeckState
from . import icons, theme
from .base import Region

#: Panel-Schaltflaechen rechts in der Kopfzeile, in Anzeigereihenfolge.
PANEL_BUTTONS: tuple[tuple[TouchPanel, str], ...] = (
    (TouchPanel.BEAT_LOOP, "BEAT\nLOOP"),
    (TouchPanel.KEY_SHIFT, "KEY\nSHIFT"),
    (TouchPanel.BEAT_JUMP, "BEAT\nJUMP"),
)


class CdjTopBar(Region):
    def __init__(self, master: tk.Misc, send, deck_id: int) -> None:
        super().__init__(master, send)
        self.deck_id = deck_id
        self.display: CdjDisplayState | None = None
        self._hits: list[tuple[tuple[float, float, float, float], TouchPanel]] = []
        self.bind("<Button-1>", self._on_press)

    def signature(self, state: DeckState) -> tuple:
        """Die Kopfzeile aendert sich nur bei Trackwechsel oder Panelwahl."""
        display = self.display
        track = state.track
        return (
            track.track_id if track else "",
            track.title if track else "",
            track.artist if track else "",
            track.album if track else "",
            track.genre if track else "",
            track.source if track else "",
            state.deck_id,
            state.operating_mode,
            state.connection_state,
            display.panel if display else None,
        )

    def set_display(self, display: CdjDisplayState) -> None:
        # Kein blindes Ungueltigmachen: was sichtbar wird, steht in
        # ``signature`` - sonst zeichnete jeder Bereich in jedem Bild neu.
        self.display = display

    # ------------------------------------------------------------------

    def _on_press(self, event: tk.Event) -> None:
        for (x0, y0, x1, y1), panel in self._hits:
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                self.send(
                    command(
                        CommandType.PANEL, self.deck_id, "TOUCH", panel=panel
                    )
                )
                return

    # ------------------------------------------------------------------

    def render(self, state: DeckState) -> None:
        m = self.metrics
        self._hits.clear()
        pad = m.px(theme.PAD_X)
        height = self.h
        display = self.display
        active_panel = display.panel if display else TouchPanel.NONE

        right_limit = self._render_panel_buttons(active_panel)
        art = height - 2 * m.px(6)
        art_x = pad
        art_y = m.px(6)

        # Artwork
        self.box(
            art_x, art_y, art_x + art, art_y + art,
            fill=theme.PANEL_HI, outline=theme.BORDER,
        )
        track = state.track
        if track is None or not track.artwork_path:
            icons.note(
                self, art_x + art / 2, art_y + art / 2, art * 0.44,
                theme.TEXT_MUTED,
            )

        text_x = art_x + art + m.px(10)

        if track is None:
            self.text(
                text_x, height / 2, "KEIN TRACK GELADEN",
                size=14, weight="bold", fill=theme.TEXT_MUTED, anchor="w",
            )
        else:
            self.text(
                text_x, m.px(9), track.display_title,
                size=14, weight="bold", fill=theme.TEXT,
            )
            details = [part for part in (track.artist, track.album) if part]
            if track.genre:
                details.append(track.genre)
            self.text(
                text_x, m.px(31), "   ·   ".join(details) or "—",
                size=9, fill=theme.TEXT_SECOND,
            )

            # ⓘ - Detailanzeige des Tracks
            info_x = text_x
            radius = m.px(7)
            info_y = height - m.px(11)
            icons.info(
                self, info_x + radius, info_y, radius * 2,
                theme.ACCENT
                if active_panel is TouchPanel.TRACK_INFO
                else theme.TEXT_DIM,
            )
            self._hits.append(
                (
                    (
                        info_x - m.px(4), info_y - radius - m.px(4),
                        info_x + 2 * radius + m.px(4), info_y + radius + m.px(4),
                    ),
                    TouchPanel.TRACK_INFO,
                )
            )

        # Quelle und Decknummer rechts vor den Panel-Tasten
        source = (track.source if track else "") or "—"
        mode = state.operating_mode
        self.text(
            right_limit - m.px(10), m.px(13),
            f"DECK {state.deck_id}",
            size=9, weight="bold", fill=theme.ACCENT, anchor="ne",
        )
        self.text(
            right_limit - m.px(10), m.px(30), source,
            size=8, fill=theme.TEXT_DIM, anchor="ne",
        )
        if mode:
            self.text(
                right_limit - m.px(10), m.px(45), mode,
                size=7, weight="bold",
                fill=theme.GREEN if mode == "CDJ" else theme.ACCENT,
                anchor="se",
            )

        self.separator(height - 1)

    # ------------------------------------------------------------------

    def _render_panel_buttons(self, active: TouchPanel) -> float:
        """Panel-Schaltflaechen zeichnen. Rueckgabe: linke Kante des Blocks."""
        m = self.metrics
        width = m.px(62)
        gap = m.px(4)
        top = m.px(4)
        bottom = self.h - m.px(5)
        x1 = self.w - m.px(theme.PAD_X)

        for panel, label in reversed(PANEL_BUTTONS):
            x0 = x1 - width
            is_active = panel is active
            self.box(
                x0, top, x1, bottom,
                fill=theme.PANEL_HI if is_active else theme.PANEL,
                outline=theme.ACCENT if is_active else theme.BORDER,
                width=2 if is_active else 1,
            )
            self.create_text(
                (x0 + x1) / 2, (top + bottom) / 2,
                text=label, justify="center",
                fill=theme.ACCENT if is_active else theme.TEXT_DIM,
                font=m.font(8, "bold" if is_active else "normal"),
            )
            self._hits.append(((x0, top, x1, bottom), panel))
            x1 = x0 - gap
        return x1
