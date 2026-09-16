"""Abschaltbare Entwicklungsanzeige.

Zeigt den echten Deck-Zustand, die Bildrate und die zuletzt eingegangenen
Kommandos. Nur fuer die Entwicklung - auf dem fertigen CDJ ausgeschaltet.
"""

from __future__ import annotations

import time
import tkinter as tk
from collections import deque

from ..deck.commands import DeckCommand
from ..deck.quantize import beat_value_label as _beats
from ..deck.state import DeckState, LoopAdjust
from . import theme

MAX_EVENTS = 8


class CdjDebugOverlay(tk.Canvas):
    """Halbtransparent wirkende Einblendung oben rechts."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(
            master, bg=theme.PANEL, highlightthickness=1, bd=0,
            highlightbackground=theme.ACCENT,
        )
        self.metrics = theme.DEFAULT_METRICS
        self.visible = False
        self._events: deque[str] = deque(maxlen=MAX_EVENTS)
        self._frame_times: deque[float] = deque(maxlen=60)
        self._last_frame = time.perf_counter()
        self.network_status = "lokal"
        #: ``AudioMetrics`` bzw. ``AnalysisMetrics`` der Audioschicht. Reine
        #: Datenobjekte - die Oberflaeche liest nur Attribute.
        self.audio_metrics: object | None = None
        self.analysis_metrics: object | None = None
        #: Klartext zum Ausgabegeraet, z. B. "44100 Hz / 512".
        self.audio_device = ""

    # ------------------------------------------------------------------

    def apply_metrics(self, metrics: theme.Metrics) -> None:
        self.metrics = metrics

    def note_command(self, cmd: DeckCommand) -> None:
        stamp = time.strftime("%H:%M:%S")
        self._events.appendleft(f"{stamp} {cmd}")

    def note_frame(self) -> None:
        now = time.perf_counter()
        delta = now - self._last_frame
        self._last_frame = now
        if delta > 0:
            self._frame_times.append(delta)

    @property
    def fps(self) -> float:
        if not self._frame_times:
            return 0.0
        average = sum(self._frame_times) / len(self._frame_times)
        return 1.0 / average if average > 0 else 0.0

    def toggle(self) -> bool:
        self.visible = not self.visible
        if self.visible:
            # ``Canvas.lift`` ist ``tag_raise`` - hier ist die Fenster-Variante
            # gemeint.
            tk.Misc.lift(self)
        return self.visible

    # ------------------------------------------------------------------

    def render(self, state: DeckState) -> None:
        if not self.visible:
            return
        self.delete("all")
        m = self.metrics
        x = m.px(8)
        y = m.px(6)
        line = m.px(13)

        def row(label: str, value: str, color: str = theme.TEXT) -> None:
            nonlocal y
            self.create_text(
                x, y, text=label, anchor="nw",
                fill=theme.TEXT_DIM, font=m.mono(7),
            )
            self.create_text(
                x + m.px(96), y, text=value, anchor="nw",
                fill=color, font=m.mono(7),
            )
            y += line

        self.create_text(
            x, y, text="DEBUG", anchor="nw",
            fill=theme.ACCENT, font=m.mono(8, "bold"),
        )
        y += line + m.px(2)

        track = state.track
        row("Deck ID", str(state.deck_id))
        row("Track ID", track.track_id if track else "—")
        row("Play State", state.play_state.value)
        row("Position", f"{state.position_s:8.3f} s")
        row("Duration", f"{state.duration_s:8.3f} s")
        row("Original BPM", f"{state.original_bpm:.2f}")
        row("Current BPM", f"{state.current_bpm:.2f}")
        row("Tempo", f"{state.tempo_percent:+.3f} %")
        row("Bar / Beat", f"{state.bar} / {state.beat}")
        row("Beat Phase", f"{state.beat_phase:.3f}")
        loop = state.loop
        adjust = (
            "" if loop.adjust is LoopAdjust.NONE
            else f"  ADJ {loop.adjust.value}"
        )
        row(
            "Loop",
            f"{'ON ' if loop.active else 'off'} "
            f"{loop.label() or '-'}{adjust}",
            theme.LOOP_EDGE if loop.active else theme.TEXT,
        )
        if loop.is_set:
            row(
                "Loop In/Out",
                f"{loop.in_s:.3f} - {loop.out_s:.3f} s",
            )
        # Warum der letzte Loop aufgehoert hat. Ohne diese Zeile sieht ein
        # Ausstieg aus wie "der Loop endet manchmal einfach".
        if not loop.active and loop.exit_reason is not None:
            row("Loop Exit", loop.exit_reason.value, theme.YELLOW)
        row("Sync", "ON" if state.sync else "off",
            theme.SYNC_COLOR if state.sync else theme.TEXT)
        row("Master", "ON" if state.is_master else "off",
            theme.MASTER_COLOR if state.is_master else theme.TEXT)
        row(
            "Quantize",
            f"{'ON ' if state.quantize else 'off'} "
            f"{_beats(state.quantize_beats)}",
            theme.QUANTIZE_COLOR if state.quantize else theme.TEXT,
        )
        row("Slip", "ON" if state.slip else "off",
            theme.YELLOW if state.slip else theme.TEXT)
        # Zwei getrennte Zustaende: der Schalter und die gerade laufende
        # Aktion. Ohne diese Zeile sieht ein Slip-Sprung aus wie ein
        # Positionsfehler.
        slip = state.slip_state
        row(
            "Slip Operation",
            f"ACTIVE {slip.label()}" if slip.active else "INACTIVE",
            theme.YELLOW if slip.active else theme.TEXT,
        )
        row(
            "Slip BG Pos",
            "—" if slip.position_s is None else f"{slip.position_s:8.3f} s",
        )
        row("Jog", f"{state.jog_mode.value} "
                   f"{'TOUCH' if state.jog_touch else '-'}")
        row("Pad Mode", state.pad_mode.value)
        row("Generation", str(state.generation))
        row(
            "Audio",
            state.audio_status.value,
            theme.RED if state.audio_status.value == "NO_BACKEND"
            else theme.GREEN,
        )
        row("Network", self.network_status)
        row("FPS", f"{self.fps:5.1f}",
            theme.GREEN if self.fps >= 50 else theme.YELLOW)

        audio = self.audio_metrics
        if audio is not None:
            y += m.px(4)
            self.create_text(
                x, y, text="AUDIO", anchor="nw",
                fill=theme.ACCENT, font=m.mono(8, "bold"),
            )
            y += line
            if self.audio_device:
                row(
                    "Geraet", self.audio_device,
                    theme.TEXT if getattr(audio, "callbacks", 0)
                    else theme.YELLOW,
                )
            row(
                "Buffer",
                f"{getattr(audio, 'block_frames', 0)} @ "
                f"{getattr(audio, 'sample_rate', 0)} Hz",
            )
            underruns = int(getattr(audio, "underruns", 0))
            row(
                "Underruns", str(underruns),
                theme.GREEN if underruns == 0 else theme.RED,
            )
            row("Callback", f"{getattr(audio, 'average_callback_ms', 0.0):.3f} ms")
            row("Peak cb", f"{getattr(audio, 'peak_callback_ms', 0.0):.3f} ms")
            load = float(getattr(audio, "load", 0.0))
            row(
                "Last", f"{load * 100:5.1f} %",
                theme.GREEN if load < 0.5 else theme.YELLOW,
            )
            row("Voices", str(getattr(audio, "active_voices", 0)))
            row("Speicher", f"{getattr(audio, 'memory_mb', 0.0):.1f} MB")

        analysis = self.analysis_metrics
        if analysis is not None:
            y += m.px(4)
            self.create_text(
                x, y, text="ANALYSE", anchor="nw",
                fill=theme.ACCENT, font=m.mono(8, "bold"),
            )
            y += line
            row("analysiert", str(getattr(analysis, "analysed", 0)))
            row("aus Cache", str(getattr(analysis, "cached", 0)))
            row("Trefferrate", f"{getattr(analysis, 'hit_rate', 0.0) * 100:.0f} %")
            row("letzte", f"{getattr(analysis, 'last_seconds', 0.0):.2f} s")
            row("Warteschlange", str(getattr(analysis, "queue_length", 0)))
            failed = int(getattr(analysis, "failed", 0))
            row(
                "Fehler", str(failed),
                theme.GREEN if failed == 0 else theme.RED,
            )

        y += m.px(4)
        self.create_text(
            x, y, text="INPUT", anchor="nw",
            fill=theme.ACCENT, font=m.mono(8, "bold"),
        )
        y += line
        for entry in self._events:
            self.create_text(
                x, y, text=entry, anchor="nw",
                fill=theme.TEXT_DIM, font=m.mono(6),
            )
            y += m.px(10)
