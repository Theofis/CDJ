"""Hauptfenster des virtuellen CDJ.

Verdrahtung:

    PanelView  ->  VirtualSource  ->  InputLayer  ->  CdjController
                                          |
                                          +->  DebugView
                                          +->  MonitorView

Die Ansichten haengen als Abonnenten an der Input-Schicht, nicht am Panel.
Ein spaeter erzeugtes Hardware-Ereignis landet daher in genau denselben
Ansichten.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path

from ..core import controls, hardware_map
from ..core.button_customization import ButtonCustomizationStore
from ..core.controller import CdjController
from ..core.input_layer import InputLayer
from ..core.model import Control, InputEvent, Status
from ..sources.virtual import VirtualSource
from . import theme
from .debug_view import DebugView
from .monitor_view import MonitorView
from .panel import PanelView

LEGEND = (
    "Linksklick = druecken/halten   |   Rechtsklick = rasten "
    "(fuer Tastenkombinationen)   |   Mausrad = Encoder / Jog / Fader   |   "
    "Strg+Mausrad ueber Jog = schnelle Bewegung"
    "   |   F12 = Bearbeitungsmodus"
)


class VirtualCdjApp(tk.Tk):
    def __init__(
        self,
        *,
        button_settings_path: str | Path | None = None,
        console_output: Callable[[str], None] = print,
    ) -> None:
        super().__init__()
        self.title("Virtueller CDJ - Hardware-Simulator")
        self.configure(bg=theme.BG_WINDOW)
        self.minsize(900, 700)

        self.input_layer = InputLayer()
        self.button_customizations = ButtonCustomizationStore(
            button_settings_path, output=console_output
        )
        self._unsubscribe_button_output = self.input_layer.subscribe(
            self.button_customizations.emit
        )
        self.source = VirtualSource(self.input_layer)
        self.source.start()
        self.controller = CdjController(self.input_layer)
        self.mapping = hardware_map.load()
        self._tick_id: str | None = None
        self.edit_mode_var = tk.BooleanVar(value=False)

        self._build_layout()
        self.bind("<F12>", lambda _event: self.toggle_edit_mode())

        self.input_layer.subscribe(self._on_event)
        self._tick()

    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        root = tk.Frame(self, bg=theme.BG_WINDOW)
        root.pack(fill="both", expand=True)

        left = tk.Frame(root, bg=theme.BG_WINDOW)
        left.pack(side="left", fill="both", expand=False)

        self.panel = PanelView(
            left,
            self.source,
            on_hover=self._on_hover,
            customizations=self.button_customizations,
            on_edit=self._edit_button,
        )
        self.panel.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        self.hover_var = tk.StringVar(value="")
        tk.Label(
            left, textvariable=self.hover_var, bg=theme.BG_WINDOW,
            fg=theme.FG_LABEL, font=theme.FONT_SMALL, anchor="w",
            justify="left", wraplength=theme.PANEL_CANVAS_SIZE[0],
        ).pack(fill="x", padx=10, pady=(2, 6))

        right = tk.Frame(root, bg=theme.BG_SIDE)
        right.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=8)

        self.debug = DebugView(right)
        self.debug.pack(fill="x")

        tk.Frame(right, bg=theme.BG_PANEL_EDGE, height=1).pack(
            fill="x", padx=10, pady=4
        )

        self.monitor = MonitorView(right)
        self.monitor.pack(fill="both", expand=True)

        tk.Frame(right, bg=theme.BG_PANEL_EDGE, height=1).pack(
            fill="x", padx=10, pady=4
        )

        self._build_status(right)

        tk.Label(
            self, text=LEGEND, bg=theme.BG_WINDOW, fg=theme.FG_DIM,
            font=theme.FONT_SMALL, anchor="w", justify="left",
        ).pack(fill="x", padx=10, pady=(0, 6))

    def _build_status(self, master: tk.Misc) -> None:
        frame = tk.Frame(master, bg=theme.BG_SIDE, padx=10, pady=6)
        frame.pack(fill="x")

        tk.Label(
            frame, text="STATUS", bg=theme.BG_SIDE, fg=theme.ACCENT,
            font=theme.FONT_HEAD, anchor="w",
        ).pack(fill="x")

        inputs = len(controls.INPUT_CONTROLS)
        outputs = len(controls.OUTPUT_CONTROLS)
        unresolved = len(controls.unresolved())
        assigned = len(self.mapping.assigned())

        lines = [
            f"Bedienelemente: {inputs} Eingaenge, {outputs} Anzeigen",
            f"davon ungeklaert: {unresolved}",
            f"Hardware zugeordnet: {assigned} / {len(self.mapping)}",
            f"Funktionen zugeordnet: {len(self.controller.assigned_functions())}"
            f" / {inputs}",
        ]
        for line in lines:
            tk.Label(
                frame, text=line, bg=theme.BG_SIDE, fg=theme.FG_LABEL,
                font=theme.FONT_SMALL, anchor="w",
            ).pack(fill="x")

        buttons = tk.Frame(frame, bg=theme.BG_SIDE)
        buttons.pack(fill="x", pady=(6, 0))
        tk.Button(
            buttons, text="Rastungen loesen",
            command=self.panel.release_all_latches,
            bg=theme.BTN_FACE, fg=theme.FG_TEXT, font=theme.FONT_SMALL,
            relief="flat", padx=8,
        ).pack(side="left")
        tk.Button(
            buttons, text="LED-Test",
            command=self._led_test,
            bg=theme.BTN_FACE, fg=theme.FG_TEXT, font=theme.FONT_SMALL,
            relief="flat", padx=8,
        ).pack(side="left", padx=(6, 0))
        tk.Checkbutton(
            buttons,
            text="Bearbeitungsmodus",
            variable=self.edit_mode_var,
            command=self._apply_edit_mode,
            bg=theme.BG_SIDE,
            fg=theme.FG_LABEL,
            selectcolor=theme.BG_SIDE,
            activebackground=theme.BG_SIDE,
            activeforeground=theme.FG_TEXT,
            font=theme.FONT_SMALL,
            highlightthickness=0,
            bd=0,
        ).pack(side="left", padx=(8, 0))

    # ------------------------------------------------------------------

    def _on_event(self, event: InputEvent) -> None:
        self.debug.show(event)
        self.debug.show_chord(self.input_layer.state.pressed_ids())
        self.monitor.add(event)

    def _on_hover(self, control: Control | None) -> None:
        if control is None:
            self.hover_var.set("")
            return
        entry = self.mapping.get(control.id)
        hardware = "nicht zugeordnet"
        if entry is not None and entry.hardware is not None:
            hardware = str(entry.hardware)
        text = (
            f"{control.id}  |  {control.label}  |  {control.type.value}  |  "
            f"Gruppe {control.group}  |  Hardware: {hardware}"
        )
        if control.status is Status.UNRESOLVED:
            text += f"\nUNGEKLAERT: {control.note}"
        self.hover_var.set(text)

    def _led_test(self) -> None:
        """Alle LEDs kurz einschalten, um die Ausgangsrichtung zu pruefen."""
        state = self.input_layer.state
        target = not all(state.led(cid) for cid in controls.LED_CONTROLS)
        for control_id in controls.LED_CONTROLS:
            self.input_layer.set_led(control_id, target)
        self.panel.refresh()

    def toggle_edit_mode(self) -> bool:
        enabled = not self.edit_mode_var.get()
        self.edit_mode_var.set(enabled)
        self._apply_edit_mode()
        return enabled

    def _apply_edit_mode(self) -> None:
        self.panel.set_edit_mode(self.edit_mode_var.get())

    def _edit_button(self, control: Control) -> None:
        from .button_editor import ButtonEditorDialog

        ButtonEditorDialog(self, control, self.button_customizations)

    def _tick(self) -> None:
        # Gesammelte Jog-Bewegung weitergeben - genau wie der Bildtakt beim
        # echten Geraet den Positionszaehler abholt. Ein Ereignis je Bild,
        # nicht eines je Mausbewegung.
        self.source.poll_jog()
        self.panel.refresh()
        self._tick_id = self.after(40, self._tick)

    def destroy(self) -> None:
        if self._tick_id is not None:
            self.after_cancel(self._tick_id)
            self._tick_id = None
        self.controller.close()
        self._unsubscribe_button_output()
        super().destroy()


def main() -> None:
    VirtualCdjApp().mainloop()
