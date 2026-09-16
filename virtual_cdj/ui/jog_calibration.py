"""Kalibrierungsoberflaeche fuer den kapazitiven Jog-Sensor.

Zeigt den Rohwert des Sensors, seine Kennwerte und den Verlauf, damit der
Grenzwert nicht geraten, sondern eingemessen wird:

* aktueller Wert, Minimum, Maximum, Durchschnitt
* Grenzwert (Ein und Aus, also mit Hysterese) - von Hand eingebbar
* Verlauf des Rohwerts ueber die Zeit, mit beiden Grenzlinien
* der daraus entstehende Touch-Zustand
* Zustand der Lichtschranken: Positionszaehler und ungueltige Wechsel

Die Oberflaeche wertet **nichts** selbst aus. Sie ruft eine Abtastfunktion
auf und zeigt, was das Scan-Programm (``virtual_cdj.jog``) daraus macht.
Damit gilt hier dasselbe wie fuer das DJ-Programm: gezeigt wird immer der
aktuelle Stand, keine abgearbeitete Warteschlange.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..jog import JogScanner, STEPS_PER_REV
from . import theme


@dataclass(frozen=True)
class Palette:
    """Farben und Schriften der Ansicht.

    Die Vorgabe ist die Oberflaeche des Hardware-Simulators. Der
    CDJ-Bildschirm reicht seine eigene Palette herein und bekommt damit
    dieselbe Ansicht in seinem Erscheinungsbild - ohne zweite Umsetzung.
    """

    window: str = theme.BG_WINDOW
    chart: str = theme.MONITOR_BG
    panel: str = theme.BG_SCREEN
    edge: str = theme.BG_PANEL_EDGE
    text: str = theme.FG_TEXT
    label: str = theme.FG_LABEL
    dim: str = theme.FG_DIM
    accent: str = theme.ACCENT
    on_line: str = theme.AMBER
    off_line: str = theme.GREEN_DIM
    good: str = theme.GREEN
    bad: str = theme.RED
    font_tiny: tuple = theme.FONT_TINY
    font_small: tuple = theme.FONT_SMALL
    font_label: tuple = theme.FONT_LABEL
    font_body: tuple = theme.FONT_BODY


#: Eine Abtastung: ``(Lichtschranke A, Lichtschranke B, Touch-Rohwert)``.
Sample = tuple[bool, bool, float]

#: Liefert die Abtastungen seit dem letzten Aufruf. Leer ist erlaubt - etwa
#: wenn ein Geraet den Scanner selbst fuellt und hier nur angezeigt wird.
SampleSource = Callable[[], Sequence[Sample]]

#: Abstand zwischen zwei Bildaufbauten in Millisekunden.
DEFAULT_INTERVAL_MS = 20


class JogCalibrationView(tk.Frame):
    """Anzeige und Eingabe der Touch-Grenzwerte."""

    def __init__(
        self,
        master: tk.Misc,
        scanner: JogScanner,
        sample_source: SampleSource,
        *,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        palette: Palette | None = None,
    ) -> None:
        #: Farben und Schriften. Ohne Angabe die des Hardware-Simulators.
        self.p = palette if palette is not None else Palette()
        super().__init__(master, bg=self.p.window)
        self.scanner = scanner
        self.sample_source = sample_source
        self.interval_ms = interval_ms
        self._after_id: str | None = None
        self._message = ""

        self.chart = tk.Canvas(
            self, bg=self.p.chart, highlightthickness=1,
            highlightbackground=self.p.edge, height=220,
        )
        self.chart.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        self.readout = tk.Canvas(
            self, bg=self.p.panel, highlightthickness=0, height=96,
        )
        self.readout.pack(fill="x", padx=8)

        self._build_controls()
        self.refresh()

    # ------------------------------------------------------------------
    # Eingabe
    # ------------------------------------------------------------------

    def _build_controls(self) -> None:
        row = tk.Frame(self, bg=self.p.window)
        row.pack(fill="x", padx=8, pady=8)

        def label(text: str) -> None:
            tk.Label(
                row, text=text, bg=self.p.window, fg=self.p.label,
                font=self.p.font_label,
            ).pack(side="left", padx=(0, 4))

        label("Grenzwert EIN")
        self.on_entry = tk.Entry(row, width=8, font=self.p.font_body)
        self.on_entry.pack(side="left", padx=(0, 10))
        label("AUS")
        self.off_entry = tk.Entry(row, width=8, font=self.p.font_body)
        self.off_entry.pack(side="left", padx=(0, 10))
        self._fill_entries()

        tk.Button(
            row, text="Uebernehmen", command=self.apply_thresholds,
            font=self.p.font_label,
        ).pack(side="left", padx=2)
        tk.Button(
            row, text="Vorschlag", command=self.suggest_thresholds,
            font=self.p.font_label,
        ).pack(side="left", padx=2)
        tk.Button(
            row, text="Messung zuruecksetzen", command=self.reset_stats,
            font=self.p.font_label,
        ).pack(side="left", padx=2)

        self.status = tk.Label(
            row, text="", bg=self.p.window, fg=self.p.dim,
            font=self.p.font_small,
        )
        self.status.pack(side="left", padx=8)

    def _fill_entries(self) -> None:
        sensor = self.scanner.touch
        self.on_entry.delete(0, "end")
        self.on_entry.insert(0, f"{sensor.on_threshold:g}")
        self.off_entry.delete(0, "end")
        self.off_entry.insert(0, f"{sensor.off_threshold:g}")

    def apply_thresholds(self) -> bool:
        """Eingetragene Grenzwerte uebernehmen. Rueckgabe: ob sie galten."""
        try:
            on_value = float(self.on_entry.get().replace(",", "."))
            off_value = float(self.off_entry.get().replace(",", "."))
        except ValueError:
            self._set_message("Grenzwert muss eine Zahl sein")
            return False
        try:
            self.scanner.touch.set_thresholds(on_value, off_value)
        except ValueError as error:
            self._set_message(str(error))
            return False
        self._set_message(f"Grenzwert {on_value:g} / {off_value:g} gesetzt")
        return True

    def suggest_thresholds(self) -> bool:
        """Grenzwerte aus den gemessenen Werten vorschlagen."""
        suggestion = self.scanner.touch.suggested_thresholds()
        if suggestion is None:
            self._set_message(
                "Zu wenig Unterschied - waehrend der Messung einmal "
                "beruehren und loslassen"
            )
            return False
        on_value, off_value = suggestion
        self.on_entry.delete(0, "end")
        self.on_entry.insert(0, f"{on_value:.1f}")
        self.off_entry.delete(0, "end")
        self.off_entry.insert(0, f"{off_value:.1f}")
        return self.apply_thresholds()

    def reset_stats(self) -> None:
        self.scanner.touch.reset_stats()
        self._set_message("Messung zurueckgesetzt")

    def _set_message(self, text: str) -> None:
        self._message = text
        if self.status.winfo_exists():
            self.status.configure(text=text)

    # ------------------------------------------------------------------
    # Takt
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Takt starten. Die erste Abtastung kommt beim naechsten Takt -
        das Aufbauen des Fensters misst noch nichts."""
        if self._after_id is None:
            self._after_id = self.after(self.interval_ms, self._tick)

    def stop(self) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:  # pragma: no cover - Fenster schon zu
                pass
            self._after_id = None

    def _tick(self) -> None:
        self.sample()
        self.refresh()
        self._after_id = self.after(self.interval_ms, self._tick)

    def sample(self) -> int:
        """Abtastungen holen und in das Scan-Programm geben.

        Rueckgabe: wie viele es waren. Das Scan-Programm verarbeitet sie
        einzeln - nur so bleibt jeder Zustandswechsel sichtbar.
        """
        samples = self.sample_source()
        for a, b, raw in samples:
            self.scanner.scan(a, b, touch_raw=raw)
        return len(samples)

    def destroy(self) -> None:
        self.stop()
        super().destroy()

    # ------------------------------------------------------------------
    # Anzeige
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        self._draw_chart()
        self._draw_readout()

    def _draw_chart(self) -> None:
        canvas = self.chart
        canvas.delete("all")
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        sensor = self.scanner.touch
        history = sensor.history
        if not history:
            canvas.create_text(
                width / 2, height / 2, text="noch keine Messwerte",
                fill=self.p.dim, font=self.p.font_body,
            )
            return

        low, high = self._range(history)
        span = high - low

        def y_of(value: float) -> float:
            return height - 8 - (value - low) / span * (height - 16)

        # Grenzlinien zuerst, damit die Kurve darueber liegt.
        for value, color, text in (
            (sensor.on_threshold, self.p.on_line, "EIN"),
            (sensor.off_threshold, self.p.off_line, "AUS"),
        ):
            if low <= value <= high:
                y = y_of(value)
                canvas.create_line(
                    0, y, width, y, fill=color, dash=(4, 3),
                )
                canvas.create_text(
                    width - 6, y - 7, text=f"{text} {value:g}",
                    fill=color, font=self.p.font_tiny, anchor="e",
                )

        step = width / max(1, len(history) - 1)
        points: list[float] = []
        for index, value in enumerate(history):
            points.extend((index * step, y_of(value)))
        if len(points) >= 4:
            canvas.create_line(
                *points, fill=self.p.accent, width=2, smooth=False
            )

        canvas.create_text(
            6, 8, text=f"{high:g}", fill=self.p.dim,
            font=self.p.font_tiny, anchor="nw",
        )
        canvas.create_text(
            6, height - 8, text=f"{low:g}", fill=self.p.dim,
            font=self.p.font_tiny, anchor="sw",
        )

    def _range(self, history: tuple[float, ...]) -> tuple[float, float]:
        """Wertebereich des Diagramms, immer mit beiden Grenzlinien."""
        sensor = self.scanner.touch
        low = min(min(history), sensor.off_threshold)
        high = max(max(history), sensor.on_threshold)
        if high - low < 1e-9:
            return (low - 1.0, high + 1.0)
        margin = (high - low) * 0.08
        return (low - margin, high + margin)

    def _draw_readout(self) -> None:
        canvas = self.readout
        canvas.delete("all")
        stats = self.scanner.touch.stats
        state = self.scanner.state

        def cell(
            column: int, row: int, name: str, value: str, color: str
        ) -> None:
            x = 10 + column * 150
            y = 14 + row * 24
            canvas.create_text(
                x, y, text=name, fill=self.p.dim,
                font=self.p.font_small, anchor="w",
            )
            canvas.create_text(
                x + 84, y, text=value, fill=color,
                font=self.p.font_body, anchor="w",
            )

        def number(value: float | None) -> str:
            return "-" if value is None else f"{value:.1f}"

        cell(0, 0, "Aktuell", number(stats.last), self.p.text)
        cell(0, 1, "Minimum", number(stats.minimum), self.p.text)
        cell(0, 2, "Maximum", number(stats.maximum), self.p.text)
        cell(1, 0, "Durchschnitt", number(stats.average), self.p.text)
        cell(1, 1, "Messwerte", str(stats.samples), self.p.text)
        cell(1, 2, "Spanne", f"{stats.span:.1f}", self.p.text)
        cell(
            2, 0, "Touch", "1" if state.touch else "0",
            self.p.good if state.touch else self.p.dim,
        )
        cell(2, 1, "Position", str(state.position), self.p.text)
        cell(
            2, 2, "Fehler", str(state.errors),
            self.p.bad if state.errors else self.p.dim,
        )
        cell(
            3, 0, "Umdrehungen",
            f"{state.position / STEPS_PER_REV:+.3f}", self.p.text,
        )
        cell(3, 1, "Schritte", str(state.steps), self.p.text)
        cell(
            3, 2, "Hysterese",
            "ja" if self.scanner.touch.has_hysteresis else "nein",
            self.p.text,
        )


