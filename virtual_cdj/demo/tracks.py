"""Demo-Tracks fuer den Displaytest.

Dies ist die **einzige** Stelle im Projekt, an der synthetische Analyse- und
Audiodaten erzeugt werden duerfen. Der Produktionspfad
(``audio/``, ``deck/``, ``cdj_ui/``) importiert dieses Paket nicht; nur die
Verdrahtung in ``app.py`` bindet es auf Wunsch ein.

Erkennbar bleibt es an ``TrackInfo.source == "DEMO"``.

Die Tracks liefern:

* ein **echtes** Beatgrid aus der BPM
* eine Waveform in allen drei Aufloesungsstufen, aus einer Songstruktur
  erzeugt - also nicht zufaellig, sondern plausibel und reproduzierbar
* Hot Cues an den Strukturgrenzen, davon einer als gespeicherter Loop
* auf Wunsch echte Audiosamples derselben Struktur, damit Wiedergabe, Loops
  und Cues hoerbar pruefbar sind
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..audio.format import ENGINE_SAMPLE_RATE, AudioBuffer
from ..deck.state import (
    BeatGrid,
    CueKind,
    HotCue,
    MemoryCue,
    TrackInfo,
    WaveformData,
    WaveformSet,
)

#: Aufloesungsstufen wie in der echten Analyse.
LEVELS: dict[str, float] = {
    "overview": 20.0,
    "medium": 80.0,
    "detailed": 320.0,
}

#: Farben der Hot Cues, an rekordbox angelehnt.
CUE_COLORS: tuple[str, ...] = (
    "#ff4d4d", "#ff8c1a", "#ffd83a", "#26d07c",
    "#3aa7ff", "#c56bff", "#ff5fa2", "#00e5c0",
)


@dataclass(frozen=True)
class Section:
    """Ein Abschnitt der Songstruktur.

    Die drei Pegel steuern, wie die Waveform in diesem Abschnitt aussieht -
    und, wenn Audio erzeugt wird, auch wie er klingt.
    """

    name: str
    start_s: float
    #: Pegel 0.0 - 1.0 fuer Bass, Mitten, Hoehen.
    low: float
    mid: float
    high: float
    #: Ob in diesem Abschnitt eine Bassdrum auf jedem Beat liegt.
    kick: bool = True
    #: Ob ein Hot Cue auf den Abschnittsanfang gelegt wird.
    cue: bool = True
    #: Wenn gesetzt, wird der Cue als gespeicherter Loop ueber so viele
    #: Beats angelegt.
    loop_beats: float | None = None


@dataclass(frozen=True)
class DemoTrackSpec:
    """Beschreibung eines Demo-Tracks."""

    track_id: str
    title: str
    artist: str
    album: str
    genre: str
    bpm: float
    key: str
    duration_s: float
    sections: tuple[Section, ...]
    label: str = ""
    rating: int = 0
    #: Erster Beat des Rasters in Sekunden.
    first_beat_s: float = 0.0
    memory_cues_s: tuple[float, ...] = ()


# --------------------------------------------------------------------------
# Die zwei Demo-Tracks
# --------------------------------------------------------------------------

BROKEN_WINDOW = DemoTrackSpec(
    track_id="demo-broken-window",
    title="Broken Window",
    artist="SHØRDY",
    album="Broken Window EP",
    genre="Industrial / Schranz",
    label="Demo",
    bpm=154.0,
    key="4A",
    duration_s=330.0,  # 5:30
    first_beat_s=0.06,
    sections=(
        Section("Intro",   0.0,   0.35, 0.20, 0.45, kick=True,  loop_beats=16),
        Section("Build",   48.0,  0.55, 0.45, 0.75),
        Section("Drop 1",  96.0,  1.00, 0.70, 0.85),
        Section("Break",   168.0, 0.15, 0.60, 0.50, kick=False),
        Section("Drop 2",  204.0, 1.00, 0.80, 0.90),
        Section("Outro",   288.0, 0.40, 0.25, 0.35),
    ),
    memory_cues_s=(24.0, 144.0),
)

STEEL_PRESSURE = DemoTrackSpec(
    track_id="demo-steel-pressure",
    title="Steel Pressure",
    artist="SHØRDY",
    album="Steel Pressure",
    genre="Hard Techno / Industrial",
    label="Demo",
    bpm=156.0,
    key="5A",
    duration_s=300.0,  # 5:00
    first_beat_s=0.19,
    # Anderer Aufbau: frueherer Drop, zwei Breaks, harter Schluss.
    sections=(
        Section("Intro",     0.0,   0.30, 0.15, 0.30),
        Section("Drop 1",    30.8,  0.95, 0.60, 0.70),
        Section("Break 1",   92.3,  0.10, 0.55, 0.65, kick=False),
        Section("Build",     123.1, 0.60, 0.65, 0.85, loop_beats=8),
        Section("Drop 2",    153.8, 1.00, 0.75, 0.80),
        Section("Break 2",   215.4, 0.12, 0.50, 0.70, kick=False),
        Section("Drop 3",    246.2, 1.00, 0.85, 0.95),
        Section("Outro",     284.6, 0.25, 0.20, 0.25, cue=False),
    ),
    memory_cues_s=(61.5, 184.6),
)

SPECS: tuple[DemoTrackSpec, ...] = (BROKEN_WINDOW, STEEL_PRESSURE)


# --------------------------------------------------------------------------
# Aufbau der Daten
# --------------------------------------------------------------------------


def _section_at(spec: DemoTrackSpec, seconds: float) -> Section:
    current = spec.sections[0]
    for section in spec.sections:
        if seconds >= section.start_s:
            current = section
        else:
            break
    return current


def _build_bands(
    spec: DemoTrackSpec, peaks_per_second: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Waveform-Baender und Dynamik aus der Songstruktur erzeugen.

    Rueckgabe: ``low, mid, high, peak, rms``. Deterministisch - derselbe
    Track ergibt immer dieselbe Waveform.
    """
    count = max(1, int(round(spec.duration_s * peaks_per_second)))
    times = np.arange(count) / peaks_per_second
    rng = np.random.default_rng(abs(hash(spec.track_id)) % (2**32))

    low = np.zeros(count, dtype=np.float32)
    mid = np.zeros(count, dtype=np.float32)
    high = np.zeros(count, dtype=np.float32)

    starts = np.array([s.start_s for s in spec.sections])
    index = np.clip(np.searchsorted(starts, times, side="right") - 1, 0, None)
    for number, section in enumerate(spec.sections):
        mask = index == number
        if not mask.any():
            continue
        low[mask] = section.low
        mid[mask] = section.mid
        high[mask] = section.high

    beat_s = 60.0 / spec.bpm
    phase = ((times - spec.first_beat_s) % beat_s) / beat_s

    # Bassdrum: kurzer Impuls am Beatanfang.
    kick = np.array(
        [_section_at(spec, float(t)).kick for t in times], dtype=bool
    )
    kick_envelope = np.clip(1.0 - phase * 6.0, 0.0, 1.0).astype(np.float32)
    low = np.where(kick, low * (0.72 + 0.28 * kick_envelope), low * 0.62)

    # Hi-Hat auf Achteln - kurz, damit das Hochband nicht dauerhaft
    # anliegt und die Farbinformation der Waveform erhalten bleibt.
    eighth_phase = ((times - spec.first_beat_s) % (beat_s / 2)) / (beat_s / 2)
    high = high * (0.12 + 0.88 * np.clip(1.0 - eighth_phase * 8.0, 0.0, 1.0))

    # Leichte Textur, damit die Flaeche nicht wie ein Rechteck aussieht.
    texture = rng.uniform(0.88, 1.0, size=count).astype(np.float32)
    low = np.clip(low * texture, 0.0, 1.0)
    mid = np.clip(mid * rng.uniform(0.8, 1.0, size=count), 0.0, 1.0)
    high = np.clip(high * rng.uniform(0.75, 1.0, size=count), 0.0, 1.0)

    # Sanftes Ein- und Ausblenden am Trackrand.
    fade = int(peaks_per_second * 2)
    if fade > 1 and count > 2 * fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        for band in (low, mid, high):
            band[:fade] *= ramp
            band[-fade:] *= ramp[::-1]

    # Dynamik: der Spitzenwert folgt dem lautesten Band und damit den
    # Transienten, der Effektivwert dem Energiemittel. Genau diese Trennung
    # nutzt der Renderer fuer die Balkenhoehe.
    peak = np.clip(np.maximum(np.maximum(low, mid), high), 0.0, 1.0)
    rms = np.clip(
        np.sqrt(0.5 * low**2 + 0.32 * mid**2 + 0.18 * high**2), 0.0, 1.0
    )

    return (
        low.astype(np.float32),
        mid.astype(np.float32),
        high.astype(np.float32),
        peak.astype(np.float32),
        rms.astype(np.float32),
    )


