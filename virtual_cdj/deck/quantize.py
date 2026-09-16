"""Quantisierung - die eine Stelle, an der auf das Beatgrid gerastet wird.

Was hier steht
--------------
Eine reine Rechnung:

    (Position, Beatgrid, Rasterweite) -> Position

Mehr nicht. Kein Zustand, kein Transport, kein Audio.

Warum es diese Datei gibt
-------------------------
Quantisierung wird an mehreren Stellen gebraucht - Cue, Hotcue, LOOP IN,
LOOP OUT, CALL, Beatloop. Frueher rasteten Cue und Hotcue ueber
``BeatGrid.snap`` und die Loop-Punkte ueber ``LoopEngine.nearest_beatgrid``.
Beide rundeten auf **ganze** Beats, und die waehlbare Rasterweite des
CDJ-3000 (1/8, 1/4, 1/2, 1 Beat) gab es nirgends. Jede Funktion haette
sonst ihre eigene Rundung bekommen - vier Stellen, die auseinanderlaufen
koennen.

Jetzt geht jede Rasterung durch ``quantize_position``.

Was **nicht** quantisiert wird
------------------------------
Die Wiedergabeposition selbst. Quantize zieht den Track nicht auf das
Raster; es verschiebt nur den Punkt, den eine musikalische Aktion setzt.
Ebenso wenig Pitch Bend, Scratch, Frame Search und das Verschieben eines
Loop-Punkts mit dem Jogwheel - dort ist die Feineinstellung der Zweck.
"""

from __future__ import annotations

import math

from .state import BeatGrid

#: Waehlbare Rasterweiten in Beats - die Werte des CDJ-3000, aufsteigend.
QUANTIZE_BEAT_VALUES: tuple[float, ...] = (0.125, 0.25, 0.5, 1.0)

#: Rasterweite eines frisch gestarteten Decks: ein ganzer Beat.
DEFAULT_QUANTIZE_BEATS: float = 1.0


def normalise_beat_value(beats: float) -> float:
    """Naechstgelegener unterstuetzter Rasterwert.

    Ein Kommando mit einem Wert, den es am Geraet nicht gibt, soll nicht
    stillschweigend ein eigenes Raster aufmachen.
    """
    return min(QUANTIZE_BEAT_VALUES, key=lambda value: abs(value - beats))


def next_beat_value(beats: float) -> float:
    """Naechster Wert der Reihe, am Ende wieder von vorn.

    Das ist die Bedienung eines einzelnen Tasters bzw. einer Zeile in den
    Einstellungen: 1/8 -> 1/4 -> 1/2 -> 1 -> 1/8.
    """
    current = normalise_beat_value(beats)
    index = QUANTIZE_BEAT_VALUES.index(current)
    return QUANTIZE_BEAT_VALUES[(index + 1) % len(QUANTIZE_BEAT_VALUES)]


def beat_value_label(beats: float) -> str:
    """Anzeigetext einer Rasterweite, z. B. ``1/4`` oder ``1``."""
    if beats >= 1:
        return f"{beats:g}"
    return f"1/{int(round(1 / beats))}"


def quantize_position(
    position_s: float,
    grid: BeatGrid | None,
    beats: float = DEFAULT_QUANTIZE_BEATS,
) -> float:
    """Position auf das Beatgrid rasten.

    Args:
        position_s: Wo die Aktion tatsaechlich stattfand.
        grid: Beatgrid des geladenen Tracks. ``None`` oder ungueltig heisst:
            es gibt keine Beats, auf die gerastet werden koennte.
        beats: Rasterweite in Beats (``QUANTIZE_BEAT_VALUES``).

    Returns:
        Die gerasterte Position. **Ohne gueltiges Beatgrid unveraendert** -
        ein erfundenes Raster waere schlechter als gar keines.

    Genau auf halber Strecke gewinnt der spaetere Rasterpunkt; das ist
    dieselbe Regel wie in ``BeatGrid.snap`` und sorgt dafuer, dass ein
    Druck kurz vor dem Beat nicht zurueckfaellt.
    """
    if grid is None or not grid.is_valid or beats <= 0:
        return position_s

    if beats >= 1.0:
        # Ganze Beats: die vorhandene Primitive des Beatgrids. Sie kennt
        # auch Tracks mit expliziter Beatliste und wechselndem Tempo.
        return grid.snap(position_s)

    number = grid.beat_number_at(position_s)
    if number < 0:
        # Vor dem ersten Beat gibt es kein Raster. Lieber die echte
        # Position als ein extrapolierter Punkt.
        return position_s
    start = grid.beat_time(number)
    if start is None:
        return position_s
    following = grid.beat_time(number + 1)
    span = (
        following - start if following is not None else grid.beat_interval_s
    )
    if span <= 0:
        return start

    step = span * beats
    index = math.floor((position_s - start) / step + 0.5)
    return start + index * step
