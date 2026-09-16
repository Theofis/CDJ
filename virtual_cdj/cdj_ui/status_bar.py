"""Obere Zeile der Wiedergabestatusanzeige (Handbuch S. 21-23).

Aufbau genau wie am Geraet, von links nach rechts:

```text
┌────────┬──────┬───────────┬──────────────┬────────┬─────────┬────────┐
│ PLAYER │TRACK │ A.HOT CUE │   REMAIN     │ TEMPO  │  BPM    │ MASTER │
│ (( 2 ))│  02  │ AUTO CUE  │  03:10.780   │  ±10   │ 128.00  │        │
│        │      │           │      SINGLE  │ +3.20% │         │        │
└────────┴──────┴───────────┴──────────────┴────────┴─────────┴────────┘
```

* 14 Playernummer, in einem Rahmen wie am Geraet
* 15 Tracknummer
* 16 A. HOT CUE, 17 AUTO CUE - Zustandskuerzel
* 18 Zeitanzeige mit REMAIN bzw. ELAPSED, Minuten/Sekunden/Millisekunden
* 19 SINGLE / CONTINUE
* 20 Wiedergabegeschwindigkeit, 21 Einstellbereich
* 22 BPM, bei Sync-Master im orangefarbenen Rahmen
* 23 MASTER / SYNC

Die untere Zeile (12 Beat Jump, 13 Quantisierung, 24 MT, 25 Tonart, 26
gesamte Wellenform) zeichnet ``overview_waveform``. Beide Zeilen benutzen
dieselben Spaltenbreiten aus ``theme``, damit die Seitenspalten senkrecht
durchlaufen.

Alle Werte kommen aus ``DeckState`` bzw. ``CdjDisplayState``. Nichts wird
hier neu berechnet.
"""

from __future__ import annotations

import tkinter as tk

from ..deck.commands import CommandType, command
from ..deck.display_state import CdjDisplayState, TimeMode
from ..deck.state import DeckState, PlayState
from . import theme
from .base import Region


