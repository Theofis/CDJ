"""Zeile ueber der Wellenform: Beat Countdown und Phasenmesser.

Handbuch S. 21-22: links der Beat-Countdown bis zum naechsten gespeicherten
Cue, rechts wahlweise die Wellenform-Kopfzeile oder der Phasenmesser, der die
Takt- und Beat-Abweichung vom Sync-Master zeigt. Ein Touch schaltet um.

Der Countdown wird aus Beatgrid und Cue-Position berechnet - kein statischer
Wert. Die Rechnung liegt in ``CdjDisplayState.beat_countdown()``.
"""

from __future__ import annotations

import tkinter as tk

from ..deck.commands import CommandType, command
from ..deck.display_state import CdjDisplayState, WaveformHeader
from ..deck.state import DeckState
from . import theme
from .base import Region


class CdjWaveformHeader(Region):
    background = theme.BG

    def __init__(self, master: tk.Misc, send, deck_id: int) -> None:
        super().__init__(master, send)
        self.deck_id = deck_id
        self.display: CdjDisplayState | None = None
        self.bind("<Button-1>", self._on_press)

    def signature(self, state: DeckState) -> tuple:
        """Beat Countdown und Taktskala aendern sich im Beattakt."""
        display = self.display
        countdown = display.beat_countdown() if display else None
        # Die Beatphase treibt nur den Phasenmesser. Zeigt die Kopfzeile die
        # Taktskala, aendert sich durch sie sichtbar nichts - dann muss sie
        # auch nicht in die Signatur.
        phase = 0
        if display is not None and display.header is WaveformHeader.PHASE_METER:
            phase = int(state.beat_phase * 24)
        return (
            state.bar, state.beat, phase,
            countdown.text() if countdown else "",
            countdown.label if countdown else "",
            display.zoom_label if display else "",
            display.header if display else None,
        )

    def set_display(self, display: CdjDisplayState) -> None:
        # Kein blindes Ungueltigmachen: was sichtbar wird, steht in
        # ``signature`` - sonst zeichnete jeder Bereich in jedem Bild neu.
        self.display = display

    def _on_press(self, event: tk.Event) -> None:
        # Rechte Haelfte schaltet zwischen Wellenform und Phasenmesser.
        if event.x > self.w * 0.35:
            self.send(
                command(CommandType.HEADER_TOGGLE, self.deck_id, "TOUCH")
            )

    # ------------------------------------------------------------------

    def render(self, state: DeckState) -> None:
        display = self.display
        if display is None:
            return
        m = self.metrics
        self._render_countdown(display)
        if display.header is WaveformHeader.PHASE_METER:
            self._render_phase_meter(display)
        else:
            self._render_beat_scale(display)
        self.text(
            self.w - m.px(6), self.h / 2, display.zoom_label,
            size=7, fill=theme.TEXT_MUTED, anchor="e",
        )

    # ------------------------------------------------------------------

    def _render_countdown(self, display: CdjDisplayState) -> None:
        m = self.metrics
        countdown = display.beat_countdown()

        self.text(m.px(6), m.px(2), "BEAT", size=6, fill=theme.TEXT_DIM)
        colour = theme.TEXT_MUTED
        if countdown.is_valid:
            # Kurz vor dem Cue auffaellig, wie am Geraet.
            colour = (
                theme.ORANGE if countdown.total_beats <= 16 else theme.TEXT
            )
        self.text(
            m.px(30), m.px(1), countdown.text(),
            size=15, weight="bold", fill=colour, mono=True,
        )
        if countdown.label:
            self.text(
                m.px(72), m.px(6), countdown.label,
                size=8, weight="bold", fill=theme.TEXT_DIM,
            )

    def _render_beat_scale(self, display: CdjDisplayState) -> None:
        """Taktraster als Kopfzeile - grobe Orientierung ueber dem Fenster."""
        m = self.metrics
        deck = display.deck
        track = deck.track
        if track is None or track.beat_grid is None:
            return
        grid = track.beat_grid
        if not grid.is_valid:
            return

        left = self.w * 0.35
        width = self.w - left - m.px(46)
        window = display.window_s
        start = deck.position_s - window / 2
        per_bar = max(1, grid.beats_per_bar)

        first = max(0, grid.beat_number_at(start))
        last = grid.beat_number_at(start + window) + 1
        if last - first > 512:
            return
        for number in range(first, last + 1):
            time_s = grid.beat_time(number)
            if time_s is None:
                break
            if (number - grid.first_downbeat_index) % per_bar != 0:
                continue
            x = left + (time_s - start) / window * width
            if x < left or x > left + width:
                continue
            self.create_line(
                x, self.h * 0.45, x, self.h, fill=theme.BAR_LINE, width=1
            )
        # Playhead-Markierung in der Mitte.
        centre = left + width / 2
        self.create_line(
            centre, 0, centre, self.h, fill=theme.PLAYHEAD, width=1
        )

    def _render_phase_meter(self, display: CdjDisplayState) -> None:
        """Beat-Phasenversatz zum Master."""
        m = self.metrics
        master = display.master
        left = self.w * 0.35
        width = self.w - left - m.px(46)
        centre_x = left + width / 2
        y = self.h / 2

        self.create_line(left, y, left + width, y, fill=theme.BORDER)
        self.create_line(
            centre_x, m.px(2), centre_x, self.h - m.px(2),
            fill=theme.TEXT_DIM,
        )
        self.text(
            left - m.px(6), y, "PHASE",
            size=7, fill=theme.TEXT_DIM, anchor="e",
        )

        if (
            master is None
            or not display.master_is_other_deck
            or not display.deck.has_beat_grid
            or master.beat_grid is None
        ):
            self.text(
                centre_x, y, "kein Master-Beatgrid",
                size=7, fill=theme.TEXT_MUTED, anchor="center",
            )
            return

        offset = display.deck.beat_phase - master.beat_phase
        if offset > 0.5:
            offset -= 1.0
        elif offset < -0.5:
            offset += 1.0

        x = centre_x + offset * width * 0.5
        in_phase = abs(offset) < 0.02
        self.create_line(
            x, m.px(2), x, self.h - m.px(2),
            fill=theme.GREEN if in_phase else theme.ORANGE, width=3,
        )
        self.text(
            left + width + m.px(4), y,
            f"{offset * 100:+.0f}",
            size=7, fill=theme.GREEN if in_phase else theme.ORANGE,
            anchor="w", mono=True,
        )
