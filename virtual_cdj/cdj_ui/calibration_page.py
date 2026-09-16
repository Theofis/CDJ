"""Kalibrierung - Sensoren und Eingaenge einstellen und speichern.

Drei Bereiche, umschaltbar ueber die Reiter oben:

* **TOUCH / JOG** - der kapazitive Sensor mit Kennwerten, Grenzwerten und
  Live-Diagramm, dazu die Quadraturwerte des Jogwheels. Die Ansicht selbst
  ist die des Hardware-Simulators (``ui/jog_calibration.py``), nur mit der
  Palette des CDJ-Bildschirms - eine Umsetzung, zwei Erscheinungsbilder.
* **FADER / POTI** - Endanschlaege einmessen, Totzone und Mittelstellung
  setzen.
* **SPEICHERN** - alles nach ``config/calibration.json`` schreiben.

Die Seite rechnet nichts nach: gemessen wird aus ``HardwareState``,
angewandt werden die Werte im ``CalibrationStore``, den die Input-Schicht
beim Normieren fragt.
"""

from __future__ import annotations

import tkinter as tk

from ..core import controls
from ..jog import JogScanner
from ..shell.calibration import (
    AnalogCalibration,
    CalibrationStore,
    TouchCalibration,
    analog_control_ids,
)
from ..shell.hardware_state import HardwareState
from ..shell.modes import ApplicationMode, ModeController
from ..ui.jog_calibration import JogCalibrationView, Palette
from . import theme
from .pages import Page

#: Reiter dieser Seite.
TAB_SENSOR = "TOUCH / JOG"
TAB_ANALOG = "FADER / POTI"
TABS = (TAB_SENSOR, TAB_ANALOG)

ROW_H = 22


def cdj_palette() -> Palette:
    """Die Palette des CDJ-Bildschirms fuer geliehene Ansichten."""
    m = theme.DEFAULT_METRICS
    return Palette(
        window=theme.BG,
        chart=theme.WAVE_BG,
        panel=theme.PANEL,
        edge=theme.BORDER,
        text=theme.TEXT,
        label=theme.TEXT_SECOND,
        dim=theme.TEXT_DIM,
        accent=theme.ACCENT,
        on_line=theme.ORANGE,
        off_line=theme.GREEN,
        good=theme.GREEN,
        bad=theme.RED,
        font_tiny=m.font(7),
        font_small=m.font(8),
        font_label=m.font(9),
        font_body=m.font(10),
    )