def format_time_ms(seconds: float, prefix: str = "") -> str:
    """``m:ss.mmm`` mit optionalem Vorzeichen - wie am Geraet."""
    if seconds < 0:
        seconds = 0.0
    minutes = int(seconds // 60)
    rest = seconds % 60
    return f"{prefix}{minutes}:{rest:06.3f}"


def beats_label(beats: float) -> str:
    if beats >= 1:
        return f"{int(beats)}"
    return f"1/{int(round(1 / beats))}"


class CdjStatusBar(Region):
    """Werte der Wiedergabestatusanzeige."""

    background = theme.PANEL

    def __init__(self, master: tk.Misc, send, deck_id: int) -> None:
        super().__init__(master, send)
        self.deck_id = deck_id
        self.display: CdjDisplayState | None = None
        self._time_box: tuple[float, float, float, float] | None = None
        self.bind("<Button-1>", self._on_press)

    # ------------------------------------------------------------------

    def signature(self, state: DeckState) -> tuple:
        """Die Statuszeile zeigt Zahlen, die sich langsam aendern.

        Die Zeit wird in Zwanzigsteln gefuehrt: mehr Aufloesung ist bei
        Millisekundenanzeige ohnehin nicht lesbar, und der Bereich muss
        dadurch nur 20-mal je Sekunde neu gezeichnet werden statt 60-mal.
        """
        display = self.display
        return (
            state.track.track_id if state.track else "",
            int(state.position_s * 20),
            round(state.current_bpm, 2),
            round(state.tempo_percent, 2),
            state.play_state, state.is_master, state.sync,
            state.master_tempo, state.play_mode,
            state.player_number, state.track_number, state.tempo_range,
            state.tempo_reset, state.hot_cue_auto_load,
            display.time_mode if display else None,
        )

    def set_display(self, display: CdjDisplayState) -> None:
        # Kein blindes Ungueltigmachen: was sichtbar wird, steht in
        # ``signature`` - sonst zeichnete jeder Bereich in jedem Bild neu.
        self.display = display

    # ------------------------------------------------------------------

    def _on_press(self, event: tk.Event) -> None:
        """Touch auf die Zeitanzeige wechselt Rest- und Laufzeit."""
        box = self._time_box
        if box is None:
            return
        x0, y0, x1, y1 = box
        if x0 <= event.x <= x1 and y0 <= event.y <= y1:
            self.send(command(CommandType.TIME_MODE, self.deck_id, "TOUCH"))

    # ------------------------------------------------------------------

    def render(self, state: DeckState) -> None:
        display = self.display
        if display is None:
            return
        m = self.metrics
        height = self.h
        left = m.px(theme.STATUS_LEFT)
        right_column = self.w - m.px(theme.STATUS_RIGHT)

        self.box(0, 0, self.w, height, fill=theme.PANEL, outline="")
        self.separator(0, strong=True)
        # Die Seitenspalten laufen ueber beide Zeilen der Statusanzeige.
        for x in (left, right_column):
            self.create_line(
                m.snap(x), 0, m.snap(x), height, fill=theme.LINE_SUBTLE
            )

        self._render_player(state, 0, left, height)
        self._render_master(state, right_column, self.w, height)
        self._render_values(display, state, left, right_column, height)

    # ------------------------------------------------------------------

    def _render_player(
        self, state: DeckState, x0: float, x1: float, height: float
    ) -> None:
        """Element 14: Playernummer im Rahmen, wie am Geraet."""
        m = self.metrics
        number = state.player_number or state.deck_id
        pad = m.px(5)
        self.box(
            x0 + pad, m.px(4), x1 - pad, height - m.px(4),
            fill=theme.PANEL_HI, outline=theme.ACCENT,
        )
        self.text(
            (x0 + x1) / 2, m.px(11), "PLAYER",
            size=6, weight="bold", fill=theme.TEXT_DIM, anchor="center",
        )
        self.text(
            (x0 + x1) / 2, m.px(31), str(number),
            size=17, weight="bold", fill=theme.ACCENT, anchor="center",
            mono=True,
        )

    def _render_master(
        self, state: DeckState, x0: float, x1: float, height: float
    ) -> None:
        """Element 23: MASTER oder SYNC, rechte Spalte oben."""
        m = self.metrics
        centre = (x0 + x1) / 2
        if state.is_master:
            label, colour = "MASTER", theme.MASTER_COLOR
        elif state.sync:
            label, colour = "SYNC", theme.SYNC_COLOR
        else:
            label, colour = "", theme.TEXT_DISABLED
        if not label:
            return
        pad = m.px(6)
        self.box(
            x0 + pad, m.px(9), x1 - pad, height - m.px(9),
            fill=colour, outline="",
        )
        self.text(
            centre, height / 2, label,
            size=9, weight="bold", fill=theme.BG, anchor="center",
        )

    # ------------------------------------------------------------------

    def _render_values(
        self,
        display: CdjDisplayState,
        state: DeckState,
        left: float,
        right: float,
        height: float,
    ) -> None:
        m = self.metrics

        # -- 15 Tracknummer ------------------------------------------
        x = left + m.px(9)
        self.text(x, m.px(6), "TRACK", size=6, fill=theme.TEXT_DIM)
        self.text(
            x, m.px(18),
            f"{state.track_number:02d}" if state.track_number else "--",
            size=15, weight="bold", fill=theme.TEXT, mono=True,
        )

        # -- 16/17 Zustandskuerzel ------------------------------------
        x += m.px(38)
        badges = (
            ("A.HOT CUE", state.hot_cue_auto_load, theme.RED),
            ("AUTO CUE", False, theme.TEXT_DISABLED),
        )
        badge_h = m.px(15)
        for index, (label, active, colour) in enumerate(badges):
            y0 = m.px(6) + index * (badge_h + m.px(3))
            width = m.px(6) + len(label) * m.px(4.4)
            self.box(
                x, y0, x + width, y0 + badge_h,
                fill=colour if active else theme.PANEL_HI, outline="",
            )
            self.text(
                x + width / 2, y0 + badge_h / 2, label,
                size=6, weight="bold",
                fill=theme.BG if active else theme.TEXT_MUTED,
                anchor="center",
            )

        # Die rechte Haelfte wird von rechts nach links gesetzt: BPM steht
        # am Rand, davor Tempo, davor der Wiedergabestatus, davor die Zeit.
        # Jeder Block gibt seine linke Kante an den naechsten weiter - so
        # kann nichts ueberlappen, egal wie breit ein Wert wird.

        # -- 22 BPM ---------------------------------------------------
        bpm_right = right - m.px(8)
        bpm_text = f"{state.current_bpm:.2f}" if state.current_bpm else "--.--"
        item = self.text(
            bpm_right, m.px(14), bpm_text,
            size=21, weight="bold",
            fill=theme.MASTER_COLOR if state.is_master else theme.TEXT,
            anchor="ne", mono=True,
        )
        bpm_bounds = self.bbox(item)
        if bpm_bounds and state.is_master:
            # Am Geraet umrahmt ein orangefarbener Kasten die BPM des
            # Sync-Masters.
            self.create_rectangle(
                bpm_bounds[0] - m.px(4), m.snap(m.px(3)),
                bpm_bounds[2] + m.px(3), m.snap(height - m.px(4)),
                outline=theme.MASTER_COLOR, width=1,
            )
        self.text(
            bpm_right, m.px(4), "BPM", size=6,
            fill=theme.MASTER_COLOR if state.is_master else theme.TEXT_DIM,
            anchor="ne",
        )
        cursor = (bpm_bounds[0] if bpm_bounds else bpm_right - m.px(96))

        # -- 20/21 Tempo ----------------------------------------------
        tempo_right = cursor - m.px(14)
        range_text = (
            "WIDE" if state.tempo_range is None else f"±{state.tempo_range:g}"
        )
        widest = self.text(
            tempo_right, m.px(5), f"TEMPO {range_text}",
            size=6, weight="bold", fill=theme.TEXT_DIM, anchor="ne",
        )
        left_edge = self.bbox(widest)[0]
        if state.tempo_reset:
            tempo_text, tempo_colour = "RESET", theme.ORANGE
        else:
            tempo_text = theme.format_percent(state.tempo_percent)
            tempo_colour = (
                theme.ACCENT if abs(state.tempo_percent) > 0.005
                else theme.TEXT_SECOND
            )
        item = self.text(
            tempo_right, m.px(17), tempo_text,
            size=12, weight="bold", fill=tempo_colour,
            anchor="ne", mono=True,
        )
        left_edge = min(left_edge, self.bbox(item)[0])
        if state.original_bpm:
            item = self.text(
                tempo_right, m.px(36), f"ORIG {state.original_bpm:.2f}",
                size=6, fill=theme.TEXT_MUTED, anchor="ne", mono=True,
            )
            left_edge = min(left_edge, self.bbox(item)[0])
        cursor = left_edge

        # -- 19 SINGLE / CONTINUE und Wiedergabestatus -----------------
        status_right = cursor - m.px(14)
        item = self.text(
            status_right, m.px(6), state.play_mode.value,
            size=6, weight="bold", fill=theme.TEXT_DIM, anchor="ne",
        )
        left_edge = self.bbox(item)[0]
        status = {
            PlayState.PLAYING: ("PLAY", theme.GREEN),
            PlayState.CUEING: ("CUE", theme.ORANGE),
            PlayState.PAUSED: ("PAUSE", theme.YELLOW),
            PlayState.STOPPED: ("STOP", theme.TEXT_DIM),
            PlayState.EMPTY: ("", theme.TEXT_DISABLED),
        }[state.play_state]
        if status[0]:
            item = self.text(
                status_right, m.px(18), status[0],
                size=7, weight="bold", fill=status[1], anchor="ne",
            )
            left_edge = min(left_edge, self.bbox(item)[0])
        item = self.text(
            status_right, m.px(36),
            f"TOTAL {theme.format_time(state.duration_s)}",
            size=6, fill=theme.TEXT_MUTED, anchor="ne", mono=True,
        )
        left_edge = min(left_edge, self.bbox(item)[0])
        cursor = left_edge

        # -- 18 Zeitanzeige ------------------------------------------
        text = (
            format_time_ms(display.time_value_s, display.time_prefix)
            if state.has_track
            else "-:--.---"
        )
        time_right = cursor - m.px(12)
        item = self.text(
            time_right, m.px(13), text,
            size=24, weight="bold",
            fill=theme.TEXT if state.has_track else theme.TEXT_MUTED,
            anchor="ne", mono=True,
        )
        bounds = self.bbox(item)
        self.text(
            bounds[0], m.px(5), display.time_mode.value,
            size=6, weight="bold",
            fill=theme.RED if display.time_mode is TimeMode.REMAIN
            else theme.TEXT_DIM, anchor="nw",
        )
        # Grosszuegiges Touch-Ziel, wie es ein 7-Zoll-Schirm braucht.
        self._time_box = (
            bounds[0] - m.px(6), 0.0, bounds[2] + m.px(6), height,
        )
