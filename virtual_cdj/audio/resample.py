"""Resampling - ausschliesslich beim Laden, niemals im Audio-Callback.

Der Audio-Callback macht nur lineare Interpolation fuer die Temporegelung
(siehe ``tempo_proc.py``). Bandbegrenztes, hochwertiges Resampling auf die
Engine-Rate passiert einmalig beim Import.
"""

from __future__ import annotations

import numpy as np

from .format import DTYPE, AudioBuffer, ENGINE_SAMPLE_RATE

#: Qualitaetsstufe von soxr. ``VHQ`` ist beim Import problemlos bezahlbar.
QUALITY = "VHQ"


def resample(buffer: AudioBuffer, target_rate: int = ENGINE_SAMPLE_RATE) -> AudioBuffer:
    """Puffer auf ``target_rate`` bringen. Gleiche Rate = unveraendert."""
    if buffer.sample_rate == target_rate or buffer.frames == 0:
        return AudioBuffer(buffer.samples, target_rate)

    import soxr

    converted = soxr.resample(
        buffer.samples, buffer.sample_rate, target_rate, quality=QUALITY
    )
    array = np.ascontiguousarray(converted, dtype=DTYPE)
    if array.ndim == 1:  # pragma: no cover - soxr liefert 2D bei 2D-Eingabe
        array = array[:, None]
    return AudioBuffer(array, target_rate)


def resample_mono(
    samples: np.ndarray, source_rate: int, target_rate: int
) -> np.ndarray:
    """1D-Array resamplen. Fuer die Analyse, die mit Mono arbeitet."""
    if source_rate == target_rate or samples.size == 0:
        return np.ascontiguousarray(samples, dtype=DTYPE)

    import soxr

    return np.ascontiguousarray(
        soxr.resample(samples, source_rate, target_rate, quality=QUALITY),
        dtype=DTYPE,
    )
