"""Debug-Bereich: das jeweils letzte Ereignis im Detail."""

from __future__ import annotations

import datetime as dt
import tkinter as tk

from ..core.model import EventType, InputEvent
from . import theme

FIELDS = (
    ("Input", "control_id"),
    ("Type", "control_type"),
    ("Event", "event"),
    ("Value", "value"),
    ("Delta", "delta"),
    ("Direction", "direction"),
    ("Position", "position"),
    ("Raw", "raw"),
    ("Source", "source"),
    ("Timestamp", "timestamp"),
)


class DebugView(tk.Frame):
    """Zeigt alle Felder des letzten Eingabeereignisses."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=theme.BG_SIDE, padx=10, pady=8)
        self._values: dict[str, tk.StringVar] = {}

        tk.Label(
            self, text="LETZTES EREIGNIS", bg=theme.BG_SIDE,
            fg=theme.ACCENT, font=theme.FONT_HEAD, anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        for row, (label, key) in enumerate(FIELDS, start=1):
            tk.Label(
                self, text=label, bg=theme.BG_SIDE, fg=theme.FG_DIM,
                font=theme.FONT_BODY, anchor="w", width=10,
            ).grid(row=row, column=0, sticky="w")
            var = tk.StringVar(value="-")
            self._values[key] = var
            tk.Label(
                self, textvariable=var, bg=theme.BG_SIDE, fg=theme.FG_TEXT,
                font=theme.FONT_MONO, anchor="w",
            ).grid(row=row, column=1, sticky="w")

        row = len(FIELDS) + 1
        tk.Label(
            self, text="Meta", bg=theme.BG_SIDE, fg=theme.FG_DIM,
            font=theme.FONT_BODY, anchor="nw", width=10,
        ).grid(row=row, column=0, sticky="nw", pady=(4, 0))
        self._meta = tk.StringVar(value="-")
        tk.Label(
            self, textvariable=self._meta, bg=theme.BG_SIDE,
            fg=theme.FG_LABEL, font=theme.FONT_MONO, anchor="w",
            justify="left", wraplength=250,
        ).grid(row=row, column=1, sticky="w", pady=(4, 0))

        row += 1
        tk.Label(
            self, text="Gehalten", bg=theme.BG_SIDE, fg=theme.FG_DIM,
            font=theme.FONT_BODY, anchor="nw", width=10,
        ).grid(row=row, column=0, sticky="nw", pady=(8, 0))
        self._chord = tk.StringVar(value="-")
        tk.Label(
            self, textvariable=self._chord, bg=theme.BG_SIDE,
            fg=theme.GREEN, font=theme.FONT_MONO, anchor="w",
            justify="left", wraplength=250,
        ).grid(row=row, column=1, sticky="w", pady=(8, 0))

    # ------------------------------------------------------------------

    def show(self, event: InputEvent) -> None:
        self._values["control_id"].set(event.control_id)
        self._values["control_type"].set(event.control_type.value)
        self._values["event"].set(event.event.value)
        self._values["value"].set(_format_value(event))
        self._values["delta"].set(
            "-" if event.delta is None else f"{event.delta:+d}"
        )
        self._values["direction"].set(
            "-" if event.direction is None else event.direction.value
        )
        self._values["position"].set(event.position or "-")
        self._values["raw"].set("-" if event.raw is None else str(event.raw))
        self._values["source"].set(event.source.value)
        self._values["timestamp"].set(_format_time(event.timestamp))
        self._meta.set(
            ", ".join(f"{k}={v}" for k, v in event.meta.items()) or "-"
        )

    def show_chord(self, pressed: tuple[str, ...]) -> None:
        self._chord.set("\n".join(pressed) if pressed else "-")


def _format_value(event: InputEvent) -> str:
    if event.value is None:
        return "-"
    if event.event in (EventType.PRESS, EventType.RELEASE, EventType.POSITION):
        return str(int(event.value))
    return f"{event.value:.3f}"


def _format_time(timestamp_ms: float) -> str:
    stamp = dt.datetime.fromtimestamp(timestamp_ms / 1000.0)
    return stamp.strftime("%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}"
