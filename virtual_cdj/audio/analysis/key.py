"""Tonarterkennung.

Verfahren: Chroma-Profil des Tracks gegen die Krumhansl-Schmuckler-Profile
fuer Dur und Moll korrelieren, in allen zwoelf Transpositionen. Das ist das
Standardverfahren und kommt mit dem aus, was librosa liefert.

Das Ergebnis wird mit einer Konfidenz geliefert. Tonarterkennung ist bei
perkussivem Material unzuverlaessig; unterhalb der Schwelle wird ausdruecklich
keine Tonart behauptet.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Krumhansl-Schmuckler-Profile.
MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)

NOTE_NAMES = (
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
)

#: Camelot-Rad: (Tonhoehenklasse, ist_moll) -> Camelot-Bezeichnung.
#: Die Reihenfolge folgt dem Quintenzirkel, wie auf CDJ und in rekordbox.
_CAMELOT_MAJOR = {
    0: "8B", 7: "9B", 2: "10B", 9: "11B", 4: "12B", 11: "1B",
    6: "2B", 1: "3B", 8: "4B", 3: "5B", 10: "6B", 5: "7B",
}
_CAMELOT_MINOR = {
    9: "8A", 4: "9A", 11: "10A", 6: "11A", 1: "12A", 8: "1A",
    3: "2A", 10: "3A", 5: "4A", 0: "5A", 7: "6A", 2: "7A",
}

#: Ab diesem Verhaeltnis (beste Korrelation zur zweitbesten) gilt die Tonart
#: als bestimmt.
KEY_CONFIDENCE_THRESHOLD = 1.08


@dataclass(frozen=True)
class KeyAnalysis:
    """Erkannte Tonart."""

    #: Camelot-Bezeichnung, z. B. ``8A``. Leer, wenn unbestimmt.
    camelot: str = ""
    #: Musikalische Bezeichnung, z. B. ``Am``. Leer, wenn unbestimmt.
    name: str = ""
    pitch_class: int = -1
    is_minor: bool = False
    confidence: float = 0.0

    @property
    def is_reliable(self) -> bool:
        return bool(self.camelot) and self.confidence >= KEY_CONFIDENCE_THRESHOLD


def _correlate(chroma: np.ndarray, profile: np.ndarray) -> np.ndarray:
    """Korrelation des Chroma-Profils mit allen Transpositionen."""
    chroma = chroma - chroma.mean()
    profile = profile - profile.mean()
    denominator = np.linalg.norm(chroma) * np.linalg.norm(profile)
    if denominator <= 0:
        return np.zeros(12)
    return np.array(
        [
            float(np.dot(np.roll(chroma, -shift), profile) / denominator)
            for shift in range(12)
        ]
    )


def analyse_key(mono: np.ndarray, sample_rate: int) -> KeyAnalysis:
    """Tonart eines Mono-Signals bestimmen."""
    if mono.size < sample_rate:
        return KeyAnalysis()

    import librosa

    chroma = librosa.feature.chroma_cqt(y=mono, sr=sample_rate)
    profile = np.mean(chroma, axis=1)
    if not np.isfinite(profile).all() or profile.sum() <= 0:
        return KeyAnalysis()

    major = _correlate(profile, MAJOR_PROFILE)
    minor = _correlate(profile, MINOR_PROFILE)

    scores = np.concatenate([major, minor])
    best = int(np.argmax(scores))
    ordered = np.sort(scores)[::-1]
    second = float(ordered[1]) if ordered.size > 1 else 0.0
    confidence = float(ordered[0] / second) if second > 0 else 0.0

    pitch_class = best % 12
    is_minor = best >= 12
    table = _CAMELOT_MINOR if is_minor else _CAMELOT_MAJOR
    camelot = table.get(pitch_class, "")
    name = NOTE_NAMES[pitch_class] + ("m" if is_minor else "")

    if confidence < KEY_CONFIDENCE_THRESHOLD:
        # Keine Behauptung ohne Kontrast.
        return KeyAnalysis(confidence=round(confidence, 3))

    return KeyAnalysis(
        camelot=camelot,
        name=name,
        pitch_class=pitch_class,
        is_minor=is_minor,
        confidence=round(confidence, 3),
    )
