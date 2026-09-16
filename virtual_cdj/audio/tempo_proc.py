"""Temporegelung im Audio-Callback.

Erste Version: lineare Interpolation. Das Tempo veraendert dabei die Tonhoehe,
genau wie bei einer Schallplatte. Das ist ausdruecklich zugelassen, solange
kein Time-Stretching existiert.

Die Schnittstelle ``TempoProcessor`` ist so gebaut, dass spaeter ein
Verfahren mit Tonhoehenkorrektur (Master Tempo / Key Lock) eingesetzt werden
kann, ohne dass sich an der Engine etwas aendert.

Echtzeitregeln: keine Datei-, GUI- oder Netzzugriffe und keine Allokationen
pro Callback. Alle Arbeitspuffer werden einmalig vorbelegt, die Rechnung
laeuft ausschliesslich mit ``out=``-Varianten von numpy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class TempoProcessor(ABC):
    """Erzeugt Ausgangssamples aus einer Quelle mit veraenderter Geschwindigkeit."""

    #: Anzeigename fuer die Diagnose.
    name = "abstract"
    #: Ob das Verfahren die Tonhoehe mitverschiebt.
    changes_pitch = True

    @abstractmethod
    def render(
        self,
        source: np.ndarray,
        position: float,
        frames: int,
        speed: float,
        out: np.ndarray,
    ) -> float:
        """``frames`` Ausgangssamples nach ``out`` schreiben.

        Args:
            source: Quelle, Form ``(n, 2)``, float32.
            position: Leseposition in Quell-Samples, gebrochen.
            frames: Anzahl zu schreibender Ausgangssamples.
            speed: Quell-Samples pro Ausgangs-Sample. Negativ = rueckwaerts.
            out: Ziel, mindestens Form ``(frames, 2)``. Wird ueberschrieben.

        Returns:
            Neue Leseposition in Quell-Samples.
        """


class SimpleResamplerTempoProcessor(TempoProcessor):
    """Lineare Interpolation. Tonhoehe folgt dem Tempo."""

    name = "resampler"
    changes_pitch = True

    def __init__(self, max_frames: int = 4096) -> None:
        self._capacity = 0
        self._ensure_capacity(max_frames)

    def _ensure_capacity(self, frames: int) -> None:
        """Puffer vergroessern. Passiert nur bei Wechsel der Blockgroesse."""
        if frames <= self._capacity:
            return
        self._capacity = frames
        self._offsets = np.arange(frames, dtype=np.float64)
        self._index = np.empty(frames, dtype=np.float64)
        self._floor = np.empty(frames, dtype=np.float64)
        self._base = np.empty(frames, dtype=np.int64)
        self._fraction = np.empty((frames, 1), dtype=np.float32)
        self._left = np.empty((frames, 2), dtype=np.float32)
        self._right = np.empty((frames, 2), dtype=np.float32)

    def render(
        self,
        source: np.ndarray,
        position: float,
        frames: int,
        speed: float,
        out: np.ndarray,
    ) -> float:
        if frames <= 0:
            return position
        self._ensure_capacity(frames)

        total = int(source.shape[0])
        if total < 2:
            out[:frames] = 0.0
            return position

        # index[i] = position + i * speed
        index = self._index[:frames]
        np.multiply(self._offsets[:frames], speed, out=index)
        np.add(index, position, out=index)

        # base = floor(index), fraction = index - base
        floor = self._floor[:frames]
        np.floor(index, out=floor)
        fraction = self._fraction[:frames]
        np.subtract(index, floor, out=index)
        np.copyto(fraction[:, 0], index, casting="unsafe")

        base = self._base[:frames]
        np.copyto(base, floor, casting="unsafe")
        # Positionen ausserhalb des Tracks auf den Rand begrenzen. Die Engine
        # haelt die Position normalerweise im gueltigen Bereich; das hier ist
        # nur die Absicherung am letzten Sample.
        np.clip(base, 0, total - 2, out=base)

        left = self._left[:frames]
        right = self._right[:frames]
        np.take(source, base, axis=0, out=left)
        np.add(base, 1, out=base)
        np.take(source, base, axis=0, out=right)

        # out = left + (right - left) * fraction
        target = out[:frames]
        np.subtract(right, left, out=target)
        np.multiply(target, fraction, out=target)
        np.add(target, left, out=target)

        return position + frames * speed


class KeyLockTempoProcessor(TempoProcessor):
    """Platzhalter fuer Master Tempo / Key Lock.

    Noch nicht implementiert. Wird ein Time-Stretching-Verfahren einsetzen
    (Phase Vocoder oder Rubber Band) und die Tonhoehe konstant halten.
    """

    name = "keylock"
    changes_pitch = False

    def render(
        self,
        source: np.ndarray,
        position: float,
        frames: int,
        speed: float,
        out: np.ndarray,
    ) -> float:
        raise NotImplementedError(
            "Key Lock ist noch nicht implementiert - "
            "SimpleResamplerTempoProcessor verwenden."
        )
