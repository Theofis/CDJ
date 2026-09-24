"""Kleine, vom Produktfenster getrennte Simulatoroberfläche."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .fake_player import FakePlayer


class SimulatorWindow(tk.Tk):
    def __init__(self, player: FakePlayer, endpoint: str) -> None:
        super().__init__()
        self.player = player
        self.title("PRO LINK SIMULATOR")
        self.geometry("430x520")
        self.resizable(False, False)

        self.online = tk.BooleanVar(value=player.online)
        self.master = tk.BooleanVar(value=player.is_master)
        self.sync = tk.BooleanVar(value=player.sync_enabled)
        self.on_air = tk.BooleanVar(value=player.on_air)
        self.bpm = tk.DoubleVar(value=player.effective_bpm)
        self.track = tk.StringVar(value=player.track_id)
        self.status = tk.StringVar(value=endpoint)
        self.position = tk.DoubleVar(value=0.0)

        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="PRO LINK SIMULATOR", font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        ttk.Label(root, textvariable=self.status).pack(anchor="w", pady=(0, 12))

        form = ttk.Frame(root)
        form.pack(fill="x")
        self._row(form, 0, "Player", str(player.player_id))
        self._row(form, 1, "Device", player.device_name)
        ttk.Label(form, text="Track").grid(row=2, column=0, sticky="w", pady=4)
        entry = ttk.Entry(form, textvariable=self.track)
        entry.grid(row=2, column=1, sticky="ew", pady=4)
        entry.bind("<Return>", lambda _event: self._apply_track())
        self._row(form, 3, "Original BPM", f"{player.original_bpm:.2f}")
        ttk.Label(form, text="Current BPM").grid(row=4, column=0, sticky="w", pady=4)
        bpm_box = ttk.Spinbox(
            form, from_=40.0, to=300.0, increment=0.1,
            textvariable=self.bpm, command=self._apply_bpm,
        )
        bpm_box.grid(row=4, column=1, sticky="ew", pady=4)
        bpm_box.bind("<Return>", lambda _event: self._apply_bpm())
        bpm_box.bind("<FocusOut>", lambda _event: self._apply_bpm())
        form.columnconfigure(1, weight=1)

        checks = ttk.Frame(root)
        checks.pack(fill="x", pady=12)
        ttk.Checkbutton(checks, text="Online", variable=self.online, command=self._apply_online).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(checks, text="Master", variable=self.master, command=self._apply_master).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(checks, text="Sync", variable=self.sync, command=self._apply_sync).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(checks, text="On Air", variable=self.on_air, command=self._apply_on_air).grid(row=1, column=1, sticky="w")

        controls = ttk.Frame(root)
        controls.pack(fill="x", pady=(0, 10))
        ttk.Button(controls, text="PLAY", command=player.play).pack(side="left", expand=True, fill="x")
        ttk.Button(controls, text="PAUSE", command=player.pause).pack(side="left", expand=True, fill="x", padx=(8, 0))
        ttk.Button(controls, text="BPM -", command=lambda: self._nudge_bpm(-0.1)).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="BPM +", command=lambda: self._nudge_bpm(+0.1)).pack(side="left", padx=(4, 0))

        ttk.Label(root, text="Position").pack(anchor="w")
        ttk.Scale(
            root,
            from_=0.0,
            to=float(player.duration_ms),
            variable=self.position,
            command=self._seek,
        ).pack(fill="x")
        self.live = ttk.Label(root, text="")
        self.live.pack(anchor="w", pady=(10, 0))
        self.after(50, self._refresh)

    @staticmethod
    def _row(parent: ttk.Frame, row: int, label: str, value: str) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Label(parent, text=value).grid(row=row, column=1, sticky="w", pady=4)

    def _apply_track(self) -> None:
        self.player.set_track(self.track.get().strip() or "unnamed-track")

    def _apply_bpm(self) -> None:
        try:
            self.player.set_effective_bpm(float(self.bpm.get()))
        except (ValueError, tk.TclError):
            self.bpm.set(self.player.effective_bpm)

    def _nudge_bpm(self, amount: float) -> None:
        self.bpm.set(round(self.player.effective_bpm + amount, 2))
        self._apply_bpm()

    def _apply_online(self) -> None:
        self.player.set_online(self.online.get())
        if not self.online.get():
            self.master.set(False)

    def _apply_master(self) -> None:
        self.player.set_master(self.master.get())

    def _apply_sync(self) -> None:
        self.player.set_sync(self.sync.get())

    def _apply_on_air(self) -> None:
        self.player.set_on_air(self.on_air.get())

    def _seek(self, value: str) -> None:
        try:
            self.player.set_position_ms(float(value))
        except ValueError:
            pass

    def _refresh(self) -> None:
        state = self.player.snapshot()
        # Während Wiedergabe folgt der Regler der monotonen Playerzeit.
        if state.playing:
            self.position.set(state.position_ms or 0.0)
        position = state.position_ms or 0.0
        minutes, seconds = divmod(position / 1000.0, 60.0)
        self.live.configure(
            text=(
                f"Position {int(minutes):02d}:{seconds:06.3f}\n"
                f"Beat {state.beat_number} / {state.beat_in_bar}\n"
                f"Pitch {state.pitch_percent:+.3f}%   "
                f"{'PLAY' if state.playing else 'PAUSE'}"
            )
        )
        self.after(50, self._refresh)
