"""Orchestrierung der Track-Analyse.

Nimmt einen dekodierten Puffer und liefert ein ``TrackAnalysis``. Laeuft
ausschliesslich im Analyse-Worker, niemals im GUI- oder Audio-Thread.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..format import AudioBuffer
from ..resample import resample_mono
from . import key as key_module
from . import tempo as tempo_module
from . import waveform as waveform_module

#: Version des Analysealgorithmus. Muss erhoeht werden, sobald sich das
#: Ergebnis eines Verfahrens aendert - dann verwirft der Cache alte Ergebnisse.
ANALYSIS_VERSION = 4


@dataclass(frozen=True)
class TrackAnalysis:
    """Vollstaendiges Analyseergebnis eines Tracks."""

    duration_s: float
    sample_rate: int
    channels: int

    tempo: tempo_module.TempoAnalysis
    key: key_module.KeyAnalysis
    #: Aufloesungsstufe -> Peaks. Schluessel siehe ``waveform.LEVELS``.
    waveform_levels: dict[str, waveform_module.BandPeaks] = field(
        default_factory=dict
    )

    peak: float = 0.0
    analysis_version: int = ANALYSIS_VERSION
    #: Wie lange die Analyse gedauert hat, in Sekunden.
    analysis_seconds: float = 0.0

    # ------------------------------------------------------------------

    @property
    def bpm(self) -> float:
        return self.tempo.bpm

    def level(self, name: str) -> waveform_module.BandPeaks | None:
        return self.waveform_levels.get(name)

    def level_for(self, seconds_per_pixel: float) -> waveform_module.BandPeaks | None:
        """Passende Aufloesungsstufe fuer eine Zoomstufe waehlen.

        Gesucht ist die feinste Stufe, die noch mindestens einen Peak pro
        Pixel liefert - feiner brauchen wir nicht, groeber wuerde stufig.
        """
        if not self.waveform_levels:
            return None
        if seconds_per_pixel <= 0:
            return self.finest_level()
        needed = 1.0 / seconds_per_pixel
        candidates = sorted(
            self.waveform_levels.values(), key=lambda p: p.peaks_per_second
        )
        for peaks in candidates:
            if peaks.peaks_per_second >= needed:
                return peaks
        return candidates[-1]

    def finest_level(self) -> waveform_module.BandPeaks | None:
        if not self.waveform_levels:
            return None
        return max(
            self.waveform_levels.values(), key=lambda p: p.peaks_per_second
        )

    @property
    def nbytes(self) -> int:
        total = sum(peaks.nbytes for peaks in self.waveform_levels.values())
        return total + int(self.tempo.beats_s.nbytes)


class TrackAnalyzer:
    """Fuehrt die Analyse durch. Zustandslos und damit wiederverwendbar."""

    def __init__(
        self,
        *,
        analysis_sample_rate: int = tempo_module.ANALYSIS_SAMPLE_RATE,
        analyse_key: bool = True,
        waveform_levels: dict[str, float] | None = None,
    ) -> None:
        self.analysis_sample_rate = analysis_sample_rate
        self.analyse_key = analyse_key
        self.waveform_levels = waveform_levels or waveform_module.LEVELS

    # ------------------------------------------------------------------

    def analyse(self, buffer: AudioBuffer) -> TrackAnalysis:
        """Puffer im internen Format analysieren."""
        started = time.perf_counter()
        mono_full = buffer.mono()

        # Waveform in der Originalrate - die Peaks sollen zur Wiedergabe
        # passen, nicht zur Analyserate.
        levels = waveform_module.compute_levels(
            mono_full, buffer.sample_rate, self.waveform_levels
        )

        # Rhythmus und Tonart in reduzierter Rate: gleiche Ergebnisse,
        # deutlich schneller.
        mono_analysis = resample_mono(
            mono_full, buffer.sample_rate, self.analysis_sample_rate
        )
        tempo_result = tempo_module.analyse_tempo(
            mono_analysis, self.analysis_sample_rate
        )
        key_result = (
            key_module.analyse_key(mono_analysis, self.analysis_sample_rate)
            if self.analyse_key
            else key_module.KeyAnalysis()
        )

        return TrackAnalysis(
            duration_s=buffer.duration_s,
            sample_rate=buffer.sample_rate,
            channels=2,
            tempo=tempo_result,
            key=key_result,
            waveform_levels=levels,
            peak=buffer.peak,
            analysis_version=ANALYSIS_VERSION,
            analysis_seconds=round(time.perf_counter() - started, 3),
        )
