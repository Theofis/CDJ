"""Input-Monitor: fortlaufende Liste der letzten Eingabeereignisse."""

from __future__ import annotations

import datetime as dt
import json
import tkinter as tk
from pathlib import Path

from ..core.model import EventType, InputEvent
from . import theme

MAX_LINES = 400


class MonitorView(tk.Frame):
    """Scrollende Ereignisliste mit Filter, Pause und Export."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=theme.BG_SIDE, padx=10, pady=8)
        self._events: list[InputEvent] = []
        self._paused = tk.BooleanVar(value=False)
        self._hide_streams = tk.BooleanVar(value=False)
        self._count = tk.StringVar(value="0 Ereignisse")

        header = tk.Frame(self, bg=theme.BG_SIDE)
        header.pack(fill="x")
        tk.Label(
            header, text="INPUT MONITOR", bg=theme.BG_SIDE,
            fg=theme.ACCENT, font=theme.FONT_HEAD,
        ).pack(side="left")
        tk.Label(
            header, textvariable=self._count, bg=theme.BG_SIDE,
            fg=theme.FG_DIM, font=theme.FONT_SMALL,
        ).pack(side="right")

        body = tk.Frame(self, bg=theme.BG_SIDE)
        body.pack(fill="both", expand=True, pady=(6, 6))
        self.text = tk.Text(
            body, height=18, width=44, bg=theme.MONITOR_BG,
            fg=theme.MONITOR_FG, font=theme.FONT_MONO,
            insertbackground=theme.FG_TEXT, relief="flat",
            wrap="none", state="disabled",
        )
        scroll = tk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.text.tag_configure("press", foreground=theme.GREEN)
        self.text.tag_configure("release", foreground="#e57373")
        self.text.tag_configure("value", foreground="#ffd54f")
        self.text.tag_configure("move", foreground=theme.ACCENT)
        self.text.tag_configure("other", foreground=theme.MONITOR_FG)

        controls_row = tk.Frame(self, bg=theme.BG_SIDE)
        controls_row.pack(fill="x")
        tk.Checkbutton(
            controls_row, text="Pause", variable=self._paused,
            bg=theme.BG_SIDE, fg=theme.FG_LABEL, selectcolor=theme.BG_SIDE,
            activebackground=theme.BG_SIDE, activeforeground=theme.FG_TEXT,
            font=theme.FONT_SMALL, highlightthickness=0, bd=0,
        ).pack(side="left")
        tk.Checkbutton(
            controls_row, text="Jog/Analog ausblenden",
            variable=self._hide_streams, command=self._redraw,
            bg=theme.BG_SIDE, fg=theme.FG_LABEL, selectcolor=theme.BG_SIDE,
            activebackground=theme.BG_SIDE, activeforeground=theme.FG_TEXT,
            font=theme.FONT_SMALL, highlightthickness=0, bd=0,
        ).pack(side="left", padx=(8, 0))
        tk.Button(
            controls_row, text="Leeren", command=self.clear,
            bg=theme.BTN_FACE, fg=theme.FG_TEXT, font=theme.FONT_SMALL,
            relief="flat", padx=8,
        ).pack(side="right")
        tk.Button(
            controls_row, text="Export JSONL", command=self.export,
            bg=theme.BTN_FACE, fg=theme.FG_TEXT, font=theme.FONT_SMALL,
            relief="flat", padx=8,
        ).pack(side="right", padx=(0, 6))

    # ------------------------------------------------------------------

    def add(self, event: InputEvent) -> None:
        self._events.append(event)
        if len(self._events) > MAX_LINES:
            del self._events[: len(self._events) - MAX_LINES]
        self._count.set(f"{len(self._events)} Ereignisse")
        if self._paused.get():
            return
        if self._is_hidden(event):
            return
        self._append_line(event)

    def clear(self) -> None:
        self._events.clear()
        self._count.set("0 Ereignisse")
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def export(self, path: str | Path = "input_monitor.jsonl") -> Path:
        path = Path(path)
        with path.open("w", encoding="utf-8") as handle:
            for event in self._events:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return path

    # ------------------------------------------------------------------

    def _is_hidden(self, event: InputEvent) -> bool:
        return self._hide_streams.get() and event.event in (
            EventType.MOVE,
            EventType.VALUE,
            EventType.AXIS,
        )

    def _redraw(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        for event in self._events:
            if not self._is_hidden(event):
                self._append_line(event)

    def _append_line(self, event: InputEvent) -> None:
        line = format_line(event)
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n", _tag_for(event))
        # Zeilen begrenzen, damit das Widget nicht unbegrenzt waechst.
        if int(self.text.index("end-1c").split(".")[0]) > MAX_LINES:
            self.text.delete("1.0", "2.0")
        self.text.see("end")
        self.text.configure(state="disabled")


def format_line(event: InputEvent) -> str:
    stamp = dt.datetime.fromtimestamp(event.timestamp / 1000.0)
    time_text = stamp.strftime("%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}"
    src = "V" if event.source.value == "VIRTUAL" else event.source.value[0]
    return f"{time_text} {src} {event.control_id:<24} {event.describe()}"


def _tag_for(event: InputEvent) -> str:
    return {
        EventType.PRESS: "press",
        EventType.RELEASE: "release",
        EventType.VALUE: "value",
        EventType.MOVE: "move",
        EventType.ROTATE: "move",
    }.get(event.event, "other")
