"""Symbole der CDJ-Oberflaeche - gezeichnet, nicht als Schriftzeichen.

Vorher standen in der Oberflaeche gemischte Unicode-Zeichen: ``←``, ``▸``,
``▶``, ``▲``, ``♪``, ``ⓘ``, ``›``, ``✕``. Deren Groesse, Strichstaerke und
senkrechte Lage haengen an der jeweiligen Schrift, sie sitzen nie auf dem
Pixelraster und wirken untereinander uneinheitlich - je nach installierter
Schrift fehlen sie sogar.

Hier werden sie mit Linien und Flaechen auf dem Canvas gezeichnet:

* gemeinsame optische Groesse ueber ``size``
* gemeinsame Strichlogik (1 px fein, 2 px bei grossen Symbolen)
* Koordinaten auf ganzen bzw. halben Pixeln, also scharfe Kanten
* eine Farbe je Symbol, aus ``theme``

Jede Funktion zeichnet in ein Quadrat mit der Kantenlaenge ``size``, dessen
Mittelpunkt bei ``(x, y)`` liegt. Rueckgabe ist die Liste der erzeugten
Canvas-Elemente, damit Aufrufer sie bei Bedarf weiterbehandeln koennen.
"""

from __future__ import annotations

import math
from collections.abc import Callable

#: Ab dieser Symbolgroesse wird mit 2 px Strich gezeichnet.
THICK_FROM = 18


def _stroke(size: float) -> int:
    return 2 if size >= THICK_FROM else 1


def _snap(value: float) -> float:
    """Auf die Mitte eines Pixels - macht 1-px-Linien scharf."""
    return float(int(value)) + 0.5


def _round(value: float) -> int:
    return int(round(value))


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


