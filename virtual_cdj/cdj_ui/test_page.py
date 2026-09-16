"""Pruefung / Test - Hardwarediagnose.

Zeigt jeden Ein- und Ausgang live. Alles kommt aus ``HardwareState``; diese
Seite wertet keinen Sensor selbst aus und loest keine DJ-Funktion aus.

Im Testmodus sperrt der ``InputRouter`` den Weg zur Deck-Engine. Ein
gedrueckter Hotcue-Taster faerbt hier also die Zeile ein, aber der Track
springt nicht.

LEDs lassen sich einzeln schalten: ein Klick auf die LED-Zeile schaltet um,
die beiden Schaltflaechen oben schalten alle zusammen.
"""

from __future__ import annotations

import tkinter as tk

from ..core import controls
from ..core.model import ControlType
from ..shell.hardware_state import HardwareState
from ..shell.modes import ApplicationMode, ModeController
from . import theme
from .pages import Page

#: Zeilenhoehe der Listen in Layoutpixeln.
ROW_H = 15


class TestPage(Page):
    """Live-Anzeige aller Ein- und Ausgaenge."""

    mode = ApplicationMode.TEST
    title = "PRUEFUNG / TEST"

    def __init__(
        self,
        master: tk.Misc,
        hardware: HardwareState,
        modes: ModeController | None = None,
    ) -> None:
        super().__init__(master)
        self.hardware = hardware
        self.modes = modes
        self.canvas = tk.Canvas(
            self, bg=theme.BG, highlightthickness=0, bd=0
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        #: Klickflaechen: (x0, y0, x1, y1, Aktion, Wert).
        self._hits: list[tuple[float, float, float, float, str, str]] = []
        self._buttons = tuple(
            c for c in controls.CONTROL_LIST
            if c.type is ControlType.DIGITAL_BUTTON
        )
        self._leds = tuple(c for c in controls.CONTROL_LIST if c.has_led)

    # ------------------------------------------------------------------
    # Bedienung
    # ------------------------------------------------------------------

    def _on_click(self, event: tk.Event) -> None:
        for x0, y0, x1, y1, action, value in self._hits:
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                if action == "led":
                    self.hardware.toggle_led(value)
                elif action == "all":
                    self.hardware.all_leds(value == "1")
                elif action == "menu" and self.modes is not None:
                    self.modes.set_mode(ApplicationMode.MENU)
                    return
                self.refresh()
                return

    # ------------------------------------------------------------------
    # Anzeige
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        self._hits.clear()
        m = theme.DEFAULT_METRICS
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())

        self._header(m, width)
        column = width / 3.0
        top = m.px(46)
        self._buttons_column(m, m.px(16), column - m.px(8), top, height)
        self._leds_column(m, column + m.px(8), 2 * column - m.px(8), top, height)
        self._sensors_column(
            m, 2 * column + m.px(8), width - m.px(16), top, height
        )

    # -- Kopf --------------------------------------------------------------

    def _header(self, m, width: float) -> None:
        canvas = self.canvas
        link = self.hardware.link()
        canvas.create_text(
            m.px(16), m.px(16), text="PRUEFUNG / TEST", anchor="w",
            fill=theme.TEXT, font=m.font(13, "bold"),
        )
        canvas.create_text(
            m.px(150), m.px(17),
            text="Eingaben loesen hier keine DJ-Funktion aus",
            anchor="w", fill=theme.ORANGE, font=m.font(8),
        )
        self._menu_button(m, width)
        canvas.create_text(
            width - m.px(16), m.px(10),
            text=f"Kommunikation: {link.text}", anchor="e",
            fill=theme.TEXT_SECOND if link.events else theme.TEXT_MUTED,
            font=m.font(8),
        )
        canvas.create_text(
            width - m.px(16), m.px(24),
            text=(
                "Geraet verbunden"
                if link.hardware_connected
                else "kein Geraet - Eingaben vom virtuellen Bedienfeld"
            ),
            anchor="e",
            fill=theme.GREEN if link.hardware_connected else theme.TEXT_MUTED,
            font=m.font(8),
        )
        canvas.create_line(
            0, m.px(40), width, m.px(40), fill=theme.LINE_SUBTLE,
        )

    # -- Spalte 1: Taster --------------------------------------------------

    def _buttons_column(
        self, m, x0: float, x1: float, top: float, height: float
    ) -> None:
        canvas = self.canvas
        pressed = self.hardware.buttons()
        count = sum(1 for value in pressed.values() if value)
        self._title(m, x0, top, f"TASTER   {count} gedrueckt")

        y = top + m.px(16)
        row = m.px(ROW_H)
        half = (x1 - x0) / 2
        limit = height - m.px(8)
        for index, control in enumerate(self._buttons):
            column = index % 2
            if column == 0 and index:
                y += row
            if y > limit:
                break
            left = x0 + column * half
            on = pressed.get(control.id, False)
            canvas.create_text(
                left, y, text=control.id[:18], anchor="w",
                fill=theme.TEXT if on else theme.TEXT_DIM,
                font=m.mono(8, "bold" if on else "normal"),
            )
            canvas.create_text(
                left + half - m.px(10), y, text="1" if on else "0",
                anchor="e",
                fill=theme.GREEN if on else theme.TEXT_MUTED,
                font=m.mono(8, "bold" if on else "normal"),
            )

    # -- Spalte 2: LEDs, Schalter, Encoder ---------------------------------

    def _leds_column(
        self, m, x0: float, x1: float, top: float, height: float
    ) -> None:
        canvas = self.canvas
        self._title(m, x0, top, "LED   Klick schaltet")

        # Alle ein / alle aus.
        y = top + m.px(14)
        for label, value, offset in (("ALLE EIN", "1", 0), ("ALLE AUS", "0", 70)):
            left = x0 + m.px(offset)
            right = left + m.px(62)
            canvas.create_rectangle(
                left, y, right, y + m.px(14),
                fill=theme.PANEL_HI, outline=theme.BORDER,
            )
            canvas.create_text(
                (left + right) / 2, y + m.px(7), text=label, anchor="center",
                fill=theme.TEXT_SECOND, font=m.font(7),
            )
            self._hits.append((left, y, right, y + m.px(14), "all", value))

        leds = self.hardware.leds()
        y += m.px(22)
        row = m.px(ROW_H)
        half = (x1 - x0) / 2
        for index, control in enumerate(self._leds):
            column = index % 2
            if column == 0 and index:
                y += row
            left = x0 + column * half
            on = leds.get(control.id, False)
            canvas.create_oval(
                left, y - m.px(4), left + m.px(8), y + m.px(4),
                fill=theme.ORANGE if on else theme.PANEL_ACTIVE,
                outline=theme.BORDER,
            )
            canvas.create_text(
                left + m.px(14), y, text=control.id[:16], anchor="w",
                fill=theme.TEXT_SECOND if on else theme.TEXT_DIM,
                font=m.mono(8),
            )
            self._hits.append(
                (left, y - m.px(6), left + half - m.px(6), y + m.px(6),
                 "led", control.id)
            )

        y += m.px(24)
        y = self._switches(m, x0, x1, y)
        self._encoders(m, x0, x1, y + m.px(14))

    def _switches(self, m, x0: float, x1: float, y: float) -> float:
        canvas = self.canvas
        self._title(m, x0, y, "SCHALTER")
        y += m.px(16)
        switches = self.hardware.switches()
        if not switches:
            canvas.create_text(
                x0, y, text="kein Schalter vorhanden", anchor="w",
                fill=theme.TEXT_MUTED, font=m.font(8),
            )
            return y
        for control_id, position in switches.items():
            canvas.create_text(
                x0, y, text=control_id, anchor="w",
                fill=theme.TEXT_DIM, font=m.mono(8),
            )
            canvas.create_text(
                x1 - m.px(6), y, text=position or "-", anchor="e",
                fill=theme.TEXT, font=m.mono(8, "bold"),
            )
            y += m.px(ROW_H)
        return y

    def _encoders(self, m, x0: float, x1: float, y: float) -> None:
        canvas = self.canvas
        self._title(m, x0, y, "ENCODER / JOYSTICK")
        y += m.px(16)
        for control_id, total in self.hardware.encoders().items():
            canvas.create_text(
                x0, y, text=control_id, anchor="w",
                fill=theme.TEXT_DIM, font=m.mono(8),
            )
            canvas.create_text(
                x1 - m.px(6), y, text=f"{total:+d}", anchor="e",
                fill=theme.TEXT, font=m.mono(8, "bold"),
            )
            y += m.px(ROW_H)
        joysticks = controls.by_type(ControlType.JOYSTICK)
        canvas.create_text(
            x0, y,
            text=(
                f"{len(joysticks)} Joystick(s)" if joysticks
                else "kein Joystick im Bedienfeld"
            ),
            anchor="w", fill=theme.TEXT_MUTED, font=m.font(8),
        )

    # -- Spalte 3: analog, Jogwheel ----------------------------------------

    def _sensors_column(
        self, m, x0: float, x1: float, top: float, height: float
    ) -> None:
        canvas = self.canvas
        self._title(m, x0, top, "FADER / POTENTIOMETER")
        y = top + m.px(18)
        canvas.create_text(
            x1 - m.px(60), y - m.px(12), text="ROH", anchor="e",
            fill=theme.TEXT_MUTED, font=m.font(7),
        )
        canvas.create_text(
            x1 - m.px(6), y - m.px(12), text="NORMIERT", anchor="e",
            fill=theme.TEXT_MUTED, font=m.font(7),
        )
        for control_id, reading in self.hardware.analogs().items():
            canvas.create_text(
                x0, y, text=control_id[:18], anchor="w",
                fill=theme.TEXT_DIM, font=m.mono(8),
            )
            canvas.create_text(
                x1 - m.px(60), y,
                text="-" if reading.raw is None else f"{reading.raw:.0f}",
                anchor="e", fill=theme.TEXT_SECOND, font=m.mono(8),
            )
            canvas.create_text(
                x1 - m.px(6), y, text=f"{reading.value:.3f}", anchor="e",
                fill=theme.TEXT, font=m.mono(8, "bold"),
            )
            y += m.px(ROW_H)
            bar_top = y - m.px(9)
            canvas.create_rectangle(
                x0, bar_top, x1 - m.px(6), bar_top + m.px(3),
                fill=theme.PANEL_ACTIVE, outline="",
            )
            canvas.create_rectangle(
                x0, bar_top,
                x0 + (x1 - m.px(6) - x0) * reading.value, bar_top + m.px(3),
                fill=theme.ACCENT, outline="",
            )
            y += m.px(8)

        self._jog(m, x0, x1, y + m.px(10))

    def _jog(self, m, x0: float, x1: float, y: float) -> None:
        canvas = self.canvas
        jog = self.hardware.read_jog()
        self._title(m, x0, y, "JOGWHEEL")
        y += m.px(18)

        def row(name: str, value: str, color: str = theme.TEXT) -> None:
            nonlocal y
            canvas.create_text(
                x0, y, text=name, anchor="w",
                fill=theme.TEXT_DIM, font=m.font(8),
            )
            canvas.create_text(
                x1 - m.px(6), y, text=value, anchor="e",
                fill=color, font=m.mono(8, "bold"),
            )
            y += m.px(ROW_H)

        row(
            "Touchsensor", "1" if jog.touch else "0",
            theme.GREEN if jog.touch else theme.TEXT_MUTED,
        )
        row("Touch Raw", f"{jog.touch_raw:.0f}")
        row("Lichtschranke A", "1" if jog.sensor_a else "0")
        row("Lichtschranke B", "1" if jog.sensor_b else "0")
        row("Quadratur", jog.state_text)
        row("Jog_Position_Count", f"{jog.position:+d}")
        row("Jog_Delta", f"{jog.delta:+d}")
        row("Richtung", jog.direction)
        row("Umdrehungen", f"{jog.revolutions:+.3f}")
        row(
            "Error_Count", str(jog.errors),
            theme.RED if jog.errors else theme.TEXT_MUTED,
        )
        if self.hardware.jog_scanner is None:
            canvas.create_text(
                x0, y + m.px(4),
                text=(
                    "kein Scan-Programm angehaengt - Sensorpegel bleiben "
                    "leer,\nBewegung kommt vom virtuellen Bedienfeld"
                ),
                anchor="nw", fill=theme.TEXT_MUTED, font=m.font(7),
            )

    # ------------------------------------------------------------------

    def _menu_button(self, m, width: float) -> None:
        """Weg zurueck - auf einem Touchgeraet gibt es keine F1-Taste."""
        if self.modes is None:
            return
        right = width / 2 + m.px(120)
        left = right - m.px(70)
        top, bottom = m.px(8), m.px(26)
        self.canvas.create_rectangle(
            left, top, right, bottom,
            fill=theme.PANEL_HI, outline=theme.BORDER,
        )
        self.canvas.create_text(
            (left + right) / 2, (top + bottom) / 2, text="MENUE",
            anchor="center", fill=theme.TEXT_SECOND, font=m.font(8),
        )
        self._hits.append((left, top, right, bottom, "menu", ""))

    def _title(self, m, x: float, y: float, text: str) -> None:
        self.canvas.create_text(
            x, y, text=text, anchor="w",
            fill=theme.TEXT_SECOND, font=m.font(9, "bold"),
        )
