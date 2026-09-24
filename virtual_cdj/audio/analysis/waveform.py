"""Waveform-Vorberechnung: drei Frequenzbaender plus Dynamik.

Laeuft **einmal** beim Import und wird danach gecacht. Waehrend der
Wiedergabe wird nie wieder in die Audiodatei geschaut - der Renderer liest
nur noch aus diesem Cache.

Je Zeitfenster werden fuenf Werte abgelegt:

* ``low``   Bass          bis  250 Hz
* ``mid``   Mitten        250 - 2500 Hz
* ``high``  Hoehen        ab  2500 Hz
* ``peak``  Breitband-Spitzenwert  - haelt Transienten wie Kick und Snare
* ``rms``   Breitband-Effektivwert - gibt der Waveform ihren Koerper

Die drei Baender bestimmen spaeter die **Farbe**, ``peak`` und ``rms``
zusammen die **Hoehe**. Beides getrennt zu speichern ist der Grund, warum
die Darstellung nicht wie eine flackernde Amplitudenlinie aussieht.

Es werden mehrere Aufloesungsstufen erzeugt, damit die Oberflaeche fuer
Uebersicht und Zoom nicht dieselben Daten neu durchsuchen muss.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

BAND_LOW_HZ = 250.0
BAND_HIGH_HZ = 2500.0

#: Aufloesungsstufen: Name -> Peaks pro Sekunde.
LEVELS: dict[str, float] = {
    "overview": 20.0,
    "medium": 80.0,
    "detailed": 320.0,
}

#: Filterordnung der Butterworth-Baender.
FILTER_ORDER = 4


@dataclass(frozen=True)
class BandPeaks:
    """Bandenergien und Dynamik eines Zeitfensters, normiert auf 0.0 - 1.0.

    ``peak`` und ``rms`` sind breitbandig und beziehen sich auf dasselbe
    Signal wie die Baender. Aeltere Analysen ohne diese Felder bleiben
    lesbar: die Arrays sind dann leer.
    """

    low: np.ndarray
    mid: np.ndarray
    high: np.ndarray
    peaks_per_second: float
    peak: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    rms: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))

    @property
    def has_dynamics(self) -> bool:
        """Ob ``peak`` und ``rms`` vorliegen."""
        return (
            self.peak.shape[0] == self.low.shape[0]
            and self.rms.shape[0] == self.low.shape[0]
            and self.low.shape[0] > 0
        )

    @property
    def length(self) -> int:
        return int(self.low.shape[0])

    @property
    def duration_s(self) -> float:
        if self.peaks_per_second <= 0:
            return 0.0
        return self.length / self.peaks_per_second

    def index_at(self, position_s: float) -> int:
        return int(position_s * self.peaks_per_second)

    def is_consistent(self) -> bool:
        return (
            self.peaks_per_second > 0
            and self.low.shape == self.mid.shape == self.high.shape
        )

    @property
    def nbytes(self) -> int:
        return int(
            self.low.nbytes + self.mid.nbytes + self.high.nbytes
            + self.peak.nbytes + self.rms.nbytes
        )


def _split_bands(
    mono: np.ndarray, sample_rate: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Signal in drei Frequenzbaender trennen."""
    from scipy import signal

    nyquist = sample_rate / 2.0
    low_cut = min(BAND_LOW_HZ / nyquist, 0.99)
    high_cut = min(BAND_HIGH_HZ / nyquist, 0.99)

    sos_low = signal.butter(FILTER_ORDER, low_cut, btype="lowpass", output="sos")
    sos_mid = signal.butter(
        FILTER_ORDER, [low_cut, high_cut], btype="bandpass", output="sos"
    )
    sos_high = signal.butter(
        FILTER_ORDER, high_cut, btype="highpass", output="sos"
    )

    return (
        signal.sosfilt(sos_low, mono).astype(np.float32),
        signal.sosfilt(sos_mid, mono).astype(np.float32),
        signal.sosfilt(sos_high, mono).astype(np.float32),
    )


