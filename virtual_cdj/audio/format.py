"""Internes Audioformat.

Genau eine Repraesentation im ganzen Programm:

* ``float32``
* Wertebereich -1.0 bis +1.0
* immer **stereo**, Form ``(frames, 2)``, C-kontinuierlich
* eine feste Engine-Samplerate

Mono-Dateien werden auf beide Kanaele gelegt, mehrkanalige auf Stereo
reduziert. Damit muss keine andere Schicht Sonderfaelle behandeln.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Interne Samplerate der Engine. Alles wird beim Laden hierher gebracht.
ENGINE_SAMPLE_RATE = 44100

#: Interner Datentyp.
DTYPE = np.float32

CHANNELS = 2


class AudioFormatError(ValueError):
    """Audiodaten passen nicht zum internen Format."""


@dataclass(frozen=True)
class AudioBuffer:
    """Ein Block oder ein ganzer Track im internen Format."""

    #: Form ``(frames, 2)``, dtype float32.
    samples: np.ndarray
    sample_rate: int

    def __post_init__(self) -> None:
        data = self.samples
        if data.ndim != 2 or data.shape[1] != CHANNELS:
            raise AudioFormatError(
                f"Erwartet Form (frames, {CHANNELS}), erhalten {data.shape}"
            )
        if data.dtype != DTYPE:
            raise AudioFormatError(
                f"Erwartet {DTYPE}, erhalten {data.dtype}"
            )
        if self.sample_rate <= 0:
            raise AudioFormatError(f"Ungueltige Samplerate {self.sample_rate}")

    # ------------------------------------------------------------------

    @property
    def frames(self) -> int:
        return int(self.samples.shape[0])

    @property
    def duration_s(self) -> float:
        return self.frames / self.sample_rate

    @property
    def peak(self) -> float:
        if self.frames == 0:
            return 0.0
        return float(np.max(np.abs(self.samples)))

    @property
    def nbytes(self) -> int:
        return int(self.samples.nbytes)

    def mono(self) -> np.ndarray:
        """Kanalsumme als 1D-float32-Array. Fuer die Analyse."""
        if self.frames == 0:
            return np.zeros(0, dtype=DTYPE)
        return np.mean(self.samples, axis=1, dtype=np.float32)

    def slice_frames(self, start: int, stop: int) -> AudioBuffer:
        start = max(0, start)
        stop = min(self.frames, stop)
        return AudioBuffer(
            samples=np.ascontiguousarray(self.samples[start:stop]),
            sample_rate=self.sample_rate,
        )

    # ------------------------------------------------------------------

    @classmethod
    def empty(cls, sample_rate: int = ENGINE_SAMPLE_RATE) -> AudioBuffer:
        return cls(np.zeros((0, CHANNELS), dtype=DTYPE), sample_rate)

    @classmethod
    def silence(
        cls, frames: int, sample_rate: int = ENGINE_SAMPLE_RATE
    ) -> AudioBuffer:
        return cls(np.zeros((frames, CHANNELS), dtype=DTYPE), sample_rate)


def to_internal(data: np.ndarray, sample_rate: int) -> AudioBuffer:
    """Beliebige dekodierte Samples ins interne Format bringen.

    Nimmt ``(frames,)``, ``(frames, 1)``, ``(frames, 2)`` oder
    ``(frames, n)`` an und liefert immer Stereo-float32.
    """
    array = np.asarray(data)

    if array.dtype != DTYPE:
        if np.issubdtype(array.dtype, np.integer):
            info = np.iinfo(array.dtype)
            scale = float(max(abs(info.min), info.max))
            array = array.astype(DTYPE) / scale
        else:
            array = array.astype(DTYPE)

    if array.ndim == 1:
        array = array[:, None]
    if array.ndim != 2:
        raise AudioFormatError(f"Unerwartete Form {array.shape}")

    channels = array.shape[1]
    if channels == 1:
        array = np.repeat(array, CHANNELS, axis=1)
    elif channels == 2:
        pass
    elif channels > 2:
        # Mehrkanal: vordere zwei Kanaele nehmen, Rest gleichmaessig
        # dazumischen, damit nichts verloren geht.
        front = array[:, :2]
        rest = array[:, 2:]
        array = front + np.mean(rest, axis=1, keepdims=True) * 0.5
    else:
        raise AudioFormatError(f"Keine Kanaele in {array.shape}")

    return AudioBuffer(np.ascontiguousarray(array, dtype=DTYPE), sample_rate)
