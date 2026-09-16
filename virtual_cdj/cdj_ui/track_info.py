"""Trackinformationen: Zeit, BPM, Tempo, Tonart, Beatposition.

Alle Werte stammen direkt aus dem ``DeckState``. Es wird nichts aus
GUI-Werten neu berechnet - insbesondere nicht die BPM.
"""

from __future__ import annotations

from ..deck.state import DeckState, PlayState
from . import theme
from .base import Region


class CdjTrackInfo(Region):
    def render(self, state: DeckState) -> None:
        m = self.metrics
        pad = m.px(theme.PAD_X)
        height = self.h
        width = self.w

        self._render_time(state, pad, height)
        self._render_bpm(state, width, height, m.px(150))
        self._render_key_and_beat(state, width, height)
        self.separator(height - 1)

    # ------------------------------------------------------------------

    def _render_time(self, state: DeckState, x: float, height: float) -> None:
        m = self.metrics
        label = "REMAIN" if state.has_track else "TIME"
        self.text(x, m.px(8), label, size=7, fill=theme.TEXT_DIM)

        if not state.has_track:
            self.text(
                x, m.px(20), "-:--.---",
                size=30, weight="bold", fill=theme.TEXT_MUTED, mono=True,
            )
            return

        self.text(
            x, m.px(20), theme.format_time_ms(state.remaining_s),
            size=30, weight="bold",
            fill=theme.RED if state.remaining_s <= 30 else theme.TEXT,
            mono=True,
        )
        self.text(
            x, m.px(74),
            f"ELAPSED  {theme.format_time_ms(state.position_s)}"
            f"    TOTAL  {theme.format_time(state.duration_s)}",
            size=8, fill=theme.TEXT_DIM, mono=True,
        )

        status = {
            PlayState.PLAYING: ("PLAY", theme.GREEN),
            PlayState.CUEING: ("CUE", theme.ORANGE),
            PlayState.PAUSED: ("PAUSE", theme.YELLOW),
            PlayState.STOPPED: ("STOP", theme.TEXT_DIM),
            PlayState.EMPTY: ("", theme.TEXT_DIM),
        }[state.play_state]
        if status[0]:
            self.text(
                x, m.px(90), status[0],
                size=8, weight="bold", fill=status[1],
            )

    def _render_bpm(
        self, state: DeckState, width: float, height: float, box_w: float
    ) -> None:
        m = self.metrics
        x1 = width - m.px(theme.PAD_X)
        x0 = x1 - box_w

        self.text(
            x1, m.px(8), "BPM", size=7, fill=theme.TEXT_DIM, anchor="ne"
        )
        self.text(
            x1, m.px(18), theme.format_bpm(state.current_bpm),
            size=30, weight="bold",
            fill=theme.TEXT if state.current_bpm else theme.TEXT_MUTED,
            anchor="ne", mono=True,
        )

        tempo_color = theme.TEXT_DIM
        if state.tempo_reset:
            tempo_text = "TEMPO RESET"
            tempo_color = theme.ORANGE
        else:
            tempo_text = theme.format_percent(state.tempo_percent)
            if abs(state.tempo_percent) > 0.005:
                tempo_color = theme.ACCENT
        self.text(
            x1, m.px(62), tempo_text,
            size=13, weight="bold", fill=tempo_color, anchor="ne", mono=True,
        )

        range_text = (
            "WIDE" if state.tempo_range is None
            else f"±{state.tempo_range:g}"
        )
        original = (
            f"{state.original_bpm:.2f}" if state.original_bpm else "--.--"
        )
        self.text(
            x1, m.px(84),
            f"ORIG {original}   RANGE {range_text}"
            + ("   MT" if state.master_tempo else ""),
            size=8, fill=theme.TEXT_DIM, anchor="ne", mono=True,
        )

    def _render_key_and_beat(
        self, state: DeckState, width: float, height: float
    ) -> None:
        m = self.metrics
        cx = width * 0.46

        self.text(cx, m.px(8), "KEY", size=7, fill=theme.TEXT_DIM)
        self.text(
            cx, m.px(18), state.key or "—",
            size=20, weight="bold",
            fill=theme.GREEN if state.key else theme.TEXT_MUTED,
        )

        # Beatposition 1 | 2 | 3 | 4 aus dem Beatgrid.
        self.text(cx, m.px(58), "BEAT", size=7, fill=theme.TEXT_DIM)
        size = m.px(15)
        gap = m.px(5)
        y0 = m.px(70)
        for i in range(1, 5):
            x0 = cx + (i - 1) * (size + gap)
            active = state.beat == i
            self.box(
                x0, y0, x0 + size, y0 + size,
                fill=theme.ACCENT if active else theme.STATE_OFF,
                outline=theme.BORDER,
            )
            self.text(
                x0 + size / 2, y0 + size / 2, str(i),
                size=8, weight="bold",
                fill=theme.BG if active else theme.TEXT_DIM,
                anchor="center",
            )

        bar_x = cx + 4 * (size + gap) + m.px(8)
        if state.has_beat_grid:
            self.text(bar_x, m.px(58), "BAR", size=7, fill=theme.TEXT_DIM)
            self.text(
                bar_x, m.px(68), str(state.bar),
                size=14, weight="bold", fill=theme.TEXT, mono=True,
            )
        else:
            self.text(
                bar_x, m.px(68), "kein Beatgrid",
                size=7, fill=theme.TEXT_MUTED,
            )