def _reshape(band: np.ndarray, bin_size: int, bins: int) -> np.ndarray:
    """Signal auf ``(bins, bin_size)`` bringen, notfalls mit Nullen."""
    needed = bins * bin_size
    if band.size < needed:
        band = np.pad(band, (0, needed - band.size))
    else:
        band = band[:needed]
    return band.reshape(bins, bin_size)


def _bin_peaks(band: np.ndarray, bin_size: int, bins: int) -> np.ndarray:
    """Betragsmaximum je Fenster. Vektorisiert, ohne Python-Schleife."""
    return _reshape(np.abs(band), bin_size, bins).max(axis=1)


def _bin_rms(band: np.ndarray, bin_size: int, bins: int) -> np.ndarray:
    """Effektivwert je Fenster."""
    blocks = _reshape(band, bin_size, bins)
    return np.sqrt(np.mean(np.square(blocks, dtype=np.float64), axis=1))


def compute_levels(
    mono: np.ndarray,
    sample_rate: int,
    levels: dict[str, float] | None = None,
) -> dict[str, BandPeaks]:
    """RGB-Peaks in allen Aufloesungsstufen berechnen.

    Args:
        mono: 1D-float32-Signal.
        sample_rate: Rate von ``mono``.
        levels: Name -> Peaks pro Sekunde. Standard ist ``LEVELS``.
    """
    levels = levels or LEVELS
    if mono.size == 0:
        empty = np.zeros(0, dtype=np.float32)
        return {
            name: BandPeaks(empty, empty, empty, rate)
            for name, rate in levels.items()
        }

    low, mid, high = _split_bands(mono, sample_rate)

    # Gemeinsame Normierung ueber alle Baender, damit die Farbanteile
    # untereinander vergleichbar bleiben.
    scale = float(
        max(
            np.max(np.abs(low)),
            np.max(np.abs(mid)),
            np.max(np.abs(high)),
            1e-9,
        )
    )
    # Dynamik wird am unveraenderten Signal gemessen und getrennt normiert:
    # sie beschreibt die Lautstaerke, nicht die Klangfarbe.
    #
    # TODO (AUTO CUE LEVEL): ``full_scale`` wird hier berechnet und danach
    # weggeworfen. Damit sind ``peak``/``rms`` **trackrelativ** - eine
    # AUTO-CUE-Schwelle von "-60 dB" bedeutet deshalb "60 dB unter dem
    # lautesten Punkt dieses Tracks", nicht -60 dBFS wie am Geraet. Fuer
    # absolute Schwellen muesste dieser Wert in ``BandPeaks`` mitgefuehrt,
    # im Analyse-Cache mitgespeichert (Formatversion!) und bis
    # ``deck/state.WaveformData`` durchgereicht werden. Die vollstaendige
    # Schrittfolge steht in ``deck/auto_cue.py``.
    full_scale = float(max(np.max(np.abs(mono)), 1e-9))

    result: dict[str, BandPeaks] = {}
    for name, peaks_per_second in levels.items():
        bin_size = max(1, int(round(sample_rate / peaks_per_second)))
        bins = max(1, int(np.ceil(mono.size / bin_size)))
        result[name] = BandPeaks(
            low=np.clip(_bin_peaks(low, bin_size, bins) / scale, 0.0, 1.0)
            .astype(np.float32),
            mid=np.clip(_bin_peaks(mid, bin_size, bins) / scale, 0.0, 1.0)
            .astype(np.float32),
            high=np.clip(_bin_peaks(high, bin_size, bins) / scale, 0.0, 1.0)
            .astype(np.float32),
            peak=np.clip(
                _bin_peaks(mono, bin_size, bins) / full_scale, 0.0, 1.0
            ).astype(np.float32),
            rms=np.clip(
                _bin_rms(mono, bin_size, bins) / full_scale, 0.0, 1.0
            ).astype(np.float32),
            peaks_per_second=sample_rate / bin_size,
        )
    return result
