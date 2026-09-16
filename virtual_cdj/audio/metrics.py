"""Messwerte statt Bauchgefuehl.

Alle Zaehler sind einfache Attribute. Der Audio-Callback schreibt nur
Skalare - keine Listen, keine Locks, keine Allokationen.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class AudioMetrics:
    """Zustand der Audioausgabe. Wird im Callback fortgeschrieben."""

    #: Von PortAudio gemeldete Aussetzer.
    underruns: int = 0
    overruns: int = 0
    callbacks: int = 0
    #: Dauer des letzten Callbacks in Millisekunden.
    last_callback_ms: float = 0.0
    #: Gleitender Mittelwert der Callback-Dauer.
    average_callback_ms: float = 0.0
    #: Groesster gemessener Wert seit dem Start.
    peak_callback_ms: float = 0.0
    #: Blockgroesse und Rate, wie PortAudio sie tatsaechlich liefert.
    block_frames: int = 0
    sample_rate: int = 0
    #: Anteil der verfuegbaren Zeit, den der Callback braucht (0.0 - 1.0+).
    load: float = 0.0
    active_voices: int = 0
    #: Ungefaehrer Speicherbedarf der geladenen Tracks in MB.
    memory_mb: float = 0.0

    def note_callback(self, duration_ms: float, frames: int) -> None:
        """Im Audio-Callback aufgerufen. Muss billig bleiben."""
        self.callbacks += 1
        self.last_callback_ms = duration_ms
        if duration_ms > self.peak_callback_ms:
            self.peak_callback_ms = duration_ms
        # Exponentielle Glaettung, eine Multiplikation und eine Addition.
        self.average_callback_ms += 0.05 * (
            duration_ms - self.average_callback_ms
        )
        if self.sample_rate > 0 and frames > 0:
            available_ms = frames / self.sample_rate * 1000.0
            self.load = duration_ms / available_ms

    def reset(self) -> None:
        self.underruns = 0
        self.overruns = 0
        self.callbacks = 0
        self.last_callback_ms = 0.0
        self.average_callback_ms = 0.0
        self.peak_callback_ms = 0.0
        self.load = 0.0

    def summary(self) -> str:
        return (
            f"{self.sample_rate} Hz / {self.block_frames} frames · "
            f"{self.average_callback_ms:.2f} ms "
            f"({self.load * 100:.0f} %) · "
            f"{self.underruns} underruns"
        )


@dataclass
class AnalysisMetrics:
    """Laufzeiten und Cache-Trefferquote der Analyse."""

    analysed: int = 0
    cached: int = 0
    failed: int = 0
    last_seconds: float = 0.0
    total_seconds: float = 0.0
    queue_length: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def note_analysis(self, seconds: float) -> None:
        with self._lock:
            self.analysed += 1
            self.last_seconds = seconds
            self.total_seconds += seconds

    def note_cache_hit(self) -> None:
        with self._lock:
            self.cached += 1

    def note_failure(self) -> None:
        with self._lock:
            self.failed += 1

    @property
    def hit_rate(self) -> float:
        total = self.analysed + self.cached
        return self.cached / total if total else 0.0

    def summary(self) -> str:
        return (
            f"{self.analysed} analysiert / {self.cached} aus Cache "
            f"({self.hit_rate * 100:.0f} %)"
        )