class CalibrationPage(Page):
    """Kalibrierseite des CDJ-Bildschirms."""

    mode = ApplicationMode.CALIBRATION
    title = "KALIBRIERUNG"

    def __init__(
        self,
        master: tk.Misc,
        hardware: HardwareState,
        store: CalibrationStore,
        *,
        jog: JogScanner | None = None,
        modes: ModeController | None = None,
    ) -> None:
        super().__init__(master)
        self.hardware = hardware
        self.store = store
        self.modes = modes
        self.tab = TAB_SENSOR
        self._message = ""
        #: Laufende Messung: Control-ID -> (Minimum, Maximum). ``None``
        #: heisst "gestartet, aber noch kein Rohwert gesehen" - ohne
        #: Messwert darf keine Zahl erfunden werden.
        self._measuring: dict[str, tuple[float, float] | None] = {}
        self._selected = (analog_control_ids() or ("",))[0]

        self._build_header()
        self.body = tk.Frame(self, bg=theme.BG)
        self.body.pack(fill="both", expand=True)

        self.sensor_view: JogCalibrationView | None = None
        if jog is not None:
            self.sensor_view = JogCalibrationView(
                self.body, jog, lambda: (), palette=cdj_palette()
            )
        self.analog_canvas = tk.Canvas(
            self.body, bg=theme.BG, highlightthickness=0, bd=0
        )
        self.analog_canvas.bind("<Button-1>", self._on_analog_click)
        self._hits: list[tuple[float, float, float, float, str, str]] = []
        self._show_tab(self.tab)

    # ------------------------------------------------------------------
    # Kopfzeile
    # ------------------------------------------------------------------

    def _build_header(self) -> None:
        m = theme.DEFAULT_METRICS
        bar = tk.Frame(self, bg=theme.PANEL, height=m.px(34))
        bar.pack(fill="x")
        bar.pack_propagate(False)

        if self.modes is not None:
            tk.Button(
                bar, text="MENUE", font=m.font(8),
                command=lambda: self.modes.set_mode(ApplicationMode.MENU),
            ).pack(side="left", padx=m.px(8), pady=m.px(4))
        tk.Label(
            bar, text="KALIBRIERUNG", bg=theme.PANEL, fg=theme.TEXT,
            font=m.font(11, "bold"),
        ).pack(side="left", padx=m.px(12))

        self._tab_buttons: dict[str, tk.Label] = {}
        for name in TABS:
            label = tk.Label(
                bar, text=name, bg=theme.PANEL, fg=theme.TEXT_DIM,
                font=m.font(9), padx=m.px(10),
            )
            label.pack(side="left")
            label.bind("<Button-1>", lambda _e, n=name: self._show_tab(n))
            self._tab_buttons[name] = label

        self.status = tk.Label(
            bar, text="", bg=theme.PANEL, fg=theme.TEXT_DIM, font=m.font(8),
        )
        self.status.pack(side="right", padx=m.px(12))
        tk.Button(
            bar, text="SPEICHERN", command=self.save, font=m.font(8),
        ).pack(side="right", padx=m.px(6), pady=m.px(4))

    def _show_tab(self, name: str) -> None:
        self.tab = name
        for tab_name, label in self._tab_buttons.items():
            label.configure(
                fg=theme.TEXT if tab_name == name else theme.TEXT_DIM
            )
        if self.sensor_view is not None:
            self.sensor_view.pack_forget()
        self.analog_canvas.pack_forget()
        if name == TAB_SENSOR and self.sensor_view is not None:
            self.sensor_view.pack(fill="both", expand=True)
        else:
            self.analog_canvas.pack(fill="both", expand=True)
        self.refresh()

    # ------------------------------------------------------------------
    # Lebenszyklus
    # ------------------------------------------------------------------

    def on_enter(self) -> None:
        if not self._message and not self._has_sensor_data():
            # Ehrlich sagen, woher die Nullen kommen.
            self._message = (
                "kein Geraet angeschlossen - Sensorwerte bleiben leer"
            )
        self._update_status()
        self.refresh()

    def _has_sensor_data(self) -> bool:
        view = self.sensor_view
        if view is None:
            return False
        return view.scanner.touch.stats.has_samples

    def on_leave(self) -> None:
        self._measuring.clear()

    def refresh(self) -> None:
        # Messen und Zeichnen sind getrennt: die Messung laeuft mit dem
        # Takt weiter, das Zeichnen zeigt nur, was gemessen wurde.
        self.sample_measurements()
        if self.tab == TAB_SENSOR and self.sensor_view is not None:
            self.sensor_view.refresh()
            return
        self._draw_analog()

    def sample_measurements(self) -> None:
        """Laufende Messungen um den aktuellen Rohwert erweitern."""
        for control_id, span in list(self._measuring.items()):
            raw = self.hardware.analog(control_id).raw
            if raw is None:
                continue
            value = float(raw)
            self._measuring[control_id] = (
                (value, value) if span is None
                else (min(span[0], value), max(span[1], value))
            )

    # ------------------------------------------------------------------
    # Fader und Potis
    # ------------------------------------------------------------------

    def _draw_analog(self) -> None:
        canvas = self.analog_canvas
        canvas.delete("all")
        self._hits.clear()
        m = theme.DEFAULT_METRICS
        width = max(1, canvas.winfo_width())

        canvas.create_text(
            m.px(16), m.px(14),
            text="Fader und Potentiometer - Endanschlaege einmessen",
            anchor="w", fill=theme.TEXT_SECOND, font=m.font(9, "bold"),
        )
        canvas.create_text(
            m.px(16), m.px(30),
            text=(
                "MESSEN druecken, das Element einmal von Anschlag zu "
                "Anschlag bewegen, dann FERTIG."
            ),
            anchor="w", fill=theme.TEXT_MUTED, font=m.font(8),
        )

        head_y = m.px(50)
        for text, x in (
            ("ELEMENT", m.px(16)), ("ROH", width * 0.40),
            ("MIN", width * 0.50), ("MAX", width * 0.58),
            ("MITTE", width * 0.66), ("TOTZONE", width * 0.74),
            ("NORMIERT", width * 0.84),
        ):
            canvas.create_text(
                x, head_y, text=text, anchor="w",
                fill=theme.TEXT_MUTED, font=m.font(7),
            )

        y = head_y + m.px(16)
        row = m.px(ROW_H)
        for control_id in analog_control_ids():
            self._analog_row(m, canvas, width, y, control_id)
            y += row

        note = (
            f"gespeichert in {self.store.path.name}"
            if not self.store.has_unsaved_changes
            else "ungespeicherte Aenderungen - SPEICHERN druecken"
        )
        canvas.create_text(
            m.px(16), y + m.px(14), text=note, anchor="w",
            fill=(
                theme.ORANGE if self.store.has_unsaved_changes
                else theme.TEXT_MUTED
            ),
            font=m.font(8),
        )
        if self.store.load_error:
            canvas.create_text(
                m.px(16), y + m.px(30),
                text=f"Kalibrierdatei nicht lesbar: {self.store.load_error}",
                anchor="w", fill=theme.RED, font=m.font(8),
            )

    def _analog_row(
        self, m, canvas: tk.Canvas, width: float, y: float, control_id: str
    ) -> None:
        reading = self.hardware.analog(control_id)
        entry = self.store.analog(control_id)
        measuring = control_id in self._measuring
        measured = self._measuring.get(control_id)

        control = controls.CONTROLS.get(control_id)
        label = control.label if control is not None else control_id
        canvas.create_text(
            m.px(16), y, text=label, anchor="w",
            fill=theme.TEXT if measuring else theme.TEXT_SECOND,
            font=m.font(9, "bold" if measuring else "normal"),
        )
        canvas.create_text(
            width * 0.40, y,
            text="-" if reading.raw is None else f"{reading.raw:.0f}",
            anchor="w", fill=theme.TEXT_SECOND, font=m.mono(8),
        )
        if entry is not None:
            low, high = (
                measured if measured is not None
                else (entry.raw_min, entry.raw_max)
            )
            canvas.create_text(
                width * 0.50, y, text=f"{low:.0f}", anchor="w",
                fill=theme.TEXT_SECOND, font=m.mono(8),
            )
            canvas.create_text(
                width * 0.58, y, text=f"{high:.0f}", anchor="w",
                fill=theme.TEXT_SECOND, font=m.mono(8),
            )
            canvas.create_text(
                width * 0.66, y,
                text="-" if entry.center is None else f"{entry.center:.0f}",
                anchor="w", fill=theme.TEXT_SECOND, font=m.mono(8),
            )
            canvas.create_text(
                width * 0.74, y, text=f"{entry.deadzone:.2f}", anchor="w",
                fill=theme.TEXT_SECOND, font=m.mono(8),
            )
        canvas.create_text(
            width * 0.84, y, text=f"{reading.value:.3f}", anchor="w",
            fill=theme.TEXT, font=m.mono(8, "bold"),
        )
        if reading.calibrated:
            canvas.create_text(
                width * 0.92, y, text="eingemessen", anchor="w",
                fill=theme.GREEN, font=m.font(7),
            )

        # Schaltflaechen am rechten Rand.
        buttons = (
            ("FERTIG", "finish") if measuring else ("MESSEN", "measure"),
            ("MITTE", "center"),
            ("TOTZONE +", "deadzone"),
            ("ZURUECK", "reset"),
        )
        x = width - m.px(14)
        for text, action in reversed(buttons):
            w = m.px(52 if len(text) > 6 else 42)
            left, right = x - w, x
            canvas.create_rectangle(
                left, y - m.px(8), right, y + m.px(8),
                fill=theme.PANEL_ACTIVE if measuring and action == "finish"
                else theme.PANEL_HI,
                outline=theme.BORDER,
            )
            canvas.create_text(
                (left + right) / 2, y, text=text, anchor="center",
                fill=theme.TEXT_SECOND, font=m.font(7),
            )
            self._hits.append(
                (left, y - m.px(8), right, y + m.px(8), action, control_id)
            )
            x = left - m.px(4)

    def _on_analog_click(self, event: tk.Event) -> None:
        for x0, y0, x1, y1, action, control_id in self._hits:
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                self.apply(action, control_id)
                return

    # ------------------------------------------------------------------
    # Aktionen - alles geht in den CalibrationStore
    # ------------------------------------------------------------------

    def apply(self, action: str, control_id: str) -> None:
        if action == "measure":
            self.start_measuring(control_id)
        elif action == "finish":
            self.finish_measuring(control_id)
        elif action == "center":
            self.set_center(control_id)
        elif action == "deadzone":
            self.step_deadzone(control_id)
        elif action == "reset":
            self.store.clear_analog(control_id)
            self._measuring.pop(control_id, None)
            self._set_message(f"{control_id}: Messung verworfen")
        self.refresh()

    def start_measuring(self, control_id: str) -> None:
        raw = self.hardware.analog(control_id).raw
        self._measuring[control_id] = (
            (float(raw), float(raw)) if raw is not None else None
        )
        self._set_message(
            f"{control_id}: von Anschlag zu Anschlag bewegen, dann FERTIG"
        )

    def finish_measuring(self, control_id: str) -> bool:
        """Messung uebernehmen. Ohne echte Spanne wird sie verworfen."""
        if control_id not in self._measuring:
            return False
        span = self._measuring.pop(control_id)
        if span is None:
            self._set_message(
                f"{control_id}: kein Rohwert empfangen - nichts uebernommen"
            )
            return False
        low, high = span
        if high - low <= 0:
            self._set_message(
                f"{control_id}: keine Bewegung gemessen - nichts uebernommen"
            )
            return False
        current = self.store.analog(control_id)
        self.store.set_analog(
            control_id,
            AnalogCalibration(
                raw_min=low,
                raw_max=high,
                deadzone=current.deadzone if current else 0.0,
                center=None,
            ),
        )
        self._set_message(f"{control_id}: {low:.0f} bis {high:.0f} uebernommen")
        return True

    def set_center(self, control_id: str) -> bool:
        """Aktuelle Stellung als Mitte festlegen (Fader mit Rastung)."""
        raw = self.hardware.analog(control_id).raw
        if raw is None:
            self._set_message(f"{control_id}: kein Rohwert vorhanden")
            return False
        entry = self.store.analog(control_id)
        if entry is None:
            return False
        if not entry.raw_min < float(raw) < entry.raw_max:
            self._set_message(
                f"{control_id}: Mitte muss zwischen den Anschlaegen liegen"
            )
            return False
        self.store.update_analog(control_id, center=float(raw))
        self._set_message(f"{control_id}: Mitte bei {float(raw):.0f}")
        return True

    def step_deadzone(self, control_id: str) -> float:
        """Totzone in Schritten von 1 % erhoehen, bei 5 % zurueck auf 0."""
        entry = self.store.analog(control_id)
        if entry is None:
            return 0.0
        value = round(entry.deadzone + 0.01, 3)
        if value > 0.05:
            value = 0.0
        self.store.update_analog(control_id, deadzone=value)
        self._set_message(f"{control_id}: Totzone {value * 100:.0f} %")
        return value

    def save(self) -> bool:
        """Alles schreiben - auch die Grenzwerte des Touch-Sensors."""
        view = self.sensor_view
        if view is not None:
            sensor = view.scanner.touch
            try:
                self.store.set_touch(
                    TouchCalibration(
                        on_threshold=sensor.on_threshold,
                        off_threshold=sensor.off_threshold,
                    )
                )
            except ValueError as error:
                self._set_message(str(error))
                return False
        try:
            path = self.store.save()
        except OSError as error:
            self._set_message(f"nicht gespeichert: {error}")
            return False
        self._set_message(f"gespeichert: {path.name}")
        return True

    # ------------------------------------------------------------------

    def _set_message(self, text: str) -> None:
        self._message = text
        self._update_status()

    def _update_status(self) -> None:
        if self.status.winfo_exists():
            self.status.configure(text=self._message)

    @property
    def message(self) -> str:
        return self._message
