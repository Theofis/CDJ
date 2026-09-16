"""Persistenter Analyse-Cache.

Ein Track wird genau einmal analysiert. Der Schluessel umfasst Pfad,
Dateigroesse, Aenderungszeit und die Analyseversion - aendert sich eines
davon, wird neu analysiert.

Ablage: eine ``.npz`` je Track. Darin die Arrays plus ein JSON-Block mit den
Skalaren. Die Waveform-Peaks werden als ``uint8`` gespeichert; bei einer
Darstellungsauflösung von wenigen hundert Pixeln ist die Quantisierung auf
1/255 nicht sichtbar und die Datei wird viermal kleiner.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .analysis.analyzer import ANALYSIS_VERSION, TrackAnalysis
from .analysis.key import KeyAnalysis
from .analysis.tempo import TempoAnalysis
from .analysis.waveform import BandPeaks

DEFAULT_CACHE_DIR = (
    Path(__file__).resolve().parents[2] / "cache" / "analysis"
)

#: Version des Cache-Dateiformats, unabhaengig von der Analyseversion.
CACHE_FORMAT_VERSION = 1


@dataclass(frozen=True)
class CacheKey:
    """Identitaet einer Datei fuer den Cache."""

    path: str
    size: int
    mtime_ns: int
    analysis_version: int = ANALYSIS_VERSION

    @classmethod
    def for_file(
        cls, path: str | Path, analysis_version: int = ANALYSIS_VERSION
    ) -> CacheKey:
        resolved = Path(path).resolve()
        stat = resolved.stat()
        return cls(
            path=str(resolved),
            size=int(stat.st_size),
            mtime_ns=int(stat.st_mtime_ns),
            analysis_version=analysis_version,
        )

    @property
    def digest(self) -> str:
        raw = "|".join(
            (
                self.path.lower(),
                str(self.size),
                str(self.mtime_ns),
                str(self.analysis_version),
                str(CACHE_FORMAT_VERSION),
            )
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def filename(self) -> str:
        stem = Path(self.path).stem[:40]
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)
        return f"{safe}-{self.digest[:16]}.npz"


@dataclass
class CacheStats:
    """Trefferstatistik - wird im Debug-Overlay angezeigt."""

    hits: int = 0
    misses: int = 0
    writes: int = 0
    errors: int = 0

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0

    def __str__(self) -> str:
        return (
            f"hit {self.hits} / miss {self.misses} "
            f"({self.hit_rate * 100:.0f} %)"
        )


class AnalysisCache:
    """Liest und schreibt Analyseergebnisse auf die Platte."""

    def __init__(self, directory: str | Path = DEFAULT_CACHE_DIR) -> None:
        self.directory = Path(directory)
        self.stats = CacheStats()

    # ------------------------------------------------------------------

    def path_for(self, key: CacheKey) -> Path:
        return self.directory / key.filename()

    def contains(self, key: CacheKey) -> bool:
        return self.path_for(key).exists()

    # ------------------------------------------------------------------

    def load(self, key: CacheKey) -> TrackAnalysis | None:
        """Ergebnis laden. ``None`` bei Fehlschlag - dann neu analysieren."""
        target = self.path_for(key)
        if not target.exists():
            self.stats.misses += 1
            return None
        try:
            analysis = self._read(target)
        except Exception:
            # Beschaedigter oder veralteter Eintrag: verwerfen statt raten.
            self.stats.errors += 1
            self.stats.misses += 1
            try:
                target.unlink()
            except OSError:  # pragma: no cover
                pass
            return None
        if analysis.analysis_version != key.analysis_version:
            self.stats.misses += 1
            return None
        self.stats.hits += 1
        return analysis

    def store(self, key: CacheKey, analysis: TrackAnalysis) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.path_for(key)
        temporary = target.with_name(target.name + ".tmp")
        self._write(temporary, key, analysis)
        os.replace(temporary, target)
        self.stats.writes += 1
        return target

    def clear(self) -> int:
        """Alle Eintraege loeschen. Rueckgabe: Anzahl."""
        if not self.directory.exists():
            return 0
        count = 0
        for entry in self.directory.glob("*.npz"):
            entry.unlink()
            count += 1
        return count

    def size_bytes(self) -> int:
        if not self.directory.exists():
            return 0
        return sum(e.stat().st_size for e in self.directory.glob("*.npz"))

    # ------------------------------------------------------------------
    # Serialisierung
    # ------------------------------------------------------------------

    @staticmethod
    def _write(target: Path, key: CacheKey, analysis: TrackAnalysis) -> None:
        arrays: dict[str, np.ndarray] = {
            "beats_s": np.asarray(analysis.tempo.beats_s, dtype=np.float64),
        }
        levels: dict[str, float] = {}
        for name, peaks in analysis.waveform_levels.items():
            levels[name] = peaks.peaks_per_second
            for band in ("low", "mid", "high", "peak", "rms"):
                data = getattr(peaks, band)
                if data.size:
                    arrays[f"wave_{name}_{band}"] = _to_uint8(data)

        meta = {
            "cache_format_version": CACHE_FORMAT_VERSION,
            "source": {
                "path": key.path,
                "size": key.size,
                "mtime_ns": key.mtime_ns,
            },
            "duration_s": analysis.duration_s,
            "sample_rate": analysis.sample_rate,
            "channels": analysis.channels,
            "peak": analysis.peak,
            "analysis_version": analysis.analysis_version,
            "analysis_seconds": analysis.analysis_seconds,
            "levels": levels,
            "tempo": {
                "bpm": analysis.tempo.bpm,
                "first_beat_s": analysis.tempo.first_beat_s,
                "first_downbeat_index": analysis.tempo.first_downbeat_index,
                "beats_per_bar": analysis.tempo.beats_per_bar,
                "downbeat_confidence": analysis.tempo.downbeat_confidence,
                "beat_residual_s": analysis.tempo.beat_residual_s,
                "raw_bpm": analysis.tempo.raw_bpm,
            },
            "key": {
                "camelot": analysis.key.camelot,
                "name": analysis.key.name,
                "pitch_class": analysis.key.pitch_class,
                "is_minor": analysis.key.is_minor,
                "confidence": analysis.key.confidence,
            },
        }
        # Ueber ein Dateiobjekt schreiben: ``savez_compressed`` haengt sonst
        # ein weiteres ``.npz`` an den Namen und die Temp-Datei landet
        # woanders als erwartet.
        with target.open("wb") as handle:
            np.savez_compressed(
                handle, meta=np.array(json.dumps(meta)), **arrays
            )

    @staticmethod
    def _read(target: Path) -> TrackAnalysis:
        with np.load(target, allow_pickle=False) as data:
            meta = json.loads(str(data["meta"].item()))
            beats = np.asarray(data["beats_s"], dtype=np.float64)

            levels: dict[str, BandPeaks] = {}
            for name, peaks_per_second in meta["levels"].items():
                def band(field: str) -> np.ndarray:
                    key = f"wave_{name}_{field}"
                    if key in data.files:
                        return _from_uint8(data[key])
                    # Aeltere Cachedatei ohne Dynamikwerte.
                    return np.zeros(0, dtype=np.float32)

                levels[name] = BandPeaks(
                    low=band("low"),
                    mid=band("mid"),
                    high=band("high"),
                    peak=band("peak"),
                    rms=band("rms"),
                    peaks_per_second=float(peaks_per_second),
                )

        tempo_meta = meta["tempo"]
        key_meta = meta["key"]
        return TrackAnalysis(
            duration_s=float(meta["duration_s"]),
            sample_rate=int(meta["sample_rate"]),
            channels=int(meta["channels"]),
            tempo=TempoAnalysis(
                bpm=float(tempo_meta["bpm"]),
                beats_s=beats,
                first_beat_s=float(tempo_meta["first_beat_s"]),
                first_downbeat_index=int(tempo_meta["first_downbeat_index"]),
                beats_per_bar=int(tempo_meta["beats_per_bar"]),
                downbeat_confidence=float(tempo_meta["downbeat_confidence"]),
                beat_residual_s=float(tempo_meta["beat_residual_s"]),
                raw_bpm=float(tempo_meta["raw_bpm"]),
            ),
            key=KeyAnalysis(
                camelot=key_meta["camelot"],
                name=key_meta["name"],
                pitch_class=int(key_meta["pitch_class"]),
                is_minor=bool(key_meta["is_minor"]),
                confidence=float(key_meta["confidence"]),
            ),
            waveform_levels=levels,
            peak=float(meta["peak"]),
            analysis_version=int(meta["analysis_version"]),
            analysis_seconds=float(meta["analysis_seconds"]),
        )


def _to_uint8(values: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(values) * 255.0, 0, 255).astype(np.uint8)


def _from_uint8(values: np.ndarray) -> np.ndarray:
    return (np.asarray(values, dtype=np.float32) / 255.0).astype(np.float32)
