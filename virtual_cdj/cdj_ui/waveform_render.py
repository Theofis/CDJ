"""Waveform zeichnen - CDJ-Darstellung aus vorberechneten Werten.

Ein Tk-Canvas mit tausenden Einzellinien pro Bild erreicht keine 60 FPS.
Deshalb wird die Waveform mit numpy in ein Bild gerechnet und als
``PhotoImage`` auf das Canvas geblittet.

Hier findet **keine** Analyse statt. Verwendet werden ausschliesslich die
vorberechneten Werte aus ``WaveformData``:

* **Hoehe** aus Effektiv- und Spitzenwert, logarithmisch gestaucht. Nur der
  Spitzenwert ergaebe eine zappelige Linie, nur der Effektivwert einen
  Block ohne Transienten.
* **Farbe** aus dem *Verhaeltnis* der drei Baender, nicht aus
  ``R=Bass, G=Mitten, B=Hoehen``. Eine direkte Zuordnung wird bei jeder
  ausgeglichenen Mischung bunt und flach; ueber eine Palette bleibt
  erkennbar, **was** klingt: Bass orange, Mitten gruen-tuerkis, Hoehen
  blau, breitbandig nahezu weiss.

Die Kanten der Balken werden mit Teildeckung gezeichnet. Das ist auf einem
1024x600-Schirm sichtbar ruhiger als harte Pixelkanten und kostet nur
Rechenzeit in der Groessenordnung der Balkenzahl, nicht der Bildflaeche.
"""

from __future__ import annotations

import numpy as np

from . import theme
from ..deck.display_state import WaveformMode
from ..deck.state import WaveformData

#: Hintergrund der Waveform-Flaeche als RGB. Kommt wie alle Farben aus
#: ``theme``; keine Farbe wird hier neu erfunden.
BACKGROUND = theme.rgb(theme.WAVE_BG)

#: Untergrenze der Balkenhoehe in Pixeln, damit stille Stellen sichtbar sind.
MIN_BAR_PIXELS = 1.0

#: Mischung von Effektiv- und Spitzenwert zur Balkenhoehe.
RMS_WEIGHT = 0.65
PEAK_WEIGHT = 0.35

#: Sicherheitsabstand nach oben. Der lauteste Balken des Tracks reicht auf
#: 99 % der halben Flaeche - nah genug am Rand, aber mit Luft gegen
#: Rundungsfehler, damit garantiert nichts abgeschnitten wird.
DISPLAY_HEADROOM = 0.99

#: Attribut, unter dem der einmal berechnete Anzeigemassstab am
#: ``WaveformData`` haengt. Siehe ``display_scale``.
_SCALE_ATTR = "_display_scale"

#: Staerke der logarithmischen Stauchung. Groessere Werte heben Leises
#: staerker an. Bei 10 bleibt ein Pegel von 10 % noch mit rund 30 % Hoehe
#: sichtbar, waehrend laute Kicks nicht dauerhaft am Rand kleben.
COMPRESSION_K = 10.0

#: Ankerfarben der spektralen Palette.
ANCHOR_LOW = theme.rgb(theme.WAVE_LOW)
ANCHOR_MID = theme.rgb(theme.WAVE_MID)
ANCHOR_HIGH = theme.rgb(theme.WAVE_HIGH)
WHITE: tuple[int, int, int] = (255, 255, 255)

#: Anhebung des Kerns nahe der Mittellinie. Rekordbox zeichnet die
#: Wellenform dort heller; das gibt ihr Tiefe statt einer flachen Flaeche.
CORE_LIFT = theme.WAVE_CORE_LIFT
#: Anteil der halben Balkenhoehe, der als Kern gilt.
CORE_SHARE = 0.42

#: Exponent auf die Bandanteile. Ueber 1 hebt das dominante Band hervor.
COLOR_GAMMA = 1.6

#: Wie weit ein breitbandiges Signal Richtung Weiss geht.
WHITE_MIX = 0.55

