"""Track laden: dekodieren, resampeln, analysieren (oder aus dem Cache).

Ergebnis ist ein ``LoadedTrack``: der ``TrackInfo`` fuer den Deck-Zustand und
die Samples fuer die Audio-Stimme. Diese Schicht ist die Bruecke zwischen der
Audio- und der Deck-Schicht.

Laeuft im File-/Analyse-Worker, niemals im GUI- oder Audio-Thread.

Zwei Schritte, nicht einer
--------------------------
::

    Track laden
      |
      +-- Audioanalyse   Waveform, Peaks, BPM, Tonart   <- muss klappen
      |
      +-- Metadaten      Titel, Interpret, Genre, ...   <- darf ausfallen

Der zweite Schritt ist **optional**. Faellt er aus, ist der Track trotzdem
geladen und analysiert; die fehlenden Felder bleiben leer, und der Grund
steht in ``LoadedTrack.metadata_error``. Warum das noetig ist, steht in
``metadata.py`` - kurz: das Tag-Lesen hat den ganzen Prozess mitgenommen.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..deck.state import BeatGrid, TrackInfo, WaveformData, WaveformSet
from .analysis.analyzer import TrackAnalysis, TrackAnalyzer
from .cache import AnalysisCache, CacheKey
from .decoder import AudioMetadata, open_decoder
from .format import ENGINE_SAMPLE_RATE, AudioBuffer
from .metadata import TagReadResult, TrackTags, read_tags
from .metrics import AnalysisMetrics
from .resample import resample

log = logging.getLogger(__name__)


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
    #: Grund, warum die Datei-Tags nicht gelesen werden konnten. Leer
    #: heisst: gelesen, oder gar nicht noetig gewesen. Ein Wert hier ist
    #: **kein** Ladefehler - die Analyse ist trotzdem vollstaendig.
    metadata_error: str = ""

    @property
    def has_metadata_error(self) -> bool:
        return bool(self.metadata_error)

    @property
    def samples(self) -> np.ndarray:
        return self.buffer.samples

    @property
    def is_analysed(self) -> bool:
        return self.analysis is not None


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
        #: Austauschbar, damit Tests ohne Prozessstart auskommen.
        self.tag_reader: Callable[[Path], TagReadResult] = read_tags
        #: Schon gelesene Tags je Datei. Der Leseprozess kostet rund
        #: 135 ms; zweimal dieselbe Datei muss das nicht zahlen. Nur fuer
        #: diese Sitzung, nichts wird auf die Platte geschrieben.
        self._tag_cache: dict[str, TrackTags] = {}

    # ------------------------------------------------------------------

    def load(
        self,
        path: str | Path,
        *,
        use_cache: bool = True,
        known: TrackTags | None = None,
    ) -> LoadedTrack:
        """Track vollstaendig laden.

        Reihenfolge: dekodieren -> auf Engine-Rate bringen -> Analyse aus dem
        Cache oder neu berechnen -> Metadaten ergaenzen -> ``TrackInfo``.

        ``known`` sind bereits bekannte Metadaten, etwa die aus
        ``export.pdb`` gelesenen eines rekordbox-Sticks. Sie haben
        **Vorrang**: sie werden nicht durch Dateitags ersetzt, und sind
        sie vollstaendig, wird die Datei dafuer gar nicht angefasst.
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

        # Erst hier - nach der Analyse. Alles Folgende kann ausfallen,
        # ohne dass das Ergebnis oben verloren geht.
        tags, metadata_error = self._metadata(source, key.digest, known)

        info = TrackInfo(
            track_id=key.digest[:16],
            # Der Dateiname ist der Rueckfall fuer den Titel: er ist kein
            # erfundener Wert, sondern der einzige, der immer da ist.
            title=tags.title or source.stem,
            artist=tags.artist,
            album=tags.album,
            genre=tags.genre,
            label=tags.label,
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
            metadata_error=metadata_error,
        )

    # ------------------------------------------------------------------

    def _metadata(
        self, source: Path, digest: str, known: TrackTags | None
    ) -> tuple[TrackTags, str]:
        """Metadaten zusammenstellen. Gibt Tags und einen Fehlergrund.

        Der optionale Schritt des Ladens. Er kann in jeder Zeile
        fehlschlagen, ohne dass der Aufrufer etwas anderes verliert als
        einzelne Textfelder.
        """
        existing = known if known is not None else TrackTags()
        if existing.is_complete:
            # Aus rekordbox oder der Bibliothek ist alles bekannt. Die
            # Datei dafuer noch einmal anzufassen waere reines Risiko.
            return existing, ""

        cached = self._tag_cache.get(digest)
        if cached is not None:
            return existing.filled_with(cached), ""

        try:
            result = self.tag_reader(source)
        except Exception as error:  # pragma: no cover - Fehler im Leser
            # Der Betriebsleser meldet Fehler als Text und wirft nicht.
            # Wirft er doch, ist das ein Programmfehler - er darf aber
            # nicht die fertige Analyse mitnehmen. Deshalb hier ebenfalls
            # nur ein Metadatenfehler, mit vollem Stapel im Log.
            log.exception("Tag-Leser von %s ist gescheitert", source.name)
            self.metrics.note_metadata_failure()
            return existing, f"{type(error).__name__}: {error}"

        if not result.ok:
            # Ausdruecklich **kein** Ladefehler. Die Analyse steht; hier
            # fehlen nur Textfelder, und der Grund wird protokolliert.
            log.warning(
                "Metadaten von %s nicht gelesen: %s", source.name, result.error
            )
            self.metrics.note_metadata_failure()
            return existing, result.error

        self._tag_cache[digest] = result.tags
        return existing.filled_with(result.tags), ""

    @property
    def analyzer_version(self) -> int:
        from .analysis.analyzer import ANALYSIS_VERSION

        return ANALYSIS_VERSION