class JogCalibrationApp(tk.Tk):
    """Fenster der Kalibrierung.

    Ohne Geraet laeuft ein Demo-Signal: ein gedachtes Jogwheel, dessen
    Drehzahl und Beruehrung von Hand gesetzt werden. Es ist ausdruecklich
    als Demo beschriftet - gemessen wird damit nichts, geprueft schon:
    das Scan-Programm muss daraus dieselbe Bewegung ableiten.
    """

    def __init__(
        self,
        scanner: JogScanner | None = None,
        sample_source: SampleSource | None = None,
        *,
        demo: object | None = None,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        palette: Palette | None = None,
    ) -> None:
        super().__init__()
        self.p = palette if palette is not None else Palette()
        self.title("Jog-Kalibrierung")
        self.configure(bg=self.p.window)
        self.geometry("760x520")
        self.scanner = scanner if scanner is not None else JogScanner()
        self.demo = demo

        if sample_source is None:
            sample_source = (
                self._demo_samples if demo is not None else _no_samples
            )
        self.view = JogCalibrationView(
            self, self.scanner, sample_source,
            interval_ms=interval_ms, palette=self.p,
        )
        self.view.pack(fill="both", expand=True)

        tk.Label(
            self,
            text=(
                "Demo-Signal - kein Geraet angeschlossen"
                if demo is not None
                else "Wartet auf Protokollzeilen (Q / C / P) von der Quelle"
            ),
            bg=self.p.window, fg=self.p.dim, font=self.p.font_small,
        ).pack(fill="x", padx=8, pady=(0, 6))

        if demo is not None:
            self._build_demo_controls()
        self.view.start()

    # -- Demo --------------------------------------------------------------

    def _build_demo_controls(self) -> None:
        row = tk.Frame(self, bg=self.p.window)
        row.pack(fill="x", padx=8, pady=(0, 8))
        tk.Label(
            row, text="Drehzahl (U/s)", bg=self.p.window,
            fg=self.p.label, font=self.p.font_label,
        ).pack(side="left")
        self.speed = tk.DoubleVar(value=0.5)
        tk.Scale(
            row, from_=-2.0, to=2.0, resolution=0.1, orient="horizontal",
            variable=self.speed, length=240, bg=self.p.window,
            fg=self.p.text, highlightthickness=0,
            troughcolor=self.p.panel,
        ).pack(side="left", padx=6)

        touch = tk.Button(row, text="Beruehren", font=self.p.font_label)
        touch.pack(side="left", padx=6)
        touch.bind("<ButtonPress-1>", lambda _e: self.demo.touch(True))
        touch.bind("<ButtonRelease-1>", lambda _e: self.demo.touch(False))

    def _demo_samples(self) -> Sequence[Sample]:
        seconds = self.view.interval_ms / 1000.0
        return self.demo.burst(self.speed.get() * seconds)

    def destroy(self) -> None:
        self.view.stop()
        super().destroy()


def _no_samples() -> Sequence[Sample]:
    """Kein eigener Messwert - der Scanner wird von aussen gefuellt."""
    return ()
