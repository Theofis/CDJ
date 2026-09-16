"""Farben, Schriften und Layout-Masse der CDJ-Bildschirmoberflaeche.

Zielaufloesung 1024 x 600 auf einem 7-Zoll-Panel. Alle Masse laufen ueber
``Metrics``, damit auch andere Groessen funktionieren.

**Schriftgroessen sind Pixel, nicht Punkte.** Tk multipliziert
Punktgroessen mit ``tk scaling`` - auf einem Windows-Rechner mit 125 %
Anzeige also 1.333. Das Layout rechnet aber in Pixeln, und die Texte waeren
ein Drittel zu gross fuer ihre Flaechen. Negative Schriftgroessen bedeuten
in Tk Pixel; damit gilt unabhaengig von der Bildschirm-DPI

    1 Layoutpixel = 1 Bildpixel.

Farben liegen ausschliesslich hier. Keine Komponente definiert eigene.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------
# Flaechen - eine Leiter aus fast gleichen Schwarztoenen
#
# Nicht jede Flaeche ist dasselbe Schwarz. Die Abstufungen sind bewusst
# klein: sie sollen Ebenen andeuten, keine Karten bilden.
# --------------------------------------------------------------------------

BG = "#07080a"           # Grundflaeche des Bildschirms
PANEL = "#0e1014"        # Leisten und Panels
PANEL_HI = "#15181d"     # hervorgehobene Flaeche, Listenauswahl
PANEL_ACTIVE = "#1d2128"  # gedrueckt oder aktiv
WAVE_BG = "#050607"      # Wellenformflaeche - die dunkelste Flaeche

#: Feine Trennlinie. Erste Wahl, wenn ueberhaupt eine Linie noetig ist.
LINE_SUBTLE = "#1b1f25"
#: Rahmen. Nur wo eine Flaeche wirklich abgegrenzt werden muss.
BORDER = "#262b33"

# --------------------------------------------------------------------------
# Text - vier Stufen statt ueberall Weiss
# --------------------------------------------------------------------------

TEXT = "#eef1f5"          # Titel, grosse Werte - kein reines Weiss
TEXT_SECOND = "#a9b2bd"   # zweite Ebene: Interpret, Werte in Listen
TEXT_DIM = "#727c88"      # Beschriftungen
TEXT_MUTED = "#4a525c"    # Platzhalter, Einheiten
TEXT_DISABLED = "#333a42"  # nicht verfuegbar

# --------------------------------------------------------------------------
# Akzente - gedaempft, nicht neon
# --------------------------------------------------------------------------

ACCENT = "#2f9ae8"
ORANGE = "#f5871f"
GREEN = "#22c176"
RED = "#e8483f"
YELLOW = "#e8c62f"
MAGENTA = "#b263e8"

#: Die acht Trackfarben von rekordbox. Die Datenbank speichert nur den
#: **Namen** (Tabelle ``colors``), nicht den Farbwert - das hier ist also
#: die Darstellung dieses Namens, kein aus den Daten gelesener Wert. Ein
#: unbekannter Name wird nicht geraten, sondern nicht gezeichnet.
#: Gehalten wie die uebrigen Akzente: kraeftig, aber nicht neon.
TRACK_COLORS: dict[str, str] = {
    "PINK": "#e85c9e",
    "RED": RED,
    "ORANGE": ORANGE,
    "YELLOW": YELLOW,
    "GREEN": GREEN,
    "AQUA": "#2fc8c8",
    "BLUE": ACCENT,
    "PURPLE": MAGENTA,
}


def track_colour(value: str) -> str:
    """Bildschirmfarbe zu einer rekordbox-Trackfarbe. Leer, wenn unbekannt."""
    if not value:
        return ""
    if value.startswith("#"):
        return value
    return TRACK_COLORS.get(value.strip().upper(), "")

#: Wellenform: Ankerfarben der spektralen Palette (Bass, Mitten, Hoehen).
#: Gedaempfter als reine Primaerfarben - kraeftig, aber nicht ueberstrahlend.
WAVE_LOW = "#ff6a1a"
WAVE_MID = "#4ad68a"
WAVE_HIGH = "#489cff"
#: 3BAND-Darstellung: Bass, Mitten, Hoehen als getrennte Flaechen.
WAVE_BAND_LOW = "#2e74ff"
WAVE_BAND_MID = "#ff8c1a"
WAVE_BAND_HIGH = "#ecf0f5"
#: Heller Kern nahe der Mittellinie - gibt der Wellenform Tiefe.
WAVE_CORE_LIFT = 0.35

PLAYHEAD = "#ffffff"
#: Dunkler Saum neben dem Playhead, damit er auch auf heller Wellenform
#: eine klare Kante hat.
PLAYHEAD_EDGE = "#050607"

#: Beatgrid in drei Stufen: Beat, Taktanfang, Phrase (4 Takte). Noch in
#: Gebrauch in der Uebersichtswellenform und der Kopfzeile.
BEAT_LINE = "#39434f"
BAR_LINE = "#6e7c8c"
PHRASE_LINE = "#aab6c4"

#: Beatgrid der vergroesserten laufenden Wellenform. Dort sind alle Linien
#: gleich hoch und gleich breit; unterschieden wird **nur** ueber die Farbe:
#: Beat 1 des Takts rot, die uebrigen grau.
GRID_DOWNBEAT = RED
GRID_BEAT = BAR_LINE

LOOP_FILL = "#3d2f0a"
LOOP_EDGE = "#e8c62f"
MEMORY_CUE = "#dfe5ec"

STATE_ON = ACCENT
STATE_OFF = "#2a3039"
MASTER_COLOR = ORANGE
SYNC_COLOR = ACCENT
QUANTIZE_COLOR = GREEN

#: Standardfarben der acht Pads, wenn die Cue-Daten keine Farbe vorgeben.
PAD_DEFAULT_COLORS: tuple[str, ...] = (
    "#ff4d4d", "#ff8c1a", "#ffd83a", "#26d07c",
    "#3aa7ff", "#5b6bff", "#c56bff", "#ff5fa2",
)

def rgb(colour: str) -> tuple[int, int, int]:
    """``#rrggbb`` in ein RGB-Tupel - fuer das Zeichnen mit numpy."""
    value = colour.lstrip("#")
    return (
        int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16),
    )