#: Grundhelligkeit. Der Rest kommt aus der Amplitude, damit leise Stellen
#: nicht genauso leuchten wie laute.
COLOR_FLOOR = 0.62

#: 3BAND: Bass, Mitten, Hoehen als getrennte Flaechen - wie am Geraet.
THREE_BAND_COLORS: tuple[tuple[int, int, int], ...] = (
    theme.rgb(theme.WAVE_BAND_LOW),
    theme.rgb(theme.WAVE_BAND_MID),
    theme.rgb(theme.WAVE_BAND_HIGH),
)

#: Einfarbige Darstellung, wie ``BLUE`` am Geraet.
BLUE_MODE_COLOR = theme.rgb(theme.WAVE_HIGH)


def compress(amplitude: np.ndarray, k: float = COMPRESSION_K) -> np.ndarray:
    """Logarithmische Stauchung auf 0.0 - 1.0.

    ``log1p(k*a) / log1p(k)`` - monoton, bildet 0 auf 0 und 1 auf 1 ab.
    """
    if k <= 0:
        return amplitude
    return np.log1p(k * amplitude) / np.log1p(k)


def mixed_amplitude(
    peak: np.ndarray,
    rms: np.ndarray,
    low: np.ndarray,
    mid: np.ndarray,
    high: np.ndarray,
) -> np.ndarray:
    """Balkenhoehe **vor** Massstab und Stauchung.

    Effektiv- und Spitzenwert gemischt: nur der Spitzenwert ergaebe eine
    zappelige Linie, nur der Effektivwert einen Block ohne Transienten.
    Ohne Dynamikwerte (aeltere Analyse, einfache Quelle) wird auf das
    lauteste Band zurueckgefallen - ehrlich gerundet statt erfunden.
    """
    if peak.any() or rms.any():
        return RMS_WEIGHT * rms + PEAK_WEIGHT * peak
    return np.maximum(np.maximum(low, mid), high)


def display_scale(level: WaveformData) -> float:
    """Anzeigemassstab dieses Tracks - **einmal** je Aufloesungsstufe.

    Warum es den braucht: die Werte der Analyse sind auf 0-1 normiert, aber
    die Mischung aus Effektiv- und Spitzenwert erreicht die 1 nie. Gemessen
    landete die lauteste Stelle je nach Track bei 67 % bis 94 % der
    verfuegbaren Hoehe - bei einem leisen Track blieb also ein Drittel der
    Flaeche ungenutzt.

    Der Massstab wird ueber den **ganzen** Track gebildet, nicht ueber den
    sichtbaren Ausschnitt. Eine abschnittsweise Normierung liesse die
    Waveform beim Scrollen pumpen: derselbe Kick waere im leisen Intro hoch
    und im lauten Drop niedrig.

    Gerechnet wird einmal je ``WaveformData`` und danach am Objekt
    gemerkt - nicht in jedem Bild. Ein Track wird einmal analysiert, seine
    Stufen sind unveraenderlich, also ist auch der Massstab konstant.
    """
    cached = getattr(level, _SCALE_ATTR, None)
    if cached is not None:
        return float(cached)

    arrays = {
        name: np.asarray(getattr(level, name), dtype=np.float32)
        for name in ("peak", "rms", "low", "mid", "high")
    }
    mixed = mixed_amplitude(**arrays)
    loudest = float(mixed.max()) if mixed.size else 0.0
    scale = DISPLAY_HEADROOM / loudest if loudest > 1e-6 else 1.0
    # ``WaveformData`` ist unveraenderlich; der Massstab ist ein reines
    # Rechenergebnis daraus und wird deshalb am Objekt gemerkt.
    object.__setattr__(level, _SCALE_ATTR, scale)
    return scale


