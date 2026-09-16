"""Untere Zeile der Wiedergabestatusanzeige (Handbuch S. 21-23).

```text
┌────────┬──────────────────────────────────────────────┬────────┐
│QUANTIZE│  A     C        E   F                        │   MT   │
│   1    │ ▁▂▅█▇▅▂▁▃▅███▇▅▃▂▁▅███▇▅▃▂▁▂▅█▇▅▂▁▃▅███▇▅▃  │        │
│BEATJUMP│ 0:00      1:00      2:00      3:00     5:00  │  KEY   │
│   16   │                                              │   4A   │
└────────┴──────────────────────────────────────────────┴────────┘
```

* 12 Anzahl der Beats fuer Beat Jump - linke Spalte unten
* 13 Anzahl der Beats fuer die Quantisierung, nur wenn eingeschaltet
* 24 MT - Master Tempo
* 25 Tonart, gruen wenn sie zur Tonart des Sync-Masters passt
* 26 Gesamte Wellenform mit Cue-, Loop- und Hot-Cue-Punkten

Die Zeitskala unter der Wellenform entfaellt bei Tracks ueber 15 Minuten -
so wie am Geraet (Handbuch S. 90).

Der bereits gespielte Teil wird abgedunkelt. Das steht nicht im Handbuch,
entspricht aber der Darstellung in rekordbox und macht auf einen Blick
sichtbar, wie viel noch kommt; ``theme.PLAYED_DIM = 1.0`` schaltet es aus.
"""

from __future__ import annotations

import tkinter as tk

from ..deck.commands import CommandType, command
from ..deck.state import CueKind, DeckState
from . import theme
from .base import Region
from .waveform_render import CanvasWaveform

#: Ab dieser Laenge zeigt das Geraet keine Zeitskala mehr (Handbuch S. 90).
SCALE_LIMIT_S = 15 * 60


