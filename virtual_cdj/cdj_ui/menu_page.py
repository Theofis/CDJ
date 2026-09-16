"""Menue - Wechsel zwischen den Bereichen.

Die Seite kennt die Bereiche nicht einzeln. Sie zeigt ``MENU_ENTRIES`` und
setzt den ``ApplicationMode``; alles Weitere macht der Rahmen. Ein neuer
Bereich erscheint hier, sobald er in ``shell/modes.py`` steht.
"""

from __future__ import annotations

import tkinter as tk

from ..deck.mode_manager import ModeManager, OperatingMode
from ..shell.modes import MENU_ENTRIES, ApplicationMode, ModeController
from . import theme
from .pages import Page

#: Hoehe einer Menuezeile in Layoutpixeln.
ROW_H = 64

OPERATING_ACTION = "OPERATING_MODE"
OPERATING_ENTRIES: tuple[tuple[OperatingMode, str, str], ...] = (
    (OperatingMode.MIDI, "MIDI / CONTROLLER", "rekordbox + GRV6"),
    (OperatingMode.CDJ, "CDJ / STANDALONE", "USB + externer Mixer"),
)


class MenuPage(Page):
    """Liste der Bereiche, mit Beruehrung oder Tastatur bedienbar."""

    mode = ApplicationMode.MENU
    title = "MENUE"

    def __init__(
        self,
        master: tk.Misc,
        modes: ModeController,
        *,
        operating_modes: ModeManager | None = None,
    ) -> None:
        super().__init__(master)
        self.modes = modes
        self.operating_modes = operating_modes
        self.operating_view = False
        self.message = ""
        self.selected = 0
        self.canvas = tk.Canvas(
            self, bg=theme.BG, highlightthickness=0, bd=0
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Configure>", lambda _e: self.refresh())
        self._rows: list[tuple[float, float, object]] = []

    # ------------------------------------------------------------------

    def on_enter(self) -> None:
        # Der Eintrag, aus dem heraus das Menue geoeffnet wurde, ist
        # vorausgewaehlt - zurueck ist damit ein Druck entfernt.
        self.operating_view = False
        self.message = ""
        previous = self.modes.previous
        for index, (target, _label, _detail) in enumerate(self._main_entries()):
            if target is previous:
                self.selected = index
                break
        self.refresh()

    # -- Bedienung ---------------------------------------------------------

    def move(self, delta: int) -> int:
        count = (
            len(OPERATING_ENTRIES)
            if self.operating_view
            else len(self._main_entries())
        )
        self.selected = max(
            0, min(self.selected + delta, count - 1)
        )
        self.refresh()
        return self.selected

    def activate(self, index: int | None = None) -> ApplicationMode:
        """Markierten Eintrag oeffnen."""
        position = self.selected if index is None else index
        if self.operating_view:
            position = max(0, min(position, len(OPERATING_ENTRIES) - 1))
            self.selected = position
            self._select_operating_mode(OPERATING_ENTRIES[position][0])
            return self.modes.mode

        entries = self._main_entries()
        position = max(0, min(position, len(entries) - 1))
        self.selected = position
        target = entries[position][0]
        if target == OPERATING_ACTION:
            self.operating_view = True
            current = self.operating_modes.current_mode  # type: ignore[union-attr]
            self.selected = next(
                i for i, entry in enumerate(OPERATING_ENTRIES)
                if entry[0] is current
            )
            self.refresh()
            return self.modes.mode
        return self.modes.set_mode(target)  # type: ignore[arg-type]

    def _on_click(self, event: tk.Event) -> None:
        for index, (top, bottom, target) in enumerate(self._rows):
            if top <= event.y <= bottom:
                self.selected = index
                if self.operating_view:
                    self._select_operating_mode(target)  # type: ignore[arg-type]
                elif target == OPERATING_ACTION:
                    self.activate(index)
                else:
                    self.modes.set_mode(target)  # type: ignore[arg-type]
                return

    def back(self) -> bool:
        """Aus der Modusauswahl ins Hauptmenue; ``True`` wenn behandelt."""
        if not self.operating_view:
            return False
        self.operating_view = False
        self.message = ""
        self.selected = next(
            index
            for index, entry in enumerate(self._main_entries())
            if entry[0] == OPERATING_ACTION
        )
        self.refresh()
        return True

    def _select_operating_mode(self, mode: OperatingMode) -> None:
        manager = self.operating_modes
        if manager is None:
            return
        changed = manager.set_mode(mode)
        self.message = (
            manager.last_error
            if manager.last_error
            else f"Aktiver Betriebsmodus: {manager.current_mode.value}"
        )
        if not changed and not self.message:
            self.message = "Betriebsmodus konnte nicht gewechselt werden."
        self.refresh()

    def _main_entries(self) -> tuple[tuple[object, str, str], ...]:
        entries = [
            (entry.mode, entry.label, entry.detail) for entry in MENU_ENTRIES
        ]
        if self.operating_modes is not None:
            entries.insert(
                1,
                (
                    OPERATING_ACTION,
                    "OPERATING MODE",
                    "MIDI / Controller oder CDJ / Standalone",
                ),
            )
        return tuple(entries)

    # -- Anzeige -----------------------------------------------------------

    def refresh(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        self._rows.clear()
        m = theme.DEFAULT_METRICS
        width = max(1, canvas.winfo_width())

        title = "OPERATING MODE" if self.operating_view else "MENUE"
        canvas.create_text(
            m.px(24), m.px(22), text=title, anchor="w",
            fill=theme.TEXT, font=m.font(16, "bold"),
        )
        canvas.create_text(
            width - m.px(24), m.px(24),
            text=(
                "F1 Hauptmenue   Drehregler waehlen"
                if self.operating_view
                else "F1 zurueck   Drehregler waehlen"
            ),
            anchor="e", fill=theme.TEXT_MUTED, font=m.font(8),
        )

        y = m.px(56)
        row_h = m.px(ROW_H)
        entries = (
            tuple((mode, label, detail) for mode, label, detail in OPERATING_ENTRIES)
            if self.operating_view
            else self._main_entries()
        )
        current = (
            self.operating_modes.current_mode
            if self.operating_modes is not None
            else None
        )
        for index, (target, label, detail) in enumerate(entries):
            top, bottom = y, y + row_h - m.px(6)
            active = index == self.selected
            canvas.create_rectangle(
                m.px(24), top, width - m.px(24), bottom,
                fill=theme.PANEL_ACTIVE if active else theme.PANEL,
                outline=theme.ACCENT if active else theme.LINE_SUBTLE,
                width=2 if active else 1,
            )
            text_x = m.px(44)
            if self.operating_view:
                radius = m.px(5)
                center_x = m.px(49)
                center_y = top + m.px(18)
                canvas.create_oval(
                    center_x - radius,
                    center_y - radius,
                    center_x + radius,
                    center_y + radius,
                    fill=theme.ACCENT if target is current else "",
                    outline=(
                        theme.ACCENT if target is current else theme.TEXT_DIM
                    ),
                    width=2,
                )
                text_x = m.px(68)
            canvas.create_text(
                text_x, top + m.px(18), text=label,
                anchor="w",
                fill=theme.TEXT if active else theme.TEXT_SECOND,
                font=m.font(13, "bold"),
            )
            canvas.create_text(
                m.px(68) if self.operating_view else m.px(44),
                top + m.px(40), text=detail, anchor="w",
                fill=theme.TEXT_DIM if active else theme.TEXT_MUTED,
                font=m.font(9),
            )
            self._rows.append((top, bottom, target))
            y += row_h

        if self.operating_view:
            status = self.message or (
                f"Aktiv: {current.value}" if current is not None else ""
            )
            canvas.create_text(
                m.px(24), y + m.px(12), text=status, anchor="nw",
                fill=(
                    theme.RED
                    if self.operating_modes is not None
                    and self.operating_modes.last_error
                    else theme.TEXT_DIM
                ),
                font=m.font(9, "bold"),
            )