def play(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    """Gefuelltes Dreieck. Optisch etwas schmaler als das Quadrat."""
    half = size / 2.0
    left = _round(x - half * 0.62)
    right = _round(x + half * 0.78)
    return [
        canvas.create_polygon(
            left, _round(y - half), left, _round(y + half), right, _round(y),
            fill=colour, outline="",
        )
    ]


def pause(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    """Zwei Balken. Breite und Abstand aus derselben Teilung."""
    half = size / 2.0
    bar = max(1, _round(size * 0.26))
    gap = max(1, _round(size * 0.18))
    top, bottom = _round(y - half), _round(y + half)
    left = _round(x - gap / 2 - bar)
    return [
        canvas.create_rectangle(
            left, top, left + bar, bottom, fill=colour, outline="",
        ),
        canvas.create_rectangle(
            _round(x + gap / 2), top, _round(x + gap / 2) + bar, bottom,
            fill=colour, outline="",
        ),
    ]


def stop(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    half = _round(size * 0.42)
    return [
        canvas.create_rectangle(
            _round(x) - half, _round(y) - half,
            _round(x) + half, _round(y) + half,
            fill=colour, outline="",
        )
    ]


def cue(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    """Cue-Marke: Fahne an einem senkrechten Strich - wie am Geraet."""
    half = size / 2.0
    line = _snap(x - half * 0.55)
    top, bottom = _round(y - half), _round(y + half)
    flag = _round(size * 0.62)
    return [
        canvas.create_line(line, top, line, bottom, fill=colour, width=1),
        canvas.create_polygon(
            line, top, line + flag, top + _round(size * 0.22),
            line, top + _round(size * 0.44),
            fill=colour, outline="",
        ),
    ]


def loop(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    """Rechteckige Schleife mit Pfeilspitze - technisch, nicht rund."""
    half = size / 2.0
    stroke = _stroke(size)
    # Die Pfeilspitze zaehlt zur Breite: das Rechteck endet vorher, damit
    # das Symbol dieselbe optische Groesse hat wie alle anderen.
    tip = _round(size * 0.22)
    left = _snap(x - half + 1)
    right = _snap(x + half - tip)
    top, bottom = _snap(y - half * 0.62), _snap(y + half * 0.62)
    return [
        canvas.create_line(
            left, bottom, left, top, right, top,
            fill=colour, width=stroke, joinstyle="miter",
        ),
        canvas.create_line(
            left, bottom, right, bottom,
            fill=colour, width=stroke,
        ),
        canvas.create_polygon(
            right, bottom - tip / 2, right + tip, bottom,
            right, bottom + tip / 2,
            fill=colour, outline="",
        ),
    ]


# --------------------------------------------------------------------------
# Navigation
# --------------------------------------------------------------------------


def _chevron(
    canvas, x: float, y: float, size: float, colour: str, direction: str
) -> list[int]:
    half = size / 2.0
    reach = half * 0.52
    stroke = _stroke(size)
    cx, cy = _snap(x), _snap(y)
    if direction in ("left", "right"):
        sign = -1.0 if direction == "left" else 1.0
        points = (
            cx - sign * reach, cy - reach,
            cx + sign * reach, cy,
            cx - sign * reach, cy + reach,
        )
    else:
        sign = -1.0 if direction == "up" else 1.0
        points = (
            cx - reach, cy - sign * reach,
            cx, cy + sign * reach,
            cx + reach, cy - sign * reach,
        )
    return [
        canvas.create_line(
            *points, fill=colour, width=stroke, joinstyle="miter",
        )
    ]


def chevron_left(canvas, x, y, size, colour) -> list[int]:
    return _chevron(canvas, x, y, size, colour, "left")


def chevron_right(canvas, x, y, size, colour) -> list[int]:
    return _chevron(canvas, x, y, size, colour, "right")


def chevron_up(canvas, x, y, size, colour) -> list[int]:
    return _chevron(canvas, x, y, size, colour, "up")


def chevron_down(canvas, x, y, size, colour) -> list[int]:
    return _chevron(canvas, x, y, size, colour, "down")


def arrow_left(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    """Pfeil nach links - eine Ebene hoeher."""
    half = size / 2.0
    stroke = _stroke(size)
    cy = _snap(y)
    left, right = _snap(x - half * 0.7), _snap(x + half * 0.7)
    tip = _round(size * 0.3)
    return [
        canvas.create_line(left, cy, right, cy, fill=colour, width=stroke),
        canvas.create_line(
            left, cy, left + tip, cy - tip,
            fill=colour, width=stroke,
        ),
        canvas.create_line(
            left, cy, left + tip, cy + tip,
            fill=colour, width=stroke,
        ),
    ]


def caret_up(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    """Kleines gefuelltes Dreieck - Sortierrichtung."""
    half = size / 2.0
    return [
        canvas.create_polygon(
            _round(x - half), _round(y + half * 0.5),
            _round(x + half), _round(y + half * 0.5),
            _round(x), _round(y - half * 0.6),
            fill=colour, outline="",
        )
    ]


def caret_down(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    half = size / 2.0
    return [
        canvas.create_polygon(
            _round(x - half), _round(y - half * 0.5),
            _round(x + half), _round(y - half * 0.5),
            _round(x), _round(y + half * 0.6),
            fill=colour, outline="",
        )
    ]


def triangle_right(
    canvas, x: float, y: float, size: float, colour: str
) -> list[int]:
    """Gefuelltes Dreieck - Ordner oder laufender Track in einer Liste."""
    half = size / 2.0
    return [
        canvas.create_polygon(
            _round(x - half * 0.6), _round(y - half),
            _round(x - half * 0.6), _round(y + half),
            _round(x + half * 0.8), _round(y),
            fill=colour, outline="",
        )
    ]


def jump_back(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    """Zwei Dreiecke nach links - Beat Jump zurueck."""
    step = size * 0.42
    return (
        triangle_left(canvas, x - step / 2, y, size * 0.8, colour)
        + triangle_left(canvas, x + step / 2, y, size * 0.8, colour)
    )


def jump_forward(
    canvas, x: float, y: float, size: float, colour: str
) -> list[int]:
    return (
        triangle_right(canvas, x - size * 0.21, y, size * 0.8, colour)
        + triangle_right(canvas, x + size * 0.21, y, size * 0.8, colour)
    )


def triangle_left(
    canvas, x: float, y: float, size: float, colour: str
) -> list[int]:
    half = size / 2.0
    return [
        canvas.create_polygon(
            _round(x + half * 0.6), _round(y - half),
            _round(x + half * 0.6), _round(y + half),
            _round(x - half * 0.8), _round(y),
            fill=colour, outline="",
        )
    ]


# --------------------------------------------------------------------------
# Zeichen
# --------------------------------------------------------------------------


def info(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    """Kreis mit ``i`` - Trackdetails."""
    half = size / 2.0
    stroke = _stroke(size)
    items = [
        canvas.create_oval(
            _snap(x - half), _snap(y - half), _snap(x + half), _snap(y + half),
            outline=colour, width=stroke,
        ),
        canvas.create_line(
            _snap(x), _round(y - half * 0.25),
            _snap(x), _round(y + half * 0.5),
            fill=colour, width=stroke,
        ),
    ]
    dot = max(1, _round(size * 0.09))
    items.append(
        canvas.create_rectangle(
            _round(x) - dot // 2, _round(y - half * 0.56) - dot,
            _round(x) - dot // 2 + dot, _round(y - half * 0.56),
            fill=colour, outline="",
        )
    )
    return items


def close(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    # Etwas eingezogen: Tk rechnet die Strichbreite auf den Kasten drauf.
    half = size / 2.0 * 0.84
    stroke = _stroke(size)
    return [
        canvas.create_line(
            _snap(x - half), _snap(y - half), _snap(x + half), _snap(y + half),
            fill=colour, width=stroke,
        ),
        canvas.create_line(
            _snap(x - half), _snap(y + half), _snap(x + half), _snap(y - half),
            fill=colour, width=stroke,
        ),
    ]


def check(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    """Haken - markiert einen Track in der Tag List (Handbuch S. 40)."""
    half = size / 2.0
    stroke = _stroke(size)
    return [
        canvas.create_line(
            _snap(x - half), _snap(y),
            _snap(x - half * 0.25), _snap(y + half * 0.8),
            _snap(x + half), _snap(y - half * 0.8),
            fill=colour, width=stroke,
        )
    ]


def star(
    canvas, x: float, y: float, size: float, colour: str, *, filled: bool = True
) -> list[int]:
    """Stern der Bewertung. Ungefuellt bleibt er ein leerer Umriss."""
    half = size / 2.0
    points: list[float] = []
    for step in range(10):
        # Aussen- und Innenpunkte abwechselnd; Start oben.
        radius = half if step % 2 == 0 else half * 0.42
        angle = math.radians(-90 + step * 36)
        points.extend(
            (x + radius * math.cos(angle), y + radius * math.sin(angle))
        )
    return [
        canvas.create_polygon(
            *points,
            fill=colour if filled else "",
            outline=colour,
            width=1,
        )
    ]


def minus(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    half = size / 2.0 * 0.86
    return [
        canvas.create_line(
            _snap(x - half), _snap(y), _snap(x + half), _snap(y),
            fill=colour, width=_stroke(size),
        )
    ]


def plus(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    half = size / 2.0 * 0.86
    stroke = _stroke(size)
    return [
        canvas.create_line(
            _snap(x - half), _snap(y), _snap(x + half), _snap(y),
            fill=colour, width=stroke,
        ),
        canvas.create_line(
            _snap(x), _snap(y - half), _snap(x), _snap(y + half),
            fill=colour, width=stroke,
        ),
    ]


def note(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    """Note - Platzhalter, wo kein Artwork vorliegt."""
    half = size / 2.0
    stroke = _stroke(size)
    stem = _snap(x + half * 0.35)
    head = max(2, _round(size * 0.3))
    return [
        canvas.create_line(
            stem, _round(y + half * 0.55), stem, _round(y - half),
            fill=colour, width=stroke,
        ),
        canvas.create_line(
            stem, _round(y - half),
            _snap(x + half * 0.95), _round(y - half * 0.62),
            fill=colour, width=stroke,
        ),
        canvas.create_oval(
            _round(stem) - head, _round(y + half * 0.55) - head // 2,
            _round(stem), _round(y + half * 0.55) + head // 2,
            fill=colour, outline="",
        ),
    ]


def search(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    """Lupe."""
    half = size / 2.0
    stroke = _stroke(size)
    radius = half * 0.62
    cx, cy = x - half * 0.16, y - half * 0.16
    return [
        canvas.create_oval(
            _snap(cx - radius), _snap(cy - radius),
            _snap(cx + radius), _snap(cy + radius),
            outline=colour, width=stroke,
        ),
        canvas.create_line(
            _snap(cx + radius * 0.72), _snap(cy + radius * 0.72),
            _snap(x + half), _snap(y + half),
            fill=colour, width=stroke,
        ),
    ]


def load(canvas, x: float, y: float, size: float, colour: str) -> list[int]:
    """Pfeil nach unten auf eine Ablage - Track laden."""
    half = size / 2.0
    stroke = _stroke(size)
    cx = _snap(x)
    tip = _round(size * 0.26)
    bottom = _round(y + half)
    return [
        canvas.create_line(
            cx, _round(y - half), cx, bottom - tip,
            fill=colour, width=stroke,
        ),
        canvas.create_polygon(
            cx - tip, bottom - tip, cx + tip, bottom - tip, cx, bottom,
            fill=colour, outline="",
        ),
    ]


#: Name -> Zeichenfunktion. Fuer Aufrufer, die den Namen zur Laufzeit haben.
ICONS: dict[str, Callable[..., list[int]]] = {
    "play": play,
    "pause": pause,
    "stop": stop,
    "cue": cue,
    "loop": loop,
    "chevron_left": chevron_left,
    "chevron_right": chevron_right,
    "chevron_up": chevron_up,
    "chevron_down": chevron_down,
    "arrow_left": arrow_left,
    "caret_up": caret_up,
    "caret_down": caret_down,
    "triangle_left": triangle_left,
    "triangle_right": triangle_right,
    "jump_back": jump_back,
    "jump_forward": jump_forward,
    "info": info,
    "close": close,
    "minus": minus,
    "plus": plus,
    "note": note,
    "search": search,
    "load": load,
}


def draw(
    canvas, name: str, x: float, y: float, size: float, colour: str
) -> list[int]:
    """Symbol nach Namen zeichnen. Unbekannter Name zeichnet nichts."""
    function = ICONS.get(name)
    if function is None:
        return []
    return function(canvas, x, y, size, colour)