def spectral_color(
    low: np.ndarray, mid: np.ndarray, high: np.ndarray
) -> np.ndarray:
    """Farbe je Spalte aus dem Verhaeltnis der drei Baender.

    Rueckgabe: ``(n, 3)`` float32 in 0.0 - 1.0.
    """
    total = np.maximum(low + mid + high, 1e-9)
    weights = np.stack(
        (low / total, mid / total, high / total), axis=1
    ).astype(np.float32)
    np.power(weights, COLOR_GAMMA, out=weights)
    weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-9)

    anchors = np.array(
        (ANCHOR_LOW, ANCHOR_MID, ANCHOR_HIGH), dtype=np.float32
    ) / 255.0
    colour = weights @ anchors

    # Breitbandig heisst: kein Band dominiert. Solche Stellen gehen Richtung
    # Weiss, wie in der rekordbox-Darstellung.
    flatness = 1.0 - (weights.max(axis=1) - weights.min(axis=1))
    mix = (flatness * WHITE_MIX)[:, None]
    colour += (1.0 - colour) * mix
    return np.clip(colour, 0.0, 1.0)


class WaveformRenderer:
    """Rechnet vorberechnete Werte in ein RGB-Bild.

    Puffer werden wiederverwendet; im Dauerbetrieb entstehen keine neuen
    Arrays in Bildgroesse.
    """

    def __init__(self) -> None:
        self._rgb: np.ndarray | None = None
        self._size: tuple[int, int] = (0, 0)
        self._rows: np.ndarray | None = None

    # ------------------------------------------------------------------

    def _buffers(self, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
        if self._rgb is None or self._size != (width, height):
            self._rgb = np.empty((height, width, 3), dtype=np.uint8)
            self._rows = np.arange(height, dtype=np.float32)[:, None]
            # Der Abstand jeder Zeile zur Mittellinie haengt nur von der
            # Hoehe ab. Einmal rechnen statt in jedem Bild.
            centre = (height - 1) / 2.0
            self._distance = np.abs(self._rows - centre).astype(np.float32)
            self._mask = np.empty((height, width), dtype=bool)
            self._size = (width, height)
        assert self._rows is not None
        return self._rgb, self._rows

    # ------------------------------------------------------------------

    @staticmethod
    def _columns(
        level: WaveformData,
        start_s: float,
        end_s: float,
        width: int,
    ) -> dict[str, np.ndarray]:
        """Vorberechnete Werte auf ``width`` Spalten zusammenfassen.

        Je Spalte ein Wert - genau das, was ein Pixel darstellen kann.
        Baender und Spitzenwert werden als Maximum zusammengefasst, der
        Effektivwert energierichtig ueber die Quadrate.

        Bereiche vor dem Trackanfang oder hinter dem Trackende bleiben leer.
        """
        fields = ("low", "mid", "high", "peak", "rms")
        source = {
            name: np.asarray(getattr(level, name), dtype=np.float32)
            for name in fields
        }
        out = {name: np.zeros(width, dtype=np.float32) for name in fields}

        total = source["low"].shape[0]
        if total == 0 or width <= 0 or end_s <= start_s:
            return out
        if not level.has_dynamics:
            source["peak"] = np.zeros(0, dtype=np.float32)
            source["rms"] = np.zeros(0, dtype=np.float32)

        pps = level.peaks_per_second
        # Spaltengrenzen als Gleitkomma-Indizes in die Wertelisten.
        edges = np.linspace(start_s * pps, end_s * pps, width + 1)
        first = np.floor(edges[:-1]).astype(np.int64)
        last = np.ceil(edges[1:]).astype(np.int64)

        valid = (last > 0) & (first < total)
        if not valid.any():
            return out

        clipped_first = np.clip(first, 0, total - 1)
        clipped_last = np.clip(last, 1, total)
        # Mindestens ein Wert je Spalte, sonst liefert reduceat Muell.
        clipped_last = np.maximum(clipped_last, clipped_first + 1)

        # Bei starkem Zoom deckt eine Spalte weniger als einen Wert ab -
        # dann genuegt direktes Indizieren.
        span = clipped_last - clipped_first
        if int(span.max()) <= 1:
            index = clipped_first
            for name, values in source.items():
                if values.shape[0] == total:
                    out[name][valid] = values[index][valid]
            return out

        # ``reduceat`` fasst von jedem Startindex bis zum naechsten zusammen.
        # Die letzte Spalte wuerde bis zum Arrayende laufen; damit sie nur
        # ihren eigenen Bereich abdeckt, wird ein Endindex angehaengt.
        indices = clipped_first
        end_index = int(clipped_last[-1])
        if end_index < total:
            indices = np.concatenate([clipped_first, [end_index]])

        for name in ("low", "mid", "high", "peak"):
            values = source[name]
            if values.shape[0] != total:
                continue
            aggregated = np.maximum.reduceat(values, indices)[:width]
            out[name][: aggregated.shape[0]] = aggregated
            out[name][~valid] = 0.0

        rms = source["rms"]
        if rms.shape[0] == total:
            counts = np.diff(
                np.concatenate([indices, [total]])
            )[:width].astype(np.float32)
            energy = np.add.reduceat(np.square(rms), indices)[:width]
            aggregated = np.sqrt(
                energy / np.maximum(counts[: energy.shape[0]], 1.0)
            )
            out["rms"][: aggregated.shape[0]] = aggregated
            out["rms"][~valid] = 0.0
        return out

    # ------------------------------------------------------------------

    @staticmethod
    def amplitude_of(
        columns: dict[str, np.ndarray], scale: float = 1.0
    ) -> np.ndarray:
        """Balkenhoehe 0.0 - 1.0 aus Effektiv- und Spitzenwert.

        ``scale`` ist der Anzeigemassstab des Tracks (``display_scale``).
        Er ist fuer den ganzen Track derselbe, damit die Waveform beim
        Scrollen nicht pumpt. Die Werte werden **vor** der Stauchung
        skaliert; abgeschnitten wird nichts, weil der Massstab so gewaehlt
        ist, dass die lauteste Stelle des Tracks genau unter dem Rand
        landet.
        """
        mixed = mixed_amplitude(
            columns["peak"], columns["rms"],
            columns["low"], columns["mid"], columns["high"],
        )
        return np.clip(compress(mixed * scale), 0.0, 1.0)

    # ------------------------------------------------------------------

    def _fill_bars(
        self,
        rgb: np.ndarray,
        rows: np.ndarray,
        half: np.ndarray,
        colour: np.ndarray,
        centre: float,
    ) -> None:
        """Balken von der Mittellinie nach oben und unten fuellen.

        Innen deckend, an den beiden Kanten mit Teildeckung - das nimmt
        der Waveform das Treppenmuster, ohne die ganze Bildflaeche
        durchrechnen zu muessen.
        """
        height, width = rgb.shape[0], rgb.shape[1]
        background = np.array(BACKGROUND, dtype=np.float32)
        colour255 = np.clip(colour, 0.0, 1.0) * 255.0

        # Innenflaeche und hellerer Kern. Gearbeitet wird nur auf den Zeilen,
        # die der lauteste Balken ueberhaupt erreicht - bei ruhigen Stellen
        # ist das ein Bruchteil des Bildes.
        solid = np.maximum(half - 0.5, 0.0)
        solid_colour = colour255.astype(np.uint8)
        core_colour = np.clip(
            colour255 * (1.0 + CORE_LIFT), 0.0, 255.0
        ).astype(np.uint8)

        for share, paint in (
            (1.0, solid_colour), (CORE_SHARE, core_colour),
        ):
            if share < 1.0 and CORE_LIFT <= 0.0:
                continue
            limit = solid * share
            reach = float(limit.max())
            if reach <= 0.0:
                continue
            top = max(0, int(centre - reach) - 1)
            bottom = min(height, int(centre + reach) + 2)
            band = self._distance[top:bottom]
            view = rgb[top:bottom]
            mask = self._mask[top:bottom]
            np.less_equal(band, limit[None, :], out=mask)
            np.copyto(view, paint[None, :, :], where=mask[:, :, None])

        self._blend_edges(rgb, half, colour, centre)

    def _blend_edges(
        self,
        rgb: np.ndarray,
        half: np.ndarray,
        colour: np.ndarray,
        centre: float,
    ) -> None:
        """Ober- und Unterkante mit Teildeckung zeichnen.

        Nur zwei Zeilen je Spalte, also Aufwand in der Groessenordnung der
        Bildbreite - nicht der Bildflaeche.
        """
        height, width = rgb.shape[0], rgb.shape[1]
        background = np.array(BACKGROUND, dtype=np.float32)
        colour255 = np.clip(colour, 0.0, 1.0) * 255.0
        columns = np.arange(width)
        for sign in (-1.0, +1.0):
            edge = centre + sign * half
            row = np.rint(edge).astype(np.int64)
            coverage = np.clip(
                np.where(sign < 0, row + 0.5 - edge, edge - (row - 0.5)),
                0.0, 1.0,
            ).astype(np.float32)
            inside = (row >= 0) & (row < height) & (half > 0.0)
            if not inside.any():
                continue
            rows_i = row[inside]
            cols_i = columns[inside]
            blended = (
                background[None, :]
                + (colour255[inside] - background[None, :])
                * coverage[inside][:, None]
            )
            existing = rgb[rows_i, cols_i].astype(np.float32)
            # Die hellere Farbe gewinnt: sonst wuerde die Kante eine bereits
            # gezeichnete Flaeche wieder abdunkeln.
            rgb[rows_i, cols_i] = np.maximum(existing, blended).astype(np.uint8)

    # ------------------------------------------------------------------

    def render_array(
        self,
        level: WaveformData,
        start_s: float,
        end_s: float,
        width: int,
        height: int,
        mode: WaveformMode = WaveformMode.RGB,
        dim: float = 1.0,
    ) -> np.ndarray:
        """RGB-Array ``(height, width, 3)`` erzeugen.

        ``mode`` entspricht ``WAVEFORM COLOR`` am Geraet. ``dim`` unter 1.0
        dunkelt die Farbe ab - dafuer, den bereits gespielten Teil der
        Uebersicht zurueckzunehmen.
        """
        rgb, rows = self._buffers(width, height)
        rgb[:] = BACKGROUND

        columns = self._columns(level, start_s, end_s, width)
        amplitude = self.amplitude_of(columns, display_scale(level))
        if not amplitude.any():
            return rgb

        centre = (height - 1) / 2.0

        if mode is WaveformMode.THREE_BAND:
            self._draw_three_band(
                rgb, rows, columns, amplitude, centre, dim
            )
            return rgb

        if mode is WaveformMode.BLUE:
            colour = np.tile(
                np.array(BLUE_MODE_COLOR, dtype=np.float32) / 255.0 * dim,
                (width, 1),
            )
        else:
            colour = spectral_color(
                columns["low"], columns["mid"], columns["high"]
            )

        brightness = COLOR_FLOOR + (1.0 - COLOR_FLOOR) * amplitude
        colour = colour * brightness[:, None] * dim

        half = np.maximum(amplitude * centre, MIN_BAR_PIXELS)
        self._fill_bars(rgb, rows, half, colour, centre)
        return rgb

    def _draw_three_band(
        self,
        rgb: np.ndarray,
        rows: np.ndarray,
        columns: dict[str, np.ndarray],
        amplitude: np.ndarray,
        centre: float,
        dim: float = 1.0,
    ) -> None:
        """3BAND: Bass, Mitten und Hoehen als getrennte Flaechen.

        Die Gesamthoehe kommt aus derselben Amplitude wie in den anderen
        Modi; die Baender teilen sie nach ihrem Anteil auf. Gezeichnet wird
        vom groessten zum kleinsten Band, damit alle drei sichtbar bleiben.
        """
        width = rgb.shape[1]
        low, mid, high = columns["low"], columns["mid"], columns["high"]
        total = np.maximum(low + mid + high, 1e-9)
        shares = [band / total for band in (low, mid, high)]
        # Das **dominante** Band erreicht die volle Amplitude, die uebrigen
        # ihren Anteil davon. Damit ist die aeussere Huellkurve dieselbe wie
        # in den anderen Modi und nichts kann ueber den Rand laufen.
        #
        # Vorher stand hier ``amplitude * anteil * 3.0`` mit anschliessendem
        # ``clip(..., 0, 1)``. Bei einem basslastigen Track erreichte der
        # Rohwert 1.87, und **alle** Spalten wurden oben abgeschnitten: der
        # Bassbalken war ein durchgehendes Rechteck ohne erkennbare Form.
        top = np.maximum(np.maximum(shares[0], shares[1]), shares[2])
        top = np.maximum(top, 1e-9)
        halves = [amplitude * (share / top) * centre for share in shares]
        order = sorted(
            range(3), key=lambda i: -float(halves[i].max())
        )

        # Statt drei Farbflaechen uebereinander wird eine Indexkarte gefuellt
        # und einmal in Farben uebersetzt. Das ist rund ein Drittel der
        # Schreibarbeit und haelt auch 3BAND unter dem Bildbudget.
        index_map = np.zeros(rgb.shape[:2], dtype=np.uint8)
        distance = np.abs(rows - centre)
        for band in order:
            np.copyto(
                index_map,
                np.uint8(band + 1),
                where=distance <= halves[band][None, :],
            )
        palette = np.empty((4, 3), dtype=np.uint8)
        palette[0] = BACKGROUND
        for band in range(3):
            palette[band + 1] = np.clip(
                np.array(THREE_BAND_COLORS[band], dtype=np.float32) * dim,
                0.0, 255.0,
            ).astype(np.uint8)
        np.take(palette, index_map, axis=0, out=rgb)

        # Weiche Kante nur an der aeusseren Silhouette - innen grenzen zwei
        # deckende Farben aneinander, dort braucht es keine Teildeckung.
        outer = order[0]
        colour = np.tile(
            np.array(THREE_BAND_COLORS[outer], dtype=np.float32)
            / 255.0 * dim,
            (width, 1),
        )
        self._blend_edges(rgb, halves[outer], colour, centre)

    def render_image(
        self,
        level: WaveformData,
        start_s: float,
        end_s: float,
        width: int,
        height: int,
        mode: WaveformMode = WaveformMode.RGB,
    ):
        """Als PIL-Bild erzeugen."""
        from PIL import Image

        array = self.render_array(
            level, start_s, end_s, width, height, mode
        )
        return Image.fromarray(array, mode="RGB")


class CanvasWaveform:
    """Haelt ein ``PhotoImage`` auf einem Canvas und tauscht nur die Pixel.

    Das Bildobjekt bleibt bestehen; pro Bild wird nur der Inhalt ersetzt.
    Damit entstehen keine neuen Tk-Objekte im Dauerbetrieb.
    """

    def __init__(self) -> None:
        self.renderer = WaveformRenderer()
        self._photo = None
        self._size: tuple[int, int] = (0, 0)

    @property
    def photo(self):
        return self._photo

    def update(
        self,
        level: WaveformData,
        start_s: float,
        end_s: float,
        width: int,
        height: int,
        mode: WaveformMode = WaveformMode.RGB,
        dim: float = 1.0,
    ):
        """Bild aktualisieren und das ``PhotoImage`` zurueckgeben."""
        if width <= 0 or height <= 0:
            return None
        from PIL import Image, ImageTk

        array = self.renderer.render_array(
            level, start_s, end_s, width, height, mode, dim
        )
        image = Image.fromarray(array, mode="RGB")

        if self._photo is None or self._size != (width, height):
            self._photo = ImageTk.PhotoImage(image)
            self._size = (width, height)
        else:
            self._photo.paste(image)
        return self._photo
