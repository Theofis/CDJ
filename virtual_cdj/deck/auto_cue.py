"""AUTO CUE: den ersten Audioeinsatz eines Tracks finden.

Am Geraet haelt AUTO CUE nach dem Laden dort, wo der Ton einsetzt, und
nicht bei 0:00 - eine Sekunde Anlaufstille wird uebersprungen (Handbuch
S. 44). Genau diese Stelle sucht dieses Modul.

Es entsteht dabei **keine zweite Analysepipeline.** Gesucht wird in den
Waveform-Daten, die die Trackanalyse ohnehin einmal berechnet und cacht
(``audio/analysis/waveform.py``). Die Audiodatei wird hier nicht
geoeffnet, nichts dekodiert und nichts gefiltert; es wird nur gelesen,
was schon da ist.

Bezugspegel - und was die dB-Werte hier **nicht** sind
------------------------------------------------------
Die Analyse normiert ``peak`` und ``rms`` auf den **lautesten Punkt des
Tracks** (``full_scale`` in ``audio/analysis/waveform.py``), nicht auf
digitale Vollaussteuerung. Eine Schwelle von -60 dB heisst hier also
"60 dB unter dem lautesten Punkt dieses Tracks".

Am CDJ-3000 ist dieselbe Zahl ein **absoluter** Pegel. Die Beschriftungen
in ``AutoCueLevel`` sind die des Geraets, die Wirkung ist es noch nicht.
Deshalb heisst der Parameter hier ``threshold_db_below_peak`` und die
Eigenschaft am Enum genauso: der Name soll nicht mehr behaupten, als die
Daten hergeben. Bei fertig gemasterten Tracks ist der Unterschied klein,
weil die nahe Vollaussteuerung liegen; bei einer leisen Aufnahme nicht.

TODO / Integrationspunkt fuer absolute Schwellen
------------------------------------------------
Damit aus den Beschriftungen echte dBFS-Werte werden, fehlt genau eine
Zahl: der **unnormierte** Spitzenwert des Tracks.

1. ``audio/analysis/waveform.py``, ``compute_levels()``: dort wird
   ``full_scale = max(abs(mono))`` berechnet und danach weggeworfen. Es
   muesste in ``BandPeaks`` (oder ``TrackAnalysis``) mitgefuehrt werden.
2. ``audio/cache.py``: der Wert gehoert in die Cache-Metadaten, sonst ist
   er nach dem ersten Lauf wieder weg. Das aendert das Cache-Format und
   braucht eine Version.
3. ``deck/state.py``, ``WaveformData``: Feld ``full_scale`` durchreichen.
4. Hier: ``detect_audio_start()`` bekommt neben
   ``threshold_db_below_peak`` ein ``threshold_dbfs`` und rechnet
   ``threshold = db_to_amplitude(dbfs) / full_scale``.

Bis dahin gilt die relative Bedeutung, und sie steht in jedem Namen und
in ``docs/AUTO_CUE.md``.

Hot Cues spielen keine Rolle
----------------------------
Ein Hotcue ist eine Setzung der Person am Geraet, kein Merkmal des
Signals. Er ist deshalb **kein** Ersatz fuer die Einsatzerkennung. Nur
die Stufe ``AutoCueLevel.MEMORY`` benutzt gespeicherte Punkte, und dort
sind es die Memory Cues.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from .state import WaveformData, WaveformSet

#: Kleinster Wert, der noch als Pegel durchgeht. Darunter ist ``log10``
#: nicht mehr sinnvoll und das Fenster gilt als Stille.
_SILENCE_FLOOR = 1e-9


def db_to_amplitude(db: float) -> float:
    """Pegel in dB in einen Amplitudenfaktor 0.0 - 1.0 umrechnen."""
    return float(10.0 ** (db / 20.0))


def amplitude_to_db(amplitude: float) -> float:
    """Amplitude in dB. Stille wird ``-inf``."""
    if amplitude <= _SILENCE_FLOOR:
        return -math.inf
    return 20.0 * math.log10(amplitude)


def _window_amplitude(peaks: WaveformData, index: int) -> float:
    """Lautstaerke eines Fensters.

    Bevorzugt der Breitband-Spitzenwert: er faellt am Einsatz zuerst auf,
    auch wenn der Effektivwert noch niedrig ist. Aeltere Analysen ohne
    Dynamikfelder haben nur die drei Baender - dann gilt das lauteste.
    """
    if peaks.has_dynamics:
        return float(peaks.peak[index])
    return max(
        float(peaks.low[index]),
        float(peaks.mid[index]),
        float(peaks.high[index]),
    )


def detect_audio_start(
    waveform: WaveformSet | None, *, threshold_db_below_peak: float
) -> float | None:
    """Beginn des ersten Tons in Sekunden.

    Zurueck kommt der **Anfang** des ersten Fensters, das die Schwelle
    ueberschreitet - also eine Stelle unmittelbar vor dem Einsatz, nie
    mitten darin. Bei der feinsten Analysestufe ist ein Fenster rund 3 ms
    lang, die Stelle liegt also dicht davor.

    ``threshold_db_below_peak`` ist die Schwelle in dB **unter dem
    lautesten Punkt des Tracks** - siehe den Modulkopf, warum das (noch)
    nicht dasselbe wie dBFS ist.

    ``None`` bedeutet "nicht feststellbar": keine Waveform vorhanden oder
    der ganze Track bleibt unter der Schwelle. Der Aufrufer entscheidet
    dann, was gilt - hier wird keine Position erfunden.
    """
    if waveform is None or not waveform:
        return None
    peaks = waveform.finest
    if peaks is None or peaks.length == 0 or peaks.peaks_per_second <= 0:
        return None

    threshold = db_to_amplitude(threshold_db_below_peak)
    for index in range(peaks.length):
        if _window_amplitude(peaks, index) >= threshold:
            return index / peaks.peaks_per_second
    return None


def first_memory_cue(positions: Sequence[float]) -> float | None:
    """Fruehester gespeicherter Punkt hinter dem Trackanfang.

    Nur fuer ``AutoCueLevel.MEMORY``. Ein Punkt genau bei 0 ist kein
    Einsprungpunkt, sondern der Trackanfang selbst.
    """
    candidates = [position for position in positions if position > 0.0]
    return min(candidates) if candidates else None


__all__ = [
    "amplitude_to_db",
    "db_to_amplitude",
    "detect_audio_start",
    "first_memory_cue",
]
