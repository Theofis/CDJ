"""Testmaterial fuer die Audioschicht.

Erzeugt synthetische Tracks mit **bekanntem** Tempo, damit die Analyse gegen
einen echten Sollwert geprueft werden kann. Die Dateien werden einmal je
Testlauf angelegt und wiederverwendet - Dekodieren und Analysieren sind sonst
der Grossteil der Testlaufzeit.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100

#: Tempo des Testmaterials. Die Analyse muss darauf kommen.
KNOWN_BPM = 124.0

#: Laenge des Testmaterials in Sekunden.
DURATION_S = 12.0


def click_track(
    bpm: float = KNOWN_BPM,
    duration_s: float = DURATION_S,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Stereo-Signal mit klarem Beat: Kick, Snare und Hi-Hat.

    Rueckgabe: ``(frames, 2)`` float32 im Bereich -1..1.
    """
    frames = int(sample_rate * duration_s)
    time_axis = np.arange(frames) / sample_rate
    signal = np.zeros(frames, dtype=np.float64)

    beat_s = 60.0 / bpm
    kick_length = sample_rate // 10
    kick_env = np.exp(-np.arange(kick_length) / (sample_rate * 0.02))
    kick = np.sin(2 * np.pi * 55 * np.arange(kick_length) / sample_rate)

    snare_length = sample_rate // 20
    snare_env = np.exp(-np.arange(snare_length) / (sample_rate * 0.01))

    for index in range(int(duration_s / beat_s)):
        start = int(index * beat_s * sample_rate)
        end = min(frames, start + kick_length)
        if end > start:
            span = end - start
            # Taktanfang lauter - damit hat die Downbeat-Schaetzung etwas.
            gain = 1.0 if index % 4 == 0 else 0.6
            signal[start:end] += kick[:span] * kick_env[:span] * gain
        if index % 2 == 1:
            end = min(frames, start + snare_length)
            span = end - start
            if span > 0:
                noise = np.random.default_rng(index).normal(0, 1, span)
                signal[start:end] += noise * snare_env[:span] * 0.35

    # Hi-Hat auf Achteln fuer Energie im Hochband.
    eighth = beat_s / 2
    for index in range(int(duration_s / eighth)):
        start = int(index * eighth * sample_rate)
        length = sample_rate // 100
        end = min(frames, start + length)
        span = end - start
        if span > 0:
            noise = np.random.default_rng(1000 + index).normal(0, 1, span)
            signal[start:end] += noise * np.exp(
                -np.arange(span) / (sample_rate * 0.002)
            ) * 0.15

    # Basslinie fuer das Tiefband.
    signal += 0.25 * np.sin(2 * np.pi * 82.4 * time_axis)

    peak = float(np.max(np.abs(signal)))
    if peak > 0:
        signal = signal / (peak * 1.05)
    return np.stack([signal, signal], axis=1).astype(np.float32)


#: Laenge des Rampensignals in Sekunden.
RAMP_DURATION_S = 10.0


def ramp_track(
    duration_s: float = RAMP_DURATION_S, sample_rate: int = SAMPLE_RATE
) -> np.ndarray:
    """Signal, dessen Samplewert die **normierte** Position ist.

    Der Wert laeuft linear von 0.0 bis knapp 1.0 und bleibt damit im
    gueltigen Audiobereich - sonst wuerde die Engine das Signal korrekt
    begrenzen und der Test wuerde das Falsche pruefen.

    Umrechnung in Sekunden: ``ramp_seconds(wert, duration_s)``.
    """
    frames = int(sample_rate * duration_s)
    ramp = (np.arange(frames) / frames).astype(np.float32)
    return np.stack([ramp, ramp], axis=1)


def ramp_seconds(
    value: float, duration_s: float = RAMP_DURATION_S
) -> float:
    """Samplewert eines Rampensignals in Sekunden umrechnen."""
    return float(value) * duration_s


def ramp_value(
    seconds: float, duration_s: float = RAMP_DURATION_S
) -> float:
    """Sekunden in den erwarteten Samplewert umrechnen."""
    return float(seconds) / duration_s


@dataclass
class Fixtures:
    """Angelegte Testdateien."""

    directory: Path
    wav: Path
    flac: Path
    mp3: Path
    bpm: float = KNOWN_BPM
    duration_s: float = DURATION_S

    def all_files(self) -> tuple[Path, ...]:
        return (self.wav, self.flac, self.mp3)


_FIXTURES: Fixtures | None = None
_TEMPDIR: tempfile.TemporaryDirectory | None = None


def fixtures() -> Fixtures:
    """Testdateien anlegen oder die bestehenden liefern."""
    global _FIXTURES, _TEMPDIR
    if _FIXTURES is not None:
        return _FIXTURES

    import soundfile as sf

    _TEMPDIR = tempfile.TemporaryDirectory(prefix="virtual_cdj_tests_")
    directory = Path(_TEMPDIR.name)
    samples = click_track()

    wav = directory / "click.wav"
    flac = directory / "click.flac"
    mp3 = directory / "click.mp3"
    sf.write(wav, samples, SAMPLE_RATE, subtype="FLOAT")
    sf.write(flac, samples, SAMPLE_RATE)
    sf.write(mp3, samples, SAMPLE_RATE)

    _FIXTURES = Fixtures(
        directory=directory, wav=wav, flac=flac, mp3=mp3
    )
    return _FIXTURES
