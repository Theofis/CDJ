"""Farben, Schriften und Koordinatentransformation der virtuellen Oberflaeche."""

from __future__ import annotations

from dataclasses import dataclass

# --- Farben ---------------------------------------------------------------

BG_WINDOW = "#14161a"
BG_PANEL = "#3a3d42"
BG_PANEL_EDGE = "#22252a"
BG_SCREEN = "#1b1e23"
BG_SIDE = "#1b1e23"

FG_TEXT = "#e6e8ec"
FG_DIM = "#8a9098"
FG_LABEL = "#b9bfc7"

BTN_FACE = "#5a5f66"
BTN_FACE_ACTIVE = "#cfd4da"
BTN_FACE_LATCHED = "#a8814a"
BTN_EDGE = "#191b1f"
BTN_HOVER_EDGE = "#7fc4ff"

AMBER = "#e07b18"
AMBER_ACTIVE = "#ffb347"
RED = "#c0392b"
GREEN = "#2ecc71"
GREEN_DIM = "#1e5f3a"

# LED-Farben im Button-Editor: (eingeschaltet, ausgeschaltet/dunkel).
BUTTON_LED_COLORS = {
    "BLUE": ("#43a5ff", "#244866"),
    "ORANGE": ("#ff9d2e", "#70451d"),
    "GREEN": (GREEN, GREEN_DIM),
}

JOG_RING = "#2f3237"
JOG_PLATTER = "#4a4e54"
JOG_CENTER = "#c9ced4"
JOG_MARK = "#7fc4ff"

FADER_TRACK = "#1b1e23"
FADER_KNOB = "#cfd4da"

ACCENT = "#7fc4ff"
MONITOR_BG = "#101216"
MONITOR_FG = "#c8ced6"

GROUP_COLORS = {
    "LOOP": AMBER,
    "PERFORMANCE_PADS": "#8ab4f8",
    "PAD_MODE": "#c792ea",
    "TRANSPORT": "#a5d6a7",
    "TEMPO": "#ffd54f",
    "SYNC": "#80cbc4",
    "JOG": ACCENT,
    "UNRESOLVED": "#ff7043",
}

# --- Schriften ------------------------------------------------------------

FONT_TINY = ("Segoe UI", 6)
FONT_SMALL = ("Segoe UI", 7)
FONT_LABEL = ("Segoe UI", 8)
FONT_BODY = ("Segoe UI", 9)
FONT_HEAD = ("Segoe UI Semibold", 10)
FONT_MONO = ("Consolas", 9)
FONT_MONO_BIG = ("Consolas", 12, "bold")

# --- Geometrie ------------------------------------------------------------

#: Skalierung der Bildkoordinaten auf Bildschirmpixel.
SCALE = 1.2
#: Verschiebung, damit der leere Rand des Referenzbildes wegfaellt.
OFFSET_X = 80.0
OFFSET_Y = 38.0

PANEL_CANVAS_SIZE = (502, 792)


@dataclass(frozen=True)
class Transform:
    """Bildkoordinaten -> Canvas-Pixel."""

    scale: float = SCALE
    offset_x: float = OFFSET_X
    offset_y: float = OFFSET_Y

    def x(self, value: float) -> float:
        return (value - self.offset_x) * self.scale

    def y(self, value: float) -> float:
        return (value - self.offset_y) * self.scale

    def s(self, value: float) -> float:
        return value * self.scale


TRANSFORM = Transform()
