"""BPM-, Beat- und Downbeat-Erkennung.

Grundlage ist ``librosa`` (Onset-Huellkurve + Beat-Tracking nach Ellis).
librosa allein reicht fuer DJ-Material nicht, deshalb drei Nachbehandlungen:

1. **Oktavfaltung.** librosa liefert bei elektronischer Musik regelmaessig
   das halbe oder doppelte Tempo. Gemessen an einem 124-BPM-Testsignal:
   librosa 62.26 BPM. Das Ergebnis wird in ein DJ-taugliches Fenster
   gefaltet und das Beat-Tracking mit diesem Tempo wiederholt.

2. **Linearfit statt Median.** Die Beatzeiten von librosa sind auf das
   Analyse-Raster gequantelt (bei hop 512 und 44.1 kHz sind das 11.6 ms).
   Ein Ausgleichsgerade ueber alle Beats mittelt das heraus: aus 123.05 BPM
   (Median der Abstaende) werden 124.005 BPM bei einem Sollwert von 124.000.

3. **Downbeat-Schaetzung mit Konfidenz.** librosa liefert keine Downbeats.
   Geschaetzt wird ueber die Onset-Energie im Bassband, weil der Taktanfang
   meist die Bassdrum traegt. Das ist eine Heuristik und wird ausdruecklich
   mit einer Konfidenz geliefert - unterhalb der Schwelle gilt der Downbeat
   als unbestimmt und die Korrektur bleibt dem Bediener.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Analyse-Samplerate. 22050 Hz genuegt fuer Rhythmus und halbiert die Zeit.
ANALYSIS_SAMPLE_RATE = 22050

#: Fenstergroesse der Onset-Analyse in Samples.
HOP_LENGTH = 512

#: DJ-taugliches Tempofenster fuer die Oktavfaltung.
TEMPO_MIN = 82.0
TEMPO_MAX = 164.0

BEATS_PER_BAR = 4

#: Ab diesem Verhaeltnis (bester Phasenwert zu Mittelwert) gilt der Downbeat
#: als bestimmt. Darunter wird er als unbestimmt gemeldet.
DOWNBEAT_CONFIDENCE_THRESHOLD = 1.6

#: Obergrenze fuer das Bassband der Downbeat-Schaetzung.
DOWNBEAT_BAND_HZ = 200.0


@dataclass(frozen=True)
class TempoAnalysis:
    """Ergebnis der Rhythmusanalyse."""

    bpm: float
    #: Beatzeiten in Sekunden, aufsteigend.
    beats_s: np.ndarray
    #: Erster Beat des Rasters in Sekunden (aus dem Linearfit).
    first_beat_s: float
    #: Index in ``beats_s``, der als Taktanfang gilt.
    first_downbeat_index: int
    beats_per_bar: int = BEATS_PER_BAR
    #: 1.0 = kein Kontrast. Ab ``DOWNBEAT_CONFIDENCE_THRESHOLD`` verlaesslich.
    downbeat_confidence: float = 0.0
    #: Streuung der Beatzeiten um die Ausgleichsgerade, in Sekunden.
    beat_residual_s: float = 0.0
    #: Rohes Tempo von librosa vor der Oktavfaltung - fuer die Diagnose.
    raw_bpm: float = 0.0

    @property
    def downbeat_is_reliable(self) -> bool:
        return self.downbeat_confidence >= DOWNBEAT_CONFIDENCE_THRESHOLD

    @property
    def beat_interval_s(self) -> float:
        return 60.0 / self.bpm if self.bpm > 0 else 0.0


def fold_tempo(
    bpm: float, minimum: float = TEMPO_MIN, maximum: float = TEMPO_MAX
) -> float:
    """Tempo in ein Oktavfenster falten.

    Behebt den haeufigsten Fehler automatischer Tempoerkennung: halbes oder
    doppeltes Tempo.
    """
    if bpm <= 0:
        return 0.0
    guard = 0
    while bpm < minimum and guard < 8:
        bpm *= 2.0
        guard += 1
    while bpm >= maximum and guard < 16:
        bpm /= 2.0
        guard += 1
    return bpm


def fit_beat_line(beats_s: np.ndarray) -> tuple[float, float, float]:
    """Ausgleichsgerade durch die Beatzeiten.

    Returns:
        ``(interval_s, first_beat_s, residual_s)``. ``interval_s`` ist der
        mittlere Beatabstand, ``first_beat_s`` der Achsenabschnitt.
    """
    count = beats_s.size
    if count < 2:
        return (0.0, float(beats_s[0]) if count else 0.0, 0.0)

    index = np.arange(count, dtype=np.float64)
    matrix = np.vstack([index, np.ones_like(index)]).T
    (interval, intercept), *_ = np.linalg.lstsq(matrix, beats_s, rcond=None)
    residual = float(np.std(beats_s - (interval * index + intercept)))
    return (float(interval), float(intercept), residual)


def _bass_onset_envelope(mono: np.ndarray, sample_rate: int) -> np.ndarray:
    """Onset-Huellkurve nur aus dem Bassband."""
    import librosa

    spectrogram = librosa.feature.melspectrogram(
        y=mono, sr=sample_rate, hop_length=HOP_LENGTH, n_mels=128, fmax=8000
    )
    frequencies = librosa.mel_frequencies(n_mels=128, fmax=8000)
    band = frequencies <= DOWNBEAT_BAND_HZ
    if not band.any():  # pragma: no cover - nur bei absurder Samplerate
        band[0] = True
    return librosa.onset.onset_strength(
        S=librosa.power_to_db(spectrogram[band]),
        sr=sample_rate,
        hop_length=HOP_LENGTH,
    )


def estimate_downbeat(
    beats_s: np.ndarray,
    mono: np.ndarray,
    sample_rate: int,
    beats_per_bar: int = BEATS_PER_BAR,
) -> tuple[int, float]:
    """Taktanfang schaetzen.

    Returns:
        ``(index, confidence)``. ``confidence`` ist das Verhaeltnis des
        besten Phasenwertes zum Mittelwert aller Phasen; 1.0 bedeutet kein
        Kontrast, also keine Aussage.
    """
    if beats_s.size < beats_per_bar * 2:
        return (0, 0.0)

    import librosa

    envelope = _bass_onset_envelope(mono, sample_rate)
    frames = librosa.time_to_frames(
        beats_s, sr=sample_rate, hop_length=HOP_LENGTH
    )
    frames = np.clip(frames, 0, envelope.size - 1)
    energy = envelope[frames]

    scores = np.array(
        [float(np.mean(energy[phase::beats_per_bar]))
         for phase in range(beats_per_bar)]
    )
    mean = float(np.mean(scores))
    if mean <= 0:
        return (0, 0.0)
    best = int(np.argmax(scores))
    return (best, float(scores[best] / mean))


def analyse_tempo(
    mono: np.ndarray,
    sample_rate: int,
    *,
    tempo_min: float = TEMPO_MIN,
    tempo_max: float = TEMPO_MAX,
) -> TempoAnalysis:
    """Vollstaendige Rhythmusanalyse eines Mono-Signals."""
    import librosa

    if mono.size < sample_rate:  # weniger als eine Sekunde
        return TempoAnalysis(
            bpm=0.0,
            beats_s=np.zeros(0, dtype=np.float64),
            first_beat_s=0.0,
            first_downbeat_index=0,
        )

    envelope = librosa.onset.onset_strength(
        y=mono, sr=sample_rate, hop_length=HOP_LENGTH, aggregate=np.median
    )

    raw = float(
        np.atleast_1d(
            librosa.feature.tempo(
                onset_envelope=envelope, sr=sample_rate, hop_length=HOP_LENGTH
            )
        )[0]
    )
    folded = fold_tempo(raw, tempo_min, tempo_max)
    if folded <= 0:
        return TempoAnalysis(
            bpm=0.0,
            beats_s=np.zeros(0, dtype=np.float64),
            first_beat_s=0.0,
            first_downbeat_index=0,
            raw_bpm=raw,
        )

    _, beats_s = librosa.beat.beat_track(
        onset_envelope=envelope,
        sr=sample_rate,
        hop_length=HOP_LENGTH,
        bpm=folded,
        units="time",
        trim=False,
    )
    beats_s = np.asarray(beats_s, dtype=np.float64)
    if beats_s.size < 2:
        return TempoAnalysis(
            bpm=folded,
            beats_s=beats_s,
            first_beat_s=float(beats_s[0]) if beats_s.size else 0.0,
            first_downbeat_index=0,
            raw_bpm=raw,
        )

    interval, intercept, residual = fit_beat_line(beats_s)
    bpm = 60.0 / interval if interval > 0 else folded
    # Ersten Beat in das erste Beatfenster holen.
    first_beat = intercept % interval if interval > 0 else intercept

    downbeat_index, confidence = estimate_downbeat(
        beats_s, mono, sample_rate
    )
    if confidence < DOWNBEAT_CONFIDENCE_THRESHOLD:
        # Nicht verlaesslich - Phase 0 statt einer Behauptung.
        downbeat_index = 0

    return TempoAnalysis(
        bpm=round(bpm, 3),
        beats_s=beats_s,
        first_beat_s=first_beat,
        first_downbeat_index=downbeat_index,
        downbeat_confidence=round(confidence, 3),
        beat_residual_s=round(residual, 6),
        raw_bpm=round(raw, 3),
    )
