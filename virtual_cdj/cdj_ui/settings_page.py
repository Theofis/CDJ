"""Einstellungen.

Gezeigt wird, was es wirklich gibt: der Zustand der Audioausgabe, die
Anzeigeeinstellungen des Bildschirms und die Dateien, aus denen die
Anwendung ihre Werte nimmt. Nichts davon wird hier erfunden - jede Zeile
kommt aus einem echten Zustand.

Umschaltbare Einstellungen gehen denselben Weg wie jede Beruehrung der
Oberflaeche: als ``DeckCommand`` an den Bildschirm. Es gibt keinen zweiten
Weg, etwas zu setzen.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from ..deck.commands import CommandType, DeckCommand, command
from ..deck.display_state import CdjDisplayState
from ..deck.quantize import beat_value_label
from ..shell.calibration import CalibrationStore
from ..shell.modes import ApplicationMode, ModeController
from . import theme
from .pages import Page

ROW_H = 26


class SettingsPage(Page):
    """Uebersicht und die wenigen Schalter, die es wirklich gibt."""

    mode = ApplicationMode.SETTINGS
    title = "EINSTELLUNGEN"

    def __init__(
        self,
        master: tk.Misc,
        *,
        display: Callable[[], CdjDisplayState],
        send: Callable[[DeckCommand], None],
        deck_id: int,
        store: CalibrationStore,
        audio_status: Callable[[], str] | None = None,
        modes: ModeController | None = None,
    ) -> None:
        super().__init__(master)
        self.display = display
        self.send = send
        self.deck_id = deck_id
        self.store = store
        self.audio_status = audio_status
        self.modes = modes
        self.canvas = tk.Canvas(
            self, bg=theme.BG, highlightthickness=0, bd=0
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        #: Klickflaechen: (x0, y0, x1, y1, Kommandotyp, Parameter).
        self._hits: list[
            tuple[float, float, float, float, CommandType, dict]
        ] = []
        #: Flaeche der Menue-Schaltflaeche, falls es sie gibt.
        self._menu_hit: tuple[float, float, float, float] | None = None

    # ------------------------------------------------------------------

    def _on_click(self, event: tk.Event) -> None:
        hit = self._menu_hit
        if hit is not None and self.modes is not None:
            x0, y0, x1, y1 = hit
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                self.modes.set_mode(ApplicationMode.MENU)
                return
        for x0, y0, x1, y1, command_type, params in self._hits:
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                self.send(
                    command(command_type, self.deck_id, "SETTINGS", **params)
                )
                self.refresh()
                return

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        self._hits.clear()
        m = theme.DEFAULT_METRICS
        width = max(1, canvas.winfo_width())
        state = self.display()
        deck = state.deck

        canvas.create_text(
            m.px(20), m.px(18), text="EINSTELLUNGEN", anchor="w",
            fill=theme.TEXT, font=m.font(13, "bold"),
        )
        if self.modes is not None:
            # Weg zurueck ohne Tastatur.
            left, right = m.px(200), m.px(270)
            top, bottom = m.px(9), m.px(27)
            canvas.create_rectangle(
                left, top, right, bottom,
                fill=theme.PANEL_HI, outline=theme.BORDER,
            )
            canvas.create_text(
                (left + right) / 2, (top + bottom) / 2, text="MENUE",
                anchor="center", fill=theme.TEXT_SECOND, font=m.font(8),
            )
            self._menu_hit = (left, top, right, bottom)

        y = m.px(52)
        y = self._section(m, y, "ANZEIGE")
        y = self._toggle(
            m, width, y, "Zeitanzeige", state.time_mode.value,
            CommandType.TIME_MODE, {},
        )
        y = self._toggle(
            m, width, y, "Wellenformfarbe", state.waveform_mode.value,
            CommandType.WAVEFORM_MODE,
            {"mode": state.waveform_mode.next_mode()},
        )
        y = self._toggle(
            m, width, y, "Kopfzeile", state.header.value,
            CommandType.HEADER_TOGGLE, {},
        )
        y = self._toggle(
            m, width, y, "Needle Lock",
            "AN" if state.needle_lock else "AUS",
            CommandType.NEEDLE_LOCK, {},
        )
        y = self._toggle(
            m, width, y, "Quantize",
            "AN" if deck.quantize else "AUS",
            CommandType.QUANTIZE_TOGGLE, {},
        )
        # Rasterweite der Quantisierung. Am Geraet sitzt sie in
        # UTILITY/SHORTCUT; diese Seiten gibt es hier noch nicht, der Wert
        # ist aber zentral gefuehrt und muss erreichbar sein.
        y = self._toggle(
            m, width, y, "Quantize Beat Value",
            f"{beat_value_label(deck.quantize_beats)} Beat",
            CommandType.QUANTIZE_BEATS, {},
        )
        # Sprungweite fuer BEAT JUMP. Am Geraet steht sie in
        # UTILITY/SHORTCUT; an den Tastern erreicht sie CALL/DELETE +
        # BEAT JUMP. Hier die dritte, bildschirmgebundene Tuer zum selben
        # zentralen Wert.
        y = self._toggle(
            m, width, y, "Beat Jump Beat Value",
            f"{beat_value_label(deck.beat_jump_beats)} Beat",
            CommandType.BEAT_JUMP_BEATS, {},
        )
        y = self._toggle(
            m, width, y, "Slip",
            "AN" if deck.slip else "AUS",
            CommandType.SLIP_TOGGLE, {},
        )
        y = self._toggle(
            m, width, y, "Jog Mode", deck.jog_mode.value,
            CommandType.JOG_MODE_TOGGLE, {},
        )
        y = self._toggle(
            m, width, y, "Zoomstufe", state.zoom_label,
            CommandType.WAVEFORM_ZOOM, {"delta": +1},
        )

        y += m.px(10)
        y = self._section(m, y, "AUDIO UND DATEN")
        y = self._line(
            m, width, y, "Audioausgabe",
            self.audio_status() if self.audio_status else "-",
        )
        y = self._line(
            m, width, y, "Audio-Status", deck.audio_status.value,
        )
        y = self._line(
            m, width, y, "Kalibrierdatei", str(self.store.path),
        )
        y = self._line(
            m, width, y, "Eingemessen",
            ", ".join(self.store.measured_ids()) or "noch nichts",
        )
        y = self._line(
            m, width, y, "Touch-Grenzwerte",
            f"{self.store.touch.on_threshold:g} / "
            f"{self.store.touch.off_threshold:g}",
        )
        if self.store.has_unsaved_changes:
            y = self._line(
                m, width, y, "Achtung",
                "ungespeicherte Kalibrierwerte", color=theme.ORANGE,
            )

        canvas.create_text(
            m.px(20), y + m.px(16),
            text=(
                "Weitere Einstellungen gibt es noch nicht. Was hier fehlt, "
                "ist nicht umgesetzt - nicht versteckt."
            ),
            anchor="w", fill=theme.TEXT_MUTED, font=m.font(8),
        )

    # ------------------------------------------------------------------

    def _section(self, m, y: float, title: str) -> float:
        self.canvas.create_text(
            m.px(20), y, text=title, anchor="w",
            fill=theme.TEXT_DIM, font=m.font(9, "bold"),
        )
        return y + m.px(20)

    def _line(
        self, m, width: float, y: float, name: str, value: str,
        *, color: str = theme.TEXT_SECOND,
    ) -> float:
        self.canvas.create_text(
            m.px(32), y, text=name, anchor="w",
            fill=theme.TEXT_DIM, font=m.font(9),
        )
        self.canvas.create_text(
            m.px(230), y, text=value, anchor="w",
            fill=color, font=m.font(9),
        )
        return y + m.px(ROW_H)

    def _toggle(
        self, m, width: float, y: float, name: str, value: str,
        command_type: CommandType, params: dict,
    ) -> float:
        self._line(m, width, y, name, value)
        left = m.px(520)
        right = left + m.px(90)
        self.canvas.create_rectangle(
            left, y - m.px(9), right, y + m.px(9),
            fill=theme.PANEL_HI, outline=theme.BORDER,
        )
        self.canvas.create_text(
            (left + right) / 2, y, text="UMSCHALTEN", anchor="center",
            fill=theme.TEXT_SECOND, font=m.font(7),
        )
        self._hits.append(
            (left, y - m.px(9), right, y + m.px(9), command_type, params)
        )
        return y + m.px(ROW_H)