FONT_FAMILY = "Segoe UI"
FONT_FAMILY_MONO = "Consolas"

#: Die Groessenangaben in den Komponenten sind Stufen einer Skala, keine
#: Pixel. Dieser Faktor rechnet sie in Pixel um. 4/3 haelt die gewohnte
#: optische Groesse: Tk kam ueber ``tk scaling`` auf denselben Wert, nur
#: eben abhaengig von der Bildschirm-DPI. Eine Stelle zum Nachstellen.
FONT_PIXEL_FACTOR = 4.0 / 3.0

# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------

#: Referenzaufloesung, auf die sich alle Masse beziehen.
BASE_WIDTH = 1024
BASE_HEIGHT = 600

#: Zeilenhoehen bei Referenzaufloesung. Die laufende Waveform bekommt den Rest.
#: Summe der festen Zeilen bei 1024x600: 40+56+26+68+56+72 = 318, damit
#: bleiben rund 220 px fuer die laufende Wellenform (bzw. 156 mit
#: eingeblendeter Master-Zeile).
H_TOUCH_NAV = 40
H_TOP_BAR = 56
H_WAVE_HEADER = 26
H_MASTER_WAVE = 64

#: Unterer Bereich = Wiedergabestatusanzeige (Handbuch S. 21-23).
#: Zwei Zeilen, die zusammen ein L bilden: oben die Werte (Elemente 14-23),
#: unten die gesamte Wellenform (Element 26) zwischen den beiden
#: Seitenspalten. Eine Pad-Reihe gibt es am Geraet nicht - die Hot Cues
#: sind dort Tasten unter dem Display.
H_STATUS_BAR = 52
H_OVERVIEW = 74

