"""Laufende Wellenform mit festem Playhead in der Mitte.

Die Wellenform bewegt sich unter dem Playhead; die Position kommt aus
``DeckState.position_s`` und damit aus dem Audio-Thread - es gibt hier keine
zeitbasierte Animation, die vom Playback entkoppelt waere.

Gezeichnet wird aus vorberechneten Peaks. Darueber liegen Beatgrid, Loop und
Hot Cues, alle aus **derselben** Zeit-zu-Pixel-Funktion ``_x_of``. Nur so
bleiben Marker und Raster ueber alle Zoomstufen deckungsgleich.
"""

from __future__ import annotations

import tkinter as tk

from ..deck.commands import CommandType, command
from ..deck.display_state import CdjDisplayState, WaveformMode
from ..deck.state import CueKind, DeckState, LoopAdjust
from . import icons, theme
from .base import Region
from .waveform_render import CanvasWaveform

#: Ruecklauf, wenn kein Anzeigezustand gesetzt ist.
DEFAULT_WINDOW_S = 8.0

#: Anteil der Breite, an dem der Playhead steht. Entspricht
#: ``WAVEFORM CURRENT POSITION = CENTER`` am Geraet.
PLAYHEAD_FRACTION = 0.5

#: Vorrat links und rechts des Sichtfensters in Pixeln. So weit kann die
#: Wellenform wandern, ohne neu gerechnet zu werden. Bei 8 Beats auf 1024 px
#: und 154 BPM sind 256 px rund 0.8 Sekunden Vorlauf.
MARGIN_PX = 256


