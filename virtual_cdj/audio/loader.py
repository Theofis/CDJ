"""Track laden: dekodieren, resampeln, analysieren (oder aus dem Cache).

Ergebnis ist ein ``LoadedTrack``: der ``TrackInfo`` fuer den Deck-Zustand und
die Samples fuer die Audio-Stimme. Diese Schicht ist die Bruecke zwischen der
Audio- und der Deck-Schicht.

Laeuft im File-/Analyse-Worker, niemals im GUI- oder Audio-Thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..deck.state import BeatGrid, TrackInfo, WaveformData, WaveformSet
from .analysis.analyzer import TrackAnalysis, TrackAnalyzer
from .cache import AnalysisCache, CacheKey
from .decoder import AudioMetadata, open_decoder
from .format import ENGINE_SAMPLE_RATE, AudioBuffer
from .metrics import AnalysisMetrics
from .resample import resample


@dataclass(frozen=True)
class LoadedTrack:
    """Alles, was Deck und Stimme fuer einen Track brauchen.

    ``analysis`` und ``metadata`` fehlen bei Quellen, die keine Audiodatei
    analysieren - etwa dem Demo-Provider. Der Rest des Programms braucht nur
    ``info`` und ``samples``.
    """

    info: TrackInfo
    buffer: AudioBuffer
    analysis: TrackAnalysis | None = None
    metadata: AudioMetadata | None = None
    from_cache: bool = False

    @property
    def samples(self) -> np.ndarray:
        return self.buffer.samples

    @property
    def is_analysed(self) -> bool:
        return self.analysis is not None


def _read_tags(path: Path) -> dict[str, str]:
    """Titel, Interpret und Co. aus den Datei-Tags lesen."""
    tags = {
        "title": "",
        "artist": "",
        "album": "",
        "genre": "",
        "label": "",
    }
    try:
        import mutagen
    except ImportError:  # pragma: no cover - mutagen ist Abhaengigkeit
        return tags

    try:
        handle = mutagen.File(str(path), easy=True)
    except Exception:
        return tags
    if handle is None:
        return tags

    def first(*keys: str) -> str:
        for key in keys:
            value = handle.get(key)
            if value:
                return str(value[0]) if isinstance(value, list) else str(value)
        return ""

    tags["title"] = first("title")
    tags["artist"] = first("artist", "albumartist")
    tags["album"] = first("album")
    tags["genre"] = first("genre")
    tags["label"] = first("organization", "label")
    return tags


def _to_waveform_set(analysis: TrackAnalysis) -> WaveformSet | None:
    """Analyse-Peaks in das Deck-Datenmodell uebertragen."""
    if not analysis.waveform_levels:
        return None
    levels = {
        name: WaveformData(
            peaks_per_second=peaks.peaks_per_second,
            low=peaks.low,
            mid=peaks.mid,
            high=peaks.high,
            peak=peaks.peak,
            rms=peaks.rms,
        )
        for name, peaks in analysis.waveform_levels.items()
    }
    return WaveformSet(levels=levels)


def _to_beat_grid(analysis: TrackAnalysis) -> BeatGrid | None:
    tempo = analysis.tempo
    if tempo.bpm <= 0:
        return None
    return BeatGrid(
        first_beat_s=tempo.first_beat_s,
        bpm=tempo.bpm,
        beats_per_bar=tempo.beats_per_bar,
        # Die explizite Beatliste wird bewusst **nicht** uebernommen: das
        # gleichmaessige Raster aus dem Linearfit ist praeziser als die auf
        # das Analyseraster gequantelten Einzelbeats. Die Liste bleibt fuer
        # spaetere variable Grids vorgesehen.
        beats_s=(),
        first_downbeat_index=tempo.first_downbeat_index,
    )


class TrackLoader:
    """Laedt Tracks und haelt den Analyse-Cache."""

    def __init__(
        self,
        cache: AnalysisCache | None = None,
        analyzer: TrackAnalyzer | None = None,
        *,
        engine_sample_rate: int = ENGINE_SAMPLE_RATE,
        metrics: AnalysisMetrics | None = None,
    ) -> None:
        self.cache = cache if cache is not None else AnalysisCache()
        self.analyzer = analyzer if analyzer is not None else TrackAnalyzer()
        self.engine_sample_rate = engine_sample_rate
        self.metrics = metrics if metrics is not None else AnalysisMetrics()

    # ------------------------------------------------------------------

    def load(self, path: str | Path, *, use_cache: bool = True) -> LoadedTrack:
        """Track vollstaendig laden.

        Reihenfolge: dekodieren -> auf Engine-Rate bringen -> Analyse aus dem
        Cache oder neu berechnen -> ``TrackInfo`` bauen.
        """
        source = Path(path)
        decoder = open_decoder(source)
        metadata = decoder.load_metadata()

        buffer = resample(decoder.decode(), self.engine_sample_rate)

        key = CacheKey.for_file(source, self.analyzer_version)
        analysis: TrackAnalysis | None = None
        from_cache = False
        if use_cache:
            analysis = self.cache.load(key)
            if analysis is not None:
                from_cache = True
                self.metrics.note_cache_hit()

        if analysis is None:
            analysis = self.analyzer.analyse(buffer)
            self.metrics.note_analysis(analysis.analysis_seconds)
            if use_cache:
                try:
                    self.cache.store(key, analysis)
                except OSError:  # pragma: no cover - Platte voll o. ae.
                    self.cache.stats.errors += 1

        tags = _read_tags(source)
        info = TrackInfo(
            track_id=key.digest[:16],
            title=tags["title"] or source.stem,
            artist=tags["artist"],
            album=tags["album"],
            genre=tags["genre"],
            label=tags["label"],
            duration_s=buffer.duration_s,
            original_bpm=analysis.tempo.bpm,
            key=analysis.key.camelot,
            file_path=str(source),
            source=source.parent.name,
            waveform=_to_waveform_set(analysis),
            beat_grid=_to_beat_grid(analysis),
            key_confidence=analysis.key.confidence,
            downbeat_confidence=analysis.tempo.downbeat_confidence,
            analysis_version=analysis.analysis_version,
        )

        return LoadedTrack(
            info=info,
            buffer=buffer,
            analysis=analysis,
            metadata=metadata,
            from_cache=from_cache,
        )

    @property
    def analyzer_version(self) -> int:
        from .analysis.analyzer import ANALYSIS_VERSION

        return ANALYSIS_VERSION