def build_waveform(spec: DemoTrackSpec) -> WaveformSet:
    """Alle Aufloesungsstufen erzeugen."""
    levels: dict[str, WaveformData] = {}
    for name, peaks_per_second in LEVELS.items():
        low, mid, high, peak, rms = _build_bands(spec, peaks_per_second)
        levels[name] = WaveformData(
            peaks_per_second=peaks_per_second,
            low=low, mid=mid, high=high, peak=peak, rms=rms,
        )
    return WaveformSet(levels=levels)


def build_beat_grid(spec: DemoTrackSpec) -> BeatGrid:
    return BeatGrid(
        first_beat_s=spec.first_beat_s,
        bpm=spec.bpm,
        beats_per_bar=4,
        first_downbeat_index=0,
    )


def build_hot_cues(spec: DemoTrackSpec) -> tuple[HotCue, ...]:
    """Hot Cues auf die Abschnittsgrenzen legen, beatgenau."""
    grid = build_beat_grid(spec)
    cues: list[HotCue] = []
    for section in spec.sections:
        if not section.cue or len(cues) >= 8:
            continue
        index = len(cues)
        position = grid.snap(section.start_s)
        loop_end = None
        kind = CueKind.CUE
        if section.loop_beats:
            kind = CueKind.LOOP
            loop_end = position + grid.seconds_for_beats(
                section.loop_beats, position
            )
        cues.append(
            HotCue(
                index=index,
                position_s=position,
                color=CUE_COLORS[index % len(CUE_COLORS)],
                kind=kind,
                loop_end_s=loop_end,
                comment=section.name,
            )
        )
    return tuple(cues)


