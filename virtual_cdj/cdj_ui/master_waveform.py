"""Wellenform des Master-Decks - die gestapelte Ansicht.

Bewusste Abweichung vom Vier-Deck-Denken: dieser Bildschirm zeigt **lokal
gegen Master**, nicht alle Decks. Ist dieses Deck selbst Master, entfaellt die
Zeile, weil sie dieselbe Wellenform doppelt zeigen wuerde.

Der Zustand kommt als ``MasterDeckView`` - ein flaches Datenobjekt, das
spaeter genauso gut aus dem Netzwerk stammen kann.

Damit man erkennt, ob die Tracks aufeinander liegen, verwendet diese Zeile
**dasselbe Zeitfenster** wie die laufende Wellenform darunter und zeichnet
ihr Beatgrid im gleichen Maßstab.

Sie ist bewusst **kompakter** als die eigene Wellenform: eine groebere
Aufloesungsstufe, nur Taktlinien statt jedes Beats und ein klarer
Positionsstrich. Die eigene Wellenform bleibt die detaillierte; die
Master-Zeile ist die Vergleichsanzeige.
"""

from __future__ import annotations

import tkinter as tk

from ..deck.display_state import CdjDisplayState, MasterDeckView
from ..deck.state import DeckState
from . import theme
from .base import Region
from .waveform_render import CanvasWaveform

#: Wie viel groeber die Master-Zeile aufgeloest wird als die eigene
#: Wellenform. 2 heisst: halb so viele Werte je Pixel.
MASTER_DETAIL_FACTOR = 2.0


class CdjMasterWaveform(Region):
    background = theme.WAVE_BG

    def __init__(self, master: tk.Misc, send, deck_id: int) -> None:
        super().__init__(master, send)
        self.deck_id = deck_id
        self.display: CdjDisplayState | None = None
        self._waveform = CanvasWaveform()

    # ------------------------------------------------------------------

    def signature(self, own_state: DeckState) -> tuple:
        """Haengt an der Position des **Masters**, nicht am eigenen Deck."""
        display = self.display
        master = self.master_view
        if display is None or master is None:
            return ("leer",)
        seconds_per_pixel = display.window_s / max(1, self.w)
        return (
            master.player_id, master.track_id,
            int(master.position_s / max(1e-6, seconds_per_pixel)),
            round(display.window_s, 4),
            display.waveform_mode,
            round(master.bpm, 2), master.key,
        )

    def set_display(self, display: CdjDisplayState) -> None:
        # Kein blindes Ungueltigmachen: was sichtbar wird, steht in
        # ``signature`` - sonst zeichnete jeder Bereich in jedem Bild neu.
        self.display = display

    @property
    def master_view(self) -> MasterDeckView | None:
        """Master-Sicht. Nicht ``master`` - das gehoert Tkinter."""
        return self.display.master if self.display else None

    def should_show(self, own_state: DeckState) -> bool:
        """Nur zeigen, wenn ein **anderes** Deck Master ist."""
        display = self.display
        if display is None:
            return False
        return display.master_is_other_deck

    # ------------------------------------------------------------------

    def render(self, own_state: DeckState) -> None:
        display = self.display
        master = self.master_view
        m = self.metrics
        width, height = self.w, self.h

        self.box(0, 0, width, height, fill=theme.WAVE_BG, outline="")
        self.create_line(
            0, m.snap(0), width, m.snap(0),
            fill=theme.MASTER_COLOR, width=1,
        )

        if display is None or master is None:
            self.placeholder("kein Master-Deck verbunden")
            return

        label_height = m.px(12)
        wave_top = label_height
        wave_height = max(1, int(height - wave_top))

        # Dasselbe Zeitfenster wie die eigene Wellenform - nur so ist der
        # Vergleich aussagekraeftig.
        window = display.window_s
        start = master.position_s - window / 2
        end = start + window

        drawn = False
        if master.has_waveform and master.waveform is not None:
            # Bewusst eine Stufe groeber als die eigene Wellenform: die
            # Zeile ist halb so hoch, feinere Peaks waeren dort nicht mehr
            # unterscheidbar - und es spart Rechenzeit.
            level = master.waveform.level_for(
                window / max(1, width) * MASTER_DETAIL_FACTOR
            )
            if level is not None and level.length > 0:
                photo = self._waveform.update(
                    level, start, end, int(width), wave_height,
                    display.waveform_mode,
                )
                if photo is not None:
                    self.create_image(0, wave_top, image=photo, anchor="nw")
                    drawn = True

        self._draw_beat_grid(master, start, end, wave_top, height)

        if not drawn:
            self.text(
                width / 2, wave_top + wave_height / 2,
                "keine Wellenform vom Master",
                size=8, fill=theme.TEXT_MUTED, anchor="center",
            )

        # Positionsstrich des Masters - mittig, pixelgenau, mit dunklem
        # Saum, damit er sich auch von heller Wellenform abhebt.
        centre = m.snap(width / 2)
        for offset in (-1, +1):
            self.create_line(
                centre + offset, wave_top, centre + offset, height,
                fill=theme.PLAYHEAD_EDGE, width=1,
            )
        self.create_line(
            centre, wave_top, centre, height,
            fill=theme.MASTER_COLOR, width=1,
        )

        # Kopfzeile
        self.text(
            m.px(6), m.px(1),
            f"MASTER · PLAYER {master.player_id}",
            size=7, weight="bold", fill=theme.MASTER_COLOR,
        )
        title = master.title or "kein Track"
        if master.artist:
            title = f"{master.artist} – {title}"
        self.text(
            m.px(96), m.px(1), title, size=7, fill=theme.TEXT_SECOND
        )
        self.text(
            width - m.px(6), m.px(1),
            f"{master.bpm:.2f} BPM   {master.key or '—'}"
            if master.bpm else "--.--",
            size=7, fill=theme.TEXT_DIM, anchor="ne", mono=True,
        )

    # ------------------------------------------------------------------

    def _draw_beat_grid(
        self,
        master: MasterDeckView,
        start: float,
        end: float,
        top: float,
        bottom: float,
    ) -> None:
        grid = master.beat_grid
        if grid is None or not grid.is_valid:
            return
        window = end - start
        if window <= 0:
            return
        width = self.w
        per_bar = max(1, grid.beats_per_bar)

        first = max(0, grid.beat_number_at(start))
        last = grid.beat_number_at(end) + 1
        if last - first > 512:
            return
        m = self.metrics
        for number in range(first, last + 1):
            time_s = grid.beat_time(number)
            if time_s is None:
                break
            x = (time_s - start) / window * width
            if x < 0 or x > width:
                continue
            offset = number - grid.first_downbeat_index
            # Nur Takt- und Phrasenlinien. Jeder einzelne Beat waere in
            # dieser flachen Zeile nur Streifenmuster.
            if offset % (per_bar * 4) == 0:
                self.create_line(
                    m.snap(x), top, m.snap(x), bottom,
                    fill=theme.PHRASE_LINE, width=1,
                )
            elif offset % per_bar == 0:
                self.create_line(
                    m.snap(x), top + (bottom - top) * 0.25,
                    m.snap(x), bottom - (bottom - top) * 0.25,
                    fill=theme.BAR_LINE, width=1,
                )