class CdjScrollingWaveform(Region):
    background = theme.WAVE_BG

    def __init__(self, master, send, deck_id: int) -> None:
        super().__init__(master, send)
        self.deck_id = deck_id
        self.display: CdjDisplayState | None = None
        self._waveform = CanvasWaveform()
        #: Gerechneter Bildausschnitt: Schluessel und abgedeckter Zeitraum.
        self._cache_key: tuple | None = None
        self._cache_start = 0.0
        self._cache_end = 0.0
        self._zoom_hits: list[tuple[tuple[float, float, float, float], int]] = []
        self.bind("<Button-1>", self._on_press)

    # ------------------------------------------------------------------

    def set_display(self, display: CdjDisplayState) -> None:
        # Kein blindes Ungueltigmachen: was sichtbar wird, steht in
        # ``signature`` - sonst zeichnete jeder Bereich in jedem Bild neu.
        self.display = display

    def signature(self, state: DeckState) -> tuple:
        """Neu zeichnen, sobald sich das Bild um ein Pixel verschieben wuerde.

        Das ist genau die noetige Rate: feiner waere unsichtbar, groeber
        wuerde ruckeln. Bei 8 s Fenster auf 1024 px sind das rund 7.8 ms je
        Pixel, also gut 60-mal je Sekunde - die Wellenform laeuft voll mit.
        """
        display = self.display
        seconds_per_pixel = self.window_s / max(1, self.w)
        return (
            state.track.track_id if state.track else "",
            int(state.position_s / max(1e-6, seconds_per_pixel)),
            round(self.window_s, 4),
            display.waveform_mode if display else None,
            display.rotary if display else None,
            state.loop.is_set, state.loop.active,
            state.loop.in_s, state.loop.out_s,
            state.loop.adjust,
        )

    @property
    def window_s(self) -> float:
        """Sichtbares Zeitfenster - kommt aus der Zoomstufe."""
        display = self.display
        return display.window_s if display is not None else DEFAULT_WINDOW_S

    def _on_press(self, event: tk.Event) -> None:
        for (x0, y0, x1, y1), delta in self._zoom_hits:
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                self.send(
                    command(
                        CommandType.WAVEFORM_ZOOM, self.deck_id, "TOUCH",
                        delta=delta,
                    )
                )
                return
        # Beruehrung der Flaeche schaltet die Wellenformfarbe weiter.
        display = self.display
        if display is not None:
            self.send(
                command(
                    CommandType.WAVEFORM_MODE, self.deck_id, "TOUCH",
                    mode=display.waveform_mode.next_mode(),
                )
            )

    # ------------------------------------------------------------------

    def _x_of(self, seconds: float, position_s: float) -> float:
        """Zeitpunkt -> x-Koordinate, relativ zur Abspielposition."""
        seconds_per_px = self.window_s / self.w
        return (
            self.w * PLAYHEAD_FRACTION
            + (seconds - position_s) / seconds_per_px
        )

    def visible_range(self, position_s: float) -> tuple[float, float]:
        left = position_s - self.window_s * PLAYHEAD_FRACTION
        return (left, left + self.window_s)

    # ------------------------------------------------------------------

    def render(self, state: DeckState) -> None:
        m = self.metrics
        display = self.display
        width, height = self.w, self.h
        playhead_x = width * PLAYHEAD_FRACTION

        self.box(0, 0, width, height, fill=theme.WAVE_BG, outline="")
        centre = height / 2

        if not state.has_track:
            self.placeholder(
                "kein Track geladen",
                "Track laden ueber BROWSE",
            )
            self._draw_playhead(playhead_x, height)
            return

        start, end = self.visible_range(state.position_s)

        # Waveform zuerst, damit Beatgrid, Loop und Cues darueber liegen.
        drawn = self._blit_waveform(state, start, end, width, height)

        self._draw_beat_grid(state, start, end, height)
        self._draw_loop(state, start, end, height)
        self._draw_hot_cues(state, start, end, height)

        if not drawn:
            self.create_line(
                0, centre, width, centre, fill=theme.BORDER, width=1
            )
            self.text(
                width / 2, centre - m.px(18),
                "keine Waveform-Analyse verfuegbar",
                size=9, fill=theme.TEXT_MUTED, anchor="center",
            )

        self._draw_playhead(playhead_x, height)
        self._draw_loop_length(state, height)
        self._draw_scale(state, start, end, height)
        self._draw_zoom_buttons()

    # ------------------------------------------------------------------

    def _blit_waveform(
        self,
        state: DeckState,
        start: float,
        end: float,
        width: float,
        height: float,
    ) -> bool:
        """Wellenform zeichnen - mit Vorrat links und rechts.

        Zwischen zwei Bildern verschiebt sich die Wellenform nur um wenige
        Pixel; ihr Inhalt bleibt derselbe. Das Bild in jedem Bild neu zu
        rechnen und nach Tk zu uebertragen kostete bei 1024x392 rund 12 ms -
        mehr als das ganze Bildbudget von 16.7 ms.

        Deshalb wird ein breiteres Bild erzeugt, das links und rechts einen
        Vorrat von ``MARGIN_PX`` Pixeln enthaelt. Danach wird es nur noch
        verschoben. Neu gerechnet wird erst, wenn der Vorrat aufgebraucht
        ist oder sich Track, Zoom oder Farbmodus aendern.
        """
        track = state.track
        if (
            not state.has_waveform
            or track is None
            or track.waveform is None
        ):
            return False

        seconds_per_pixel = (end - start) / max(1.0, width)
        if seconds_per_pixel <= 0:
            return False
        display = self.display
        mode = (
            display.waveform_mode if display is not None
            else WaveformMode.RGB
        )
        margin = MARGIN_PX
        key = (
            track.track_id, round(seconds_per_pixel, 9),
            int(width), int(height), mode,
        )

        need_render = key != self._cache_key
        if not need_render:
            # Liegt das Sichtfenster noch vollstaendig im gerechneten Bild?
            need_render = not (
                self._cache_start <= start + 1e-9
                and end - 1e-9 <= self._cache_end
            )
        if need_render:
            level = track.waveform.level_for(seconds_per_pixel)
            if level is None or level.length <= 0:
                return False
            image_width = int(width) + 2 * margin
            cache_start = start - margin * seconds_per_pixel
            cache_end = cache_start + image_width * seconds_per_pixel
            photo = self._waveform.update(
                level, cache_start, cache_end,
                image_width, int(height), mode,
            )
            if photo is None:
                return False
            self._cache_key = key
            self._cache_start = cache_start
            self._cache_end = cache_end

        photo = self._waveform.photo
        if photo is None:
            return False
        # Verschiebung: wo liegt der Bildanfang gegenueber dem Sichtfenster?
        offset = (self._cache_start - start) / seconds_per_pixel
        self.create_image(round(offset), 0, image=photo, anchor="nw")
        return True

    def _draw_playhead(self, x: float, height: float) -> None:
        """Ein Pixel weiss, links und rechts ein dunkler Saum.

        Eine 2 px breite Linie auf einer halben Koordinate wird von Tk auf
        drei Pixelreihen verteilt und sieht dadurch weich aus. Ein einzelnes,
        pixelgenaues Weiss mit dunklem Saum ist schaerfer und bleibt auch
        auf heller Wellenform sichtbar, ohne sie zu verdecken.
        """
        m = self.metrics
        centre = m.snap(x)
        for offset in (-1, +1):
            self.create_line(
                centre + offset, 0, centre + offset, height,
                fill=theme.PLAYHEAD_EDGE, width=1,
            )
        self.create_line(
            centre, 0, centre, height, fill=theme.PLAYHEAD, width=1
        )
        # Kleine Kerbe oben statt eines grossen Dreiecks.
        notch = m.px(4)
        self.create_line(
            centre - notch, m.snap(0), centre + notch, m.snap(0),
            fill=theme.PLAYHEAD, width=2,
        )

    def _draw_loop_length(self, state: DeckState, height: float) -> None:
        """Element 1 des Handbuchs: Anzahl der Beats des aktiven Loops.

        Am Geraet steht sie unten an der vergroesserten Wellenform, nicht in
        einer eigenen Leiste.

        Dazu der Adjust-Modus: waehrend ``IN ADJ``/``OUT ADJ`` haengt das
        Jogwheel am Loop-Punkt und **nicht** mehr an der Wiedergabe. Ohne
        Anzeige sieht ein Geraet in diesem Zustand aus, als haette es sich
        aufgehaengt - das Rad dreht, und nichts passiert.
        """
        loop = state.loop
        label = loop.label()
        m = self.metrics
        x = self.w * 0.5 - m.px(56)
        y = height - m.px(11)

        if loop.is_set and label:
            icons.loop(self, x, y, m.px(10), theme.LOOP_EDGE)
            self.text(
                x + m.px(9), y, label,
                size=9, weight="bold",
                fill=theme.LOOP_EDGE if loop.active else theme.TEXT_DIM,
                anchor="w", mono=True,
            )

        if loop.adjust is LoopAdjust.NONE:
            return
        text = f"{loop.adjust.value} ADJ"
        box_x = x + m.px(34)
        self.box(
            box_x, y - m.px(7), box_x + m.px(52), y + m.px(7),
            fill=theme.LOOP_EDGE, outline="",
        )
        self.text(
            box_x + m.px(26), y, text,
            size=7, weight="bold", fill=theme.BG, anchor="center",
        )

    def _draw_beat_grid(
        self, state: DeckState, start: float, end: float, height: float
    ) -> None:
        if state.track is None:
            return
        grid = state.track.beat_grid
        if grid is None or not grid.is_valid:
            return

        first = max(0, grid.beat_number_at(start))
        last = grid.beat_number_at(end) + 1
        # Sicherheitsgrenze, damit ein kaputtes Grid die GUI nicht blockiert.
        if last - first > 512:
            return

        per_bar = max(1, grid.beats_per_bar)
        m = self.metrics
        # Drei Stufen, damit das Raster gelesen werden kann statt nur zu
        # streifen: Beat dezent, Taktanfang klarer, Phrase (4 Takte)
        # deutlich. Alle Linien 1 px und pixelgenau gesetzt.
        for number in range(first, last + 1):
            time_s = grid.beat_time(number)
            if time_s is None:
                break
            x = self._x_of(time_s, state.position_s)
            if x < 0 or x > self.w:
                continue
            x = m.snap(x)
            offset = number - grid.first_downbeat_index
            if offset % (per_bar * 4) == 0:
                self.create_line(
                    x, 0, x, height, fill=theme.PHRASE_LINE, width=1
                )
            elif offset % per_bar == 0:
                self.create_line(
                    x, 0, x, height, fill=theme.BAR_LINE, width=1
                )
            else:
                self.create_line(
                    x, int(height * 0.22), x, int(height * 0.78),
                    fill=theme.BEAT_LINE, width=1,
                )

    def _draw_loop(
        self, state: DeckState, start: float, end: float, height: float
    ) -> None:
        loop = state.loop
        if not loop.is_set:
            return
        if loop.out_s < start or loop.in_s > end:  # type: ignore[operator]
            return
        x0 = self._x_of(loop.in_s, state.position_s)  # type: ignore[arg-type]
        x1 = self._x_of(loop.out_s, state.position_s)  # type: ignore[arg-type]
        # Kein flaechiger Fuellung: der Tk-Canvas kann keine Transparenz, und
        # eine gefuellte Flaeche wuerde die Wellenform verdecken. Stattdessen
        # Rahmen plus schmale Baender oben und unten.
        band = self.metrics.px(5)
        if loop.active:
            self.box(x0, 0, x1, band, fill=theme.LOOP_FILL, outline="")
            self.box(
                x0, height - band, x1, height,
                fill=theme.LOOP_FILL, outline="",
            )
        self.box(
            x0, 0, x1, height,
            fill="", outline=theme.LOOP_EDGE,
            width=2 if loop.active else 1,
        )
        self.text(
            x0 + self.metrics.px(3), self.metrics.px(3), "IN",
            size=7, weight="bold", fill=theme.LOOP_EDGE,
        )

    def _draw_hot_cues(
        self, state: DeckState, start: float, end: float, height: float
    ) -> None:
        if state.track is None:
            return
        m = self.metrics
        for cue in state.track.hot_cues:
            if not start <= cue.position_s <= end:
                continue
            x = self._x_of(cue.position_s, state.position_s)
            color = cue.color or theme.PAD_DEFAULT_COLORS[cue.index % 8]
            self.create_line(x, 0, x, height, fill=color, width=2)
            self.box(
                x, 0, x + m.px(13), m.px(13), fill=color, outline=""
            )
            self.text(
                x + m.px(6), m.px(6), cue.label,
                size=7, weight="bold", fill=theme.BG, anchor="center",
            )
            if cue.kind is CueKind.LOOP and cue.loop_end_s is not None:
                x1 = self._x_of(cue.loop_end_s, state.position_s)
                self.create_line(
                    x, height / 2, x1, height / 2, fill=color, width=2
                )

    def _draw_scale(
        self, state: DeckState, start: float, end: float, height: float
    ) -> None:
        m = self.metrics
        display = self.display
        mode = display.waveform_mode.value if display else "RGB"
        # Die Zoomstufe steht in der Kopfzeile; hier nur die Pixel je Beat
        # als Hinweis, wie dicht das Raster gerade liegt.
        if display is not None and display.is_beat_zoom:
            per_beat = self.w / max(1, display.zoom_beats)
            label = f"{mode}   {per_beat:.0f} px/Beat"
        else:
            label = f"{mode}   {self.window_s:g} s"
        self.text(
            m.px(3), height - m.px(11), label,
            size=7, fill=theme.TEXT_MUTED,
        )

    def _draw_zoom_buttons(self) -> None:
        """Zoom per Touch.

        Am Geraet wird ueber den Drehregler gezoomt. Auf einem
        7-Zoll-Touchscreen sind zwei grosse Touch-Ziele die bessere
        Bedienung; der Drehregler bleibt gleichwertig nutzbar.
        """
        m = self.metrics
        self._zoom_hits.clear()
        size = m.px(24)
        gap = m.px(4)
        x1 = self.w - m.px(4)
        y0 = m.px(4)
        for symbol, delta in ((icons.plus, -1), (icons.minus, +1)):
            x0 = x1 - size
            self.box(
                x0, y0, x1, y0 + size,
                fill=theme.PANEL, outline=theme.LINE_SUBTLE,
            )
            symbol(
                self, (x0 + x1) / 2, y0 + size / 2,
                m.px(10), theme.TEXT_DIM,
            )
            self._zoom_hits.append(((x0, y0, x1, y0 + size), delta))
            x1 = x0 - gap