def build_track_info(spec: DemoTrackSpec) -> TrackInfo:
    """``TrackInfo`` desselben Typs, den auch der echte Loader liefert."""
    grid = build_beat_grid(spec)
    return TrackInfo(
        track_id=spec.track_id,
        title=spec.title,
        artist=spec.artist,
        album=spec.album,
        genre=spec.genre,
        label=spec.label,
        duration_s=spec.duration_s,
        original_bpm=spec.bpm,
        key=spec.key,
        rating=spec.rating,
        file_path="",
        source="DEMO",
        waveform=build_waveform(spec),
        beat_grid=grid,
        hot_cues=build_hot_cues(spec),
        memory_cues=tuple(
            MemoryCue(position_s=grid.snap(seconds))
            for seconds in spec.memory_cues_s
        ),
        key_confidence=0.0,
        downbeat_confidence=0.0,
        analysis_version=0,
    )


def build_audio(
    spec: DemoTrackSpec, sample_rate: int = ENGINE_SAMPLE_RATE
) -> AudioBuffer:
    """Echte Samples aus derselben Struktur erzeugen.

    Kein vorgetaeuschtes Playback: hier entsteht ein synthetischer Track, der
    tatsaechlich klingt und dessen Struktur zur angezeigten Waveform passt.
    """
    frames = int(spec.duration_s * sample_rate)
    time_axis = np.arange(frames, dtype=np.float64) / sample_rate
    signal = np.zeros(frames, dtype=np.float64)
    beat_s = 60.0 / spec.bpm

    section_starts = np.array([s.start_s for s in spec.sections])
    section_index = np.clip(
        np.searchsorted(section_starts, time_axis, side="right") - 1, 0, None
    )

    gain_low = np.zeros(frames)
    gain_mid = np.zeros(frames)
    gain_high = np.zeros(frames)
    kick_on = np.zeros(frames, dtype=bool)
    for number, section in enumerate(spec.sections):
        mask = section_index == number
        if not mask.any():
            continue
        gain_low[mask] = section.low
        gain_mid[mask] = section.mid
        gain_high[mask] = section.high
        kick_on[mask] = section.kick

    # Bassdrum
    phase = (time_axis - spec.first_beat_s) % beat_s
    envelope = np.exp(-phase / 0.045)
    pitch_drop = 120.0 * np.exp(-phase / 0.03) + 48.0
    signal += (
        np.sin(2 * np.pi * pitch_drop * phase)
        * envelope
        * gain_low
        * kick_on
        * 0.9
    )

    # Bassflaeche
    signal += 0.25 * gain_low * np.sin(2 * np.pi * 55.0 * time_axis)

    # Mitten: Stab auf der Zaehlzeit 2 und 4
    beat_number = np.floor((time_axis - spec.first_beat_s) / beat_s)
    offbeat = (beat_number % 2) == 1
    stab_phase = phase
    signal += (
        0.35
        * gain_mid
        * offbeat
        * np.exp(-stab_phase / 0.08)
        * np.sin(2 * np.pi * 220.0 * time_axis)
    )

    # Hoehen: Hi-Hat auf Achteln
    eighth = (time_axis - spec.first_beat_s) % (beat_s / 2)
    rng = np.random.default_rng(abs(hash(spec.track_id)) % (2**32))
    noise = rng.normal(0.0, 1.0, frames)
    signal += 0.22 * gain_high * noise * np.exp(-eighth / 0.012)

    peak = float(np.max(np.abs(signal)))
    if peak > 0:
        signal = signal / (peak * 1.08)

    stereo = np.stack([signal, signal], axis=1).astype(np.float32)
    return AudioBuffer(np.ascontiguousarray(stereo), sample_rate)