class CdjOverviewWaveform(Region):
    background = theme.PANEL

    def __init__(self, master: tk.Misc, send, deck_id: int) -> None:
        super().__init__(master, send)
        self.deck_id = deck_id
        self.display = None
        # Zwei Bilder: der gespielte Teil abgedunkelt, der Rest in voller
        # Helligkeit. Zusammen decken sie genau die Wellenformflaeche.
        self._played = CanvasWaveform()
        self._rest = CanvasWaveform()
        self._locked_hint = False
        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_click)

    # ------------------------------------------------------------------

    def signature(self, state) -> tuple:
        """Der Positionsstrich wandert langsam: 1024 px in Trackminuten.

        Zehn Schritte je Sekunde reichen, damit er fluessig wirkt - statt
        60 Neuzeichnungen fuer denselben sichtbaren Fortschritt.
        """
        display = self.display
        return (
            state.track.track_id if state.track else "",
            int(state.position_s * 10),
            state.loop.is_set, state.loop.active,
            state.loop.in_s, state.loop.out_s,
            state.cue_point_s, state.play_state,
            state.slip_active,
            None if state.slip_position_s is None
            else int(state.slip_position_s * 10),
            state.quantize, state.quantize_beats, state.beat_jump_beats,
            state.master_tempo, state.displayed_key,
            self._locked_hint,
            display.needle_lock if display is not None else None,
            display.waveform_mode if display is not None else None,
            display.key_is_related_to_master if display is not None else None,
        )

    def set_display(self, display) -> None:
        self.display = display

    # ------------------------------------------------------------------

    def wave_area(self) -> tuple[float, float]:
        """Linke und rechte Kante der Wellenformflaeche."""
        m = self.metrics
        return (m.px(theme.STATUS_LEFT), self.w - m.px(theme.STATUS_RIGHT))

    def _on_click(self, event: tk.Event) -> None:
        """Beruehrung springt an die Stelle des Tracks (Needle Drop).

        Needle Lock: laeuft der Track hoerbar, wird die Beruehrung ignoriert.
        Ein versehentlicher Kontakt darf die laufende Wiedergabe nicht
        versetzen. Freigabe ueber ``NEEDLE_LOCK``.
        """
        state = getattr(self, "_state", None)
        if state is None or not state.has_track or state.duration_s <= 0:
            return
        x0, x1 = self.wave_area()
        if not x0 <= event.x <= x1:
            # Die Seitenspalten sind keine Sprungflaeche.
            return
        display = getattr(self, "display", None)
        if (
            display is not None
            and display.needle_lock
            and state.is_playing
        ):
            self._locked_hint = True
            self.invalidate()
            return
        fraction = max(0.0, min(1.0, (event.x - x0) / max(1.0, x1 - x0)))
        self.send(
            command(
                CommandType.SEEK, self.deck_id, "TOUCH",
                position_s=fraction * state.duration_s,
            )
        )

    def update_state(self, state: DeckState, *, force: bool = False) -> bool:
        self._state = state
        return super().update_state(state, force=force)

    # ------------------------------------------------------------------

    def render(self, state: DeckState) -> None:
        m = self.metrics
        width, height = self.w, self.h
        left, right = self.wave_area()

        self.box(0, 0, width, height, fill=theme.PANEL, outline="")
        for x in (left, right):
            self.create_line(
                m.snap(x), 0, m.snap(x), height, fill=theme.LINE_SUBTLE
            )

        self._render_left_column(state, 0, left, height)
        self._render_right_column(state, right, width, height)
        self._render_waveform(state, left, right, height)

    # ------------------------------------------------------------------

    def _render_left_column(
        self, state: DeckState, x0: float, x1: float, height: float
    ) -> None:
        """Elemente 13 und 12: Quantisierung und Beat Jump."""
        m = self.metrics
        centre = (x0 + x1) / 2
        row = height / 2

        # 13 QUANTIZE - am Geraet nur sichtbar, wenn eingeschaltet.
        if state.quantize:
            self.text(
                centre, m.px(6), "QUANTIZE",
                size=6, weight="bold", fill=theme.QUANTIZE_COLOR,
                anchor="center",
            )
            self.text(
                centre, m.px(20),
                _beats(state.quantize_beats),
                size=11, weight="bold", fill=theme.QUANTIZE_COLOR,
                anchor="center", mono=True,
            )

        # 12 BEAT JUMP
        self.text(
            centre, row + m.px(10), "BEAT JUMP",
            size=6, weight="bold", fill=theme.TEXT_DIM, anchor="center",
        )
        self.text(
            centre, row + m.px(24), _beats(state.beat_jump_beats),
            size=11, weight="bold", fill=theme.TEXT, anchor="center",
            mono=True,
        )

    def _render_right_column(
        self, state: DeckState, x0: float, x1: float, height: float
    ) -> None:
        """Elemente 24 und 25: Master Tempo und Tonart."""
        m = self.metrics
        centre = (x0 + x1) / 2
        display = self.display

        # 24 MT - wird angezeigt, wenn Master Tempo aktiv ist.
        badge_w = m.px(26)
        self.box(
            centre - badge_w / 2, m.px(5),
            centre + badge_w / 2, m.px(19),
            fill=theme.YELLOW if state.master_tempo else theme.PANEL_HI,
            outline="",
        )
        self.text(
            centre, m.px(12), "MT",
            size=7, weight="bold",
            fill=theme.BG if state.master_tempo else theme.TEXT_MUTED,
            anchor="center",
        )

        # 25 Tonart - gruen, wenn sie zur Tonart des Masters passt.
        self.text(
            centre, m.px(34), "KEY",
            size=6, fill=theme.TEXT_DIM, anchor="center",
        )
        colour = theme.TEXT_MUTED
        if state.key:
            related = (
                display is not None and display.key_is_related_to_master
            )
            colour = theme.GREEN if related else theme.TEXT
        self.text(
            centre, m.px(50), state.displayed_key or "—",
            size=14, weight="bold", fill=colour, anchor="center",
        )

    # ------------------------------------------------------------------

    def _render_waveform(
        self, state: DeckState, left: float, right: float, height: float
    ) -> None:
        """Element 26: gesamte Wellenform mit Marken und Zeitskala."""
        m = self.metrics
        span = max(1.0, right - left)
        cue_row = m.px(9)
        top = cue_row
        scale = m.px(10)
        bottom = height - scale
        mid = (top + bottom) / 2

        if not state.has_track or state.duration_s <= 0:
            self.text(
                (left + right) / 2, height / 2, "kein Track geladen",
                size=9, fill=theme.TEXT_MUTED, anchor="center",
            )
            return

        def x_of(seconds: float) -> float:
            fraction = max(0.0, min(1.0, seconds / state.duration_s))
            return left + fraction * span

        track = state.track
        assert track is not None
        wave_height = max(1, int(bottom - top))
        position_x = x_of(state.position_s)

        drawn = False
        if state.has_waveform and track.waveform is not None:
            level = track.waveform.level_for(state.duration_s / span)
            if level is not None and level.length > 0:
                mode = (
                    self.display.waveform_mode
                    if self.display is not None else None
                )
                drawn = self._blit(
                    level, state, left, right, top, wave_height,
                    position_x, mode,
                )

        if not drawn:
            self.create_line(
                left, m.snap(mid), right, m.snap(mid),
                fill=theme.LINE_SUBTLE, width=1,
            )
            self.text(
                (left + right) / 2, mid, "keine Waveform-Analyse verfuegbar",
                size=7, fill=theme.TEXT_MUTED, anchor="center",
            )

        # -- Loop -----------------------------------------------------
        loop = state.loop
        if loop.is_set:
            self.box(
                x_of(loop.in_s), top, x_of(loop.out_s), bottom,
                fill="", outline=theme.LOOP_EDGE,
                width=2 if loop.active else 1,
            )

        # -- Slip: die Hintergrundposition -----------------------------
        #
        # Waehrend einer Slip-Aktion gibt es zwei Positionen: die hoerbare
        # (der normale Positionsstrich) und die, an der der Track ohne den
        # Eingriff waere. Ohne die zweite sieht der Ruecksprung beim
        # Loslassen wie ein Fehler aus. Gezeichnet wird sie in derselben
        # Wellenform - ein zweiter Renderer entsteht nicht.
        if state.slip_active and state.slip_position_s is not None:
            slip_x = m.snap(x_of(state.slip_position_s))
            self.create_line(
                slip_x, top, slip_x, bottom,
                fill=theme.YELLOW, width=1, dash=(3, 3),
            )

        # -- Memory Cues: schmale Striche ohne Beschriftung -----------
        for memory in track.memory_cues:
            x = m.snap(x_of(memory.position_s))
            self.create_line(
                x, top, x, bottom, fill=theme.MEMORY_CUE, width=1
            )

        # -- Hot Cues: farbige Fahne oberhalb der Wellenform ----------
        for cue in track.hot_cues:
            x = m.snap(x_of(cue.position_s))
            colour = cue.color or theme.PAD_DEFAULT_COLORS[cue.index % 8]
            self.create_line(x, top, x, bottom, fill=colour, width=1)
            tab = m.px(9)
            self.box(
                x - 1, 0, x - 1 + tab, cue_row - m.px(1),
                fill=colour, outline="",
            )
            self.text(
                x - 1 + tab / 2, (cue_row - m.px(1)) / 2, cue.label,
                size=6, weight="bold", fill=theme.BG, anchor="center",
            )
            if cue.kind is CueKind.LOOP and cue.loop_end_s is not None:
                self.create_line(
                    x, m.snap(top + m.px(2)),
                    m.snap(x_of(cue.loop_end_s)), m.snap(top + m.px(2)),
                    fill=colour, width=2,
                )

        # -- Position -------------------------------------------------
        x = m.snap(position_x)
        for offset in (-1, +1):
            self.create_line(
                x + offset, top, x + offset, bottom,
                fill=theme.PLAYHEAD_EDGE, width=1,
            )
        self.create_line(x, top, x, bottom, fill=theme.PLAYHEAD, width=1)

        self._render_scale(state, left, right, bottom, height)

    def _blit(
        self,
        level,
        state: DeckState,
        left: float,
        right: float,
        top: float,
        wave_height: int,
        position_x: float,
        mode,
    ) -> bool:
        """Wellenform in zwei Teilen zeichnen: gespielt und noch offen."""
        played_px = int(max(0.0, min(position_x - left, right - left)))
        rest_px = int(right - left) - played_px
        drawn = False
        if played_px > 0:
            photo = self._played.update(
                level, 0.0, state.position_s, played_px, wave_height,
                mode, theme.PLAYED_DIM,
            ) if mode is not None else self._played.update(
                level, 0.0, state.position_s, played_px, wave_height,
                dim=theme.PLAYED_DIM,
            )
            if photo is not None:
                self.create_image(left, top, image=photo, anchor="nw")
                drawn = True
        if rest_px > 0:
            photo = self._rest.update(
                level, state.position_s, state.duration_s,
                rest_px, wave_height, mode,
            ) if mode is not None else self._rest.update(
                level, state.position_s, state.duration_s,
                rest_px, wave_height,
            )
            if photo is not None:
                self.create_image(
                    left + played_px, top, image=photo, anchor="nw"
                )
                drawn = True
        return drawn

    def _render_scale(
        self,
        state: DeckState,
        left: float,
        right: float,
        bottom: float,
        height: float,
    ) -> None:
        """Zeitskala in Minuten - am Geraet nur bis 15 Minuten (S. 90)."""
        m = self.metrics
        span = max(1.0, right - left)

        display = self.display
        if (
            display is not None
            and display.needle_lock
            and state.is_playing
            and self._locked_hint
        ):
            self.text(
                (left + right) / 2, bottom + m.px(1), "NEEDLE LOCK",
                size=6, weight="bold", fill=theme.ORANGE, anchor="n",
            )
            return
        if not (display is not None and display.needle_lock and state.is_playing):
            self._locked_hint = False

        if state.duration_s > SCALE_LIMIT_S:
            return

        # Minutenmarken; bei kurzen Tracks halbe Minuten.
        step = 60.0 if state.duration_s > 120 else 30.0
        # Die Endzeit steht rechts ausgerichtet. Eine Marke, die ihr zu nahe
        # kommt, bekommt keine Beschriftung - sonst stehen zwei Zeiten
        # uebereinander.
        end_label_width = m.px(30)
        seconds = 0.0
        while seconds <= state.duration_s:
            x = left + seconds / state.duration_s * span
            self.create_line(
                m.snap(x), bottom, m.snap(x), bottom + m.px(3),
                fill=theme.TEXT_MUTED, width=1,
            )
            if x < right - end_label_width:
                self.text(
                    m.snap(x) + m.px(2), bottom + m.px(2),
                    theme.format_time(seconds),
                    size=6, fill=theme.TEXT_MUTED, anchor="nw", mono=True,
                )
            seconds += step
        self.text(
            right - m.px(2), bottom + m.px(2),
            theme.format_time(state.duration_s),
            size=6, fill=theme.TEXT_DIM, anchor="ne", mono=True,
        )


def _beats(beats: float) -> str:
    if beats >= 1:
        return f"{int(beats)}"
    return f"1/{int(round(1 / beats))}"
