"""Dialog zum Bearbeiten eines Tasters im virtuellen Bedienfeld."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from ..core import controls
from ..core.button_customization import (
    ButtonCustomizationStore,
    ButtonLedColor,
)
from ..core.model import Control
from . import theme


class ButtonEditorDialog(tk.Toplevel):
    """Bearbeitet sichtbaren Namen, Konsolentext und LED eines Tasters."""

    def __init__(
        self,
        master: tk.Misc,
        control: Control,
        store: ButtonCustomizationStore,
    ) -> None:
        super().__init__(master)
        self.control = control
        self.store = store
        self.original = controls.get(control.id)
        self.title(f"Button bearbeiten - {control.id}")
        self.configure(bg=theme.BG_SIDE)
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())

        entry = store.get(control.id)
        self.name_var = tk.StringVar(
            value=(
                entry.display_name
                or self.original.short
                or self.original.label
            )
        )
        self.output_var = tk.StringVar(value=entry.console_output)
        self.led_var = tk.StringVar(
            value=store.led_color(self.original).value
        )

        body = tk.Frame(self, bg=theme.BG_SIDE, padx=16, pady=14)
        body.pack(fill="both", expand=True)
        tk.Label(
            body,
            text="BUTTON BEARBEITEN",
            bg=theme.BG_SIDE,
            fg=theme.ACCENT,
            font=theme.FONT_HEAD,
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))

        self._label(body, "Technische ID", 1)
        tk.Label(
            body,
            text=control.id,
            bg=theme.BG_SIDE,
            fg=theme.FG_DIM,
            font=theme.FONT_MONO,
            anchor="w",
        ).grid(row=1, column=1, sticky="ew", padx=(10, 0))

        self._label(body, "Name am Bedienfeld", 2)
        self.name_entry = self._entry(body, self.name_var, 2)

        self._label(body, "Konsolenausgabe", 3)
        self.output_entry = self._entry(body, self.output_var, 3)

        self._label(body, "Button-LED", 4)
        led_row = tk.Frame(body, bg=theme.BG_SIDE)
        led_row.grid(row=4, column=1, sticky="w", padx=(6, 0), pady=4)
        led_options = (
            ("Keine LED", ButtonLedColor.NONE, theme.FG_DIM),
            ("Blau", ButtonLedColor.BLUE, theme.BUTTON_LED_COLORS["BLUE"][0]),
            (
                "Orange",
                ButtonLedColor.ORANGE,
                theme.BUTTON_LED_COLORS["ORANGE"][0],
            ),
            (
                "Grün",
                ButtonLedColor.GREEN,
                theme.BUTTON_LED_COLORS["GREEN"][0],
            ),
        )
        for label, color, foreground in led_options:
            tk.Radiobutton(
                led_row,
                text=label,
                value=color.value,
                variable=self.led_var,
                bg=theme.BG_SIDE,
                fg=foreground,
                selectcolor=theme.MONITOR_BG,
                activebackground=theme.BG_SIDE,
                activeforeground=foreground,
                font=theme.FONT_SMALL,
                highlightthickness=0,
                bd=0,
            ).pack(side="left", padx=(4, 0))

        tk.Label(
            body,
            text=(
                "Der Konsolentext wird beim Druecken exakt einmal ausgegeben.\n"
                "Ein leeres Feld verwendet wieder die normale Backend-Ausgabe."
            ),
            bg=theme.BG_SIDE,
            fg=theme.FG_DIM,
            font=theme.FONT_SMALL,
            justify="left",
            anchor="w",
        ).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 12))

        buttons = tk.Frame(body, bg=theme.BG_SIDE)
        buttons.grid(row=6, column=0, columnspan=2, sticky="ew")
        tk.Button(
            buttons,
            text="Speichern",
            command=self.save,
            bg=theme.BTN_FACE,
            fg=theme.FG_TEXT,
            font=theme.FONT_BODY,
            relief="flat",
            padx=10,
        ).pack(side="left")
        tk.Button(
            buttons,
            text="Standard wiederherstellen",
            command=self.reset,
            bg=theme.BTN_FACE,
            fg=theme.FG_TEXT,
            font=theme.FONT_BODY,
            relief="flat",
            padx=10,
        ).pack(side="left", padx=(8, 0))
        tk.Button(
            buttons,
            text="Abbrechen",
            command=self.destroy,
            bg=theme.BTN_FACE,
            fg=theme.FG_TEXT,
            font=theme.FONT_BODY,
            relief="flat",
            padx=10,
        ).pack(side="right")

        body.grid_columnconfigure(1, weight=1)
        self.bind("<Return>", lambda _event: self.save())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.name_entry.focus_set()
        self.name_entry.selection_range(0, "end")
        self.update_idletasks()
        self._centre_over_parent()
        self.grab_set()

    def _label(self, master: tk.Misc, text: str, row: int) -> None:
        tk.Label(
            master,
            text=text,
            bg=theme.BG_SIDE,
            fg=theme.FG_LABEL,
            font=theme.FONT_BODY,
            anchor="w",
        ).grid(row=row, column=0, sticky="w", pady=4)

    def _entry(
        self, master: tk.Misc, variable: tk.StringVar, row: int
    ) -> tk.Entry:
        entry = tk.Entry(
            master,
            textvariable=variable,
            width=42,
            bg=theme.MONITOR_BG,
            fg=theme.FG_TEXT,
            insertbackground=theme.FG_TEXT,
            font=theme.FONT_MONO,
            relief="flat",
        )
        entry.grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=4)
        return entry

    def save(self) -> bool:
        name = self.name_var.get().strip()
        default_name = self.original.short or self.original.label
        if name == default_name:
            name = ""
        led_color = self.led_var.get()
        if led_color == self.store.default_led_color(self.original).value:
            led_color = ""
        try:
            self.store.set(
                self.control.id,
                display_name=name,
                console_output=self.output_var.get(),
                led_color=led_color,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Button nicht gespeichert", str(exc), parent=self)
            return False
        self.destroy()
        return True

    def reset(self) -> bool:
        try:
            self.store.reset(self.control.id)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Button nicht zurueckgesetzt", str(exc), parent=self)
            return False
        self.destroy()
        return True

    def _centre_over_parent(self) -> None:
        parent = self.master.winfo_toplevel()
        x = parent.winfo_rootx() + max(
            0, (parent.winfo_width() - self.winfo_reqwidth()) // 2
        )
        y = parent.winfo_rooty() + max(
            0, (parent.winfo_height() - self.winfo_reqheight()) // 2
        )
        self.geometry(f"+{x}+{y}")