#: Breite der Seitenspalten der Statusanzeige.
#: Links: PLAYER (oben), QUANTIZE und BEAT JUMP (unten) - Elemente 14, 13, 12.
#: Rechts: MASTER/SYNC (oben), MT und Tonart (unten) - Elemente 23, 24, 25.
STATUS_LEFT = 88
STATUS_RIGHT = 96

#: Hoehe der Touch-Panels, die sich ueber die Wellenform legen.
H_PANEL = 96

#: Frueheres Mass, noch von Tests referenziert.
H_TRACK_INFO = H_STATUS_BAR

#: Abgespielter Teil der gesamten Wellenform. Rekordbox und die
#: CDJ-Anzeige dunkeln ihn ab, damit auf einen Blick sichtbar ist, wie viel
#: noch kommt. Ein Wert von 1.0 schaltet die Abdunklung aus.
PLAYED_DIM = 0.42

PAD_X = 10
PAD_Y = 6


@dataclass(frozen=True)
class Metrics:
    """Skalierte Masse fuer eine konkrete Fenstergroesse."""

    width: int = BASE_WIDTH
    height: int = BASE_HEIGHT

    @property
    def scale(self) -> float:
        """Gemeinsamer Skalierungsfaktor, an der Hoehe orientiert."""
        return self.height / BASE_HEIGHT

    @property
    def scale_x(self) -> float:
        return self.width / BASE_WIDTH

    def px(self, value: float) -> int:
        return max(1, int(round(value * self.scale)))

    def snap(self, value: float) -> float:
        """Koordinate auf die Mitte eines Pixels legen.

        Tk zeichnet eine 1-px-Linie um die Koordinate herum. Bei ``100.0``
        liegt sie zwischen zwei Pixeln und wird auf zwei Reihen verteilt -
        also unscharf. Bei ``100.5`` deckt sie genau eine Pixelreihe.
        """
        return float(int(value)) + 0.5

    def font(self, size: int, weight: str = "normal") -> tuple:
        """Schrift in **Pixeln**. Negative Groesse heisst in Tk Pixel."""
        scaled = max(7, int(round(size * self.scale * FONT_PIXEL_FACTOR)))
        if weight == "normal":
            return (FONT_FAMILY, -scaled)
        return (FONT_FAMILY, -scaled, weight)

    def mono(self, size: int, weight: str = "normal") -> tuple:
        """Feste Zeichenbreite - fuer Zahlen, die sich laufend aendern.

        Damit springt keine Ziffernanzeige waagerecht, wenn sich der Wert
        aendert (154.9 und 155.0 sind gleich breit).
        """
        scaled = max(7, int(round(size * self.scale * FONT_PIXEL_FACTOR)))
        if weight == "normal":
            return (FONT_FAMILY_MONO, -scaled)
        return (FONT_FAMILY_MONO, -scaled, weight)

    @classmethod
    def for_widget(cls, widget) -> Metrics:  # pragma: no cover - GUI
        width = max(1, widget.winfo_width())
        height = max(1, widget.winfo_height())
        return cls(width=width, height=height)


DEFAULT_METRICS = Metrics()


def format_time(seconds: float, *, sign: str = "") -> str:
    """``m:ss`` mit optionalem Vorzeichen. Fuer Rest- und Laufzeit."""
    if seconds < 0:
        seconds = 0.0
    minutes = int(seconds // 60)
    rest = int(seconds % 60)
    return f"{sign}{minutes}:{rest:02d}"


def format_time_ms(seconds: float) -> str:
    """``m:ss.mmm`` - die grosse Zeitanzeige des CDJ."""
    if seconds < 0:
        seconds = 0.0
    minutes = int(seconds // 60)
    rest = seconds % 60
    return f"{minutes}:{rest:06.3f}"


def format_percent(percent: float) -> str:
    """Tempoabweichung mit Vorzeichen, z. B. ``+2.34 %``."""
    return f"{percent:+.2f} %"


def format_bpm(bpm: float) -> str:
    return f"{bpm:.2f}" if bpm > 0 else "--.--"