# --------------------------------------------------------------------------
# Provider
# --------------------------------------------------------------------------


@dataclass
class DemoTrack:
    """Ein fertiger Demo-Track: Anzeigedaten und optional Audio."""

    info: TrackInfo
    spec: DemoTrackSpec
    _buffer: AudioBuffer | None = field(default=None, repr=False)
    _sample_rate: int = ENGINE_SAMPLE_RATE

    @property
    def samples(self) -> np.ndarray:
        """Audiosamples. Werden beim ersten Zugriff erzeugt und behalten."""
        if self._buffer is None:
            self._buffer = build_audio(self.spec, self._sample_rate)
        return self._buffer.samples

    @property
    def has_audio(self) -> bool:
        return self._buffer is not None


class DemoTrackProvider:
    """Quelle der Demo-Tracks.

    Spaeter durch eine echte Library ersetzbar: der Browser und die
    Verdrahtung brauchen nur ``tracks()`` und ``load(track_id)``.
    """

    def __init__(
        self,
        specs: tuple[DemoTrackSpec, ...] = SPECS,
        *,
        sample_rate: int = ENGINE_SAMPLE_RATE,
        with_audio: bool = True,
    ) -> None:
        self.sample_rate = sample_rate
        self.with_audio = with_audio
        self._tracks: dict[str, DemoTrack] = {}
        for spec in specs:
            self._tracks[spec.track_id] = DemoTrack(
                info=build_track_info(spec),
                spec=spec,
                _sample_rate=sample_rate,
            )

    # ------------------------------------------------------------------

    def tracks(self) -> tuple[DemoTrack, ...]:
        return tuple(self._tracks.values())

    def infos(self) -> tuple[TrackInfo, ...]:
        return tuple(track.info for track in self._tracks.values())

    def get(self, track_id: str) -> DemoTrack | None:
        return self._tracks.get(track_id)

    def load(self, track_id: str) -> tuple[TrackInfo, np.ndarray | None]:
        """Trackdaten und - falls gewuenscht - Samples liefern."""
        track = self._tracks.get(track_id)
        if track is None:
            raise KeyError(f"Unbekannter Demo-Track {track_id!r}")
        samples = track.samples if self.with_audio else None
        return (track.info, samples)

    def by_index(self, index: int) -> DemoTrack | None:
        items = self.tracks()
        if not items:
            return None
        return items[index % len(items)]

    def index_of(self, track_id: str) -> int:
        for number, track in enumerate(self.tracks()):
            if track.info.track_id == track_id:
                return number
        return -1
