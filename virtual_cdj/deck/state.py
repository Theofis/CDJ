"""Deck-Datenmodell.

Der Zustand, den die CDJ-Oberflaeche anzeigt. Die Oberflaeche haelt keine
eigene parallele Wahrheit - sie liest ausschliesslich diese Objekte.

Alle Objekte sind unveraenderlich. Die Deck-Engine erzeugt bei jeder Aenderung
einen neuen ``DeckState`` mit erhoehter ``generation``, damit die Oberflaeche
ueberspringen kann, was sich nicht geaendert hat.

Dieses Modul enthaelt kein Audio, keine Analyse und keine Tk-Abhaengigkeit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import Enum

from .slip import SlipState


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class PlayState(str, Enum):
    """Transportzustand des Decks."""

    EMPTY = "EMPTY"  # kein Track geladen
    STOPPED = "STOPPED"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    CUEING = "CUEING"  # Cue gehalten, spielt ab Cue-Punkt


class JogMode(str, Enum):
    VINYL = "VINYL"
    CDJ = "CDJ"


class PlayMode(str, Enum):
    """Wiedergabemodus am Trackende, wie ``PLAY MODE`` am Geraet."""

    SINGLE = "SINGLE"
    CONTINUE = "CONTINUE"


class PadMode(str, Enum):
    """Belegung der Performance-Pads. Wird von den Modus-Tastern gesetzt."""

    HOT_CUE = "HOT_CUE"
    BEAT_LOOP = "BEAT_LOOP"
    BEAT_JUMP = "BEAT_JUMP"


class LoopExitReason(str, Enum):
    """Warum ein laufender Loop beendet wurde.

    Es gibt **keinen** anonymen Weg aus einem Loop: ``LoopEngine.exit_loop``
    verlangt einen Grund, und der landet hier. Damit ist jeder Wechsel von
    ``active = True`` auf ``False`` nachvollziehbar - im Log und in der
    Entwicklungsanzeige.

    Ein Loop endet **nur** hierdurch. Nicht durch das Erreichen von
    ``out_s``, nicht durch Loslassen einer Taste, nicht durch Jog, Search,
    Quantize, Tempo, ein Beatgrid-Update oder einen Bildaufbau.
    """

    #: RELOOP/EXIT gedrueckt - die eine vorgesehene Benutzeraktion.
    RELOOP_EXIT = "RELOOP_EXIT"
    #: Dasselbe Beatloop-Pad erneut gedrueckt, das den Loop gesetzt hat.
    BEAT_LOOP_PAD = "BEAT_LOOP_PAD"
    #: Ausdruecklicher Sprung nach draussen: SEEK, CUE oder ein Hotcue.
    JUMPED_OUT = "JUMPED_OUT"
    #: Neuer Track oder Auswurf.
    TRACK_CHANGED = "TRACK_CHANGED"


class LoopAdjust(str, Enum):
    """Welcher Loop-Punkt gerade am Jogwheel haengt.

    Eigenstaendig gegenueber ``LoopState.active`` und ``PadMode``: ein Loop
    kann laufen, ohne dass ein Punkt angepasst wird, und die Pads koennen im
    Beatloop-Modus sein, ohne dass ein Loop laeuft.
    """

    NONE = "NONE"
    IN = "IN"
    OUT = "OUT"


class Direction(str, Enum):
    FWD = "FWD"
    REV = "REV"
    SLIP_REV = "SLIP_REV"


class AudioStatus(str, Enum):
    """Zustand der Audio-Ausgabe."""

    #: Es existiert noch keine Audio-Engine. Ehrlicher Dauerzustand, solange
    #: das Backend fehlt - die Oberflaeche zeigt das im Debug-Overlay an.
    NO_BACKEND = "NO_BACKEND"
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    ERROR = "ERROR"


class CueKind(str, Enum):
    CUE = "CUE"
    LOOP = "LOOP"


#: Beschriftungen der acht Performance-Pads.
PAD_LABELS: tuple[str, ...] = ("A", "B", "C", "D", "E", "F", "G", "H")

#: Auswaehlbare Tempobereiche in Prozent. ``None`` = WIDE.
TEMPO_RANGES: tuple[float | None, ...] = (6.0, 10.0, 16.0, None)

#: Waehlbare Beat-Loop-Laengen in Beats.
BEAT_LOOP_LENGTHS: tuple[float, ...] = (
    0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0,
)

#: Waehlbare Sprungweiten fuer BEAT JUMP in Beats - die Werte des
#: CDJ-3000 (``BEAT JUMP BEAT VALUE``, Handbuch S. 78), aufsteigend.
BEAT_JUMP_BEAT_VALUES: tuple[float, ...] = (
    0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0,
)


# --------------------------------------------------------------------------
# Trackbestandteile
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HotCue:
    """Ein Hotcue aus den gespeicherten Cue-Daten des Tracks."""

    index: int  # 0..7 entspricht A..H
    position_s: float
    color: str  # Hex-Farbe aus den Cue-Daten
    kind: CueKind = CueKind.CUE
    #: Nur bei ``kind == LOOP`` gesetzt.
    loop_end_s: float | None = None
    comment: str = ""

    @property
    def label(self) -> str:
        return PAD_LABELS[self.index] if 0 <= self.index < 8 else "?"


@dataclass(frozen=True)
class MemoryCue:
    """Gespeicherter Cue- oder Loop-Punkt (nicht auf ein Pad gelegt)."""

    position_s: float
    kind: CueKind = CueKind.CUE
    loop_end_s: float | None = None
    comment: str = ""


@dataclass(frozen=True)
class BeatGrid:
    """Beatgrid eines Tracks.

    Zwei Formen werden unterstuetzt: konstantes Tempo ueber
    ``first_beat_s`` + ``bpm``, oder eine explizite Beatliste fuer Tracks mit
    wechselndem Tempo. Die Beatliste hat Vorrang, wenn sie gefuellt ist.
    """

    first_beat_s: float = 0.0
    bpm: float = 0.0
    beats_per_bar: int = 4
    #: Optional: explizite Beatzeiten in Sekunden, aufsteigend.
    beats_s: tuple[float, ...] = ()
    #: Index des Beats in ``beats_s``, der Beat 1 von Takt 1 ist.
    first_downbeat_index: int = 0

    @property
    def beat_interval_s(self) -> float:
        return 60.0 / self.bpm if self.bpm > 0 else 0.0

    @property
    def is_valid(self) -> bool:
        return bool(self.beats_s) or self.bpm > 0

    def beat_number_at(self, position_s: float) -> int:
        """Fortlaufende Beatnummer ab 0 an dieser Position.

        Rueckgabe kann negativ sein, wenn die Position vor dem ersten Beat
        liegt. ``-1`` bedeutet "kein gueltiges Beatgrid".
        """
        if self.beats_s:
            index = _bisect_right(self.beats_s, position_s) - 1
            return index
        if self.bpm <= 0:
            return -1
        return int((position_s - self.first_beat_s) // self.beat_interval_s)

    def beat_time(self, beat_number: int) -> float | None:
        """Zeitpunkt eines Beats. ``None`` wenn ausserhalb des Grids."""
        if self.beats_s:
            if 0 <= beat_number < len(self.beats_s):
                return self.beats_s[beat_number]
            return None
        if self.bpm <= 0:
            return None
        return self.first_beat_s + beat_number * self.beat_interval_s

    def phase_at(self, position_s: float) -> float:
        """Position innerhalb des aktuellen Beats, 0.0 - 1.0."""
        number = self.beat_number_at(position_s)
        if number < 0:
            return 0.0
        start = self.beat_time(number)
        nxt = self.beat_time(number + 1)
        if start is None:
            return 0.0
        if nxt is None:
            interval = self.beat_interval_s
            if interval <= 0:
                return 0.0
            nxt = start + interval
        span = nxt - start
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (position_s - start) / span))

    def bar_and_beat_at(self, position_s: float) -> tuple[int, int]:
        """Takt und Beat im Takt. ``(0, 0)`` = kein gueltiges Grid.

        Takt und Beat zaehlen ab 1, so wie ein DJ zaehlt.
        """
        number = self.beat_number_at(position_s)
        if number < 0:
            return (0, 0)
        offset = number - self.first_downbeat_index
        per_bar = max(1, self.beats_per_bar)
        bar = offset // per_bar + 1
        beat = offset % per_bar + 1
        return (bar, beat)

    def snap(self, position_s: float) -> float:
        """Naechstgelegene Beatposition (fuer Quantisierung)."""
        number = self.beat_number_at(position_s)
        if number < 0:
            return position_s
        current = self.beat_time(number)
        nxt = self.beat_time(number + 1)
        if current is None:
            return position_s
        if nxt is None:
            return current
        return current if (position_s - current) < (nxt - position_s) else nxt

    def seconds_for_beats(self, beats: float, at_s: float = 0.0) -> float:
        """Laenge von ``beats`` Beats in Sekunden ab ``at_s``."""
        if self.beats_s:
            number = self.beat_number_at(at_s)
            start = self.beat_time(max(0, number))
            end = self.beat_time(max(0, number) + int(round(beats)))
            if start is not None and end is not None:
                return end - start
        return beats * self.beat_interval_s

    # ------------------------------------------------------------------
    # Manuelle Korrektur
    #
    # Die automatische Analyse ist nie perfekt. Diese Methoden liefern jeweils
    # ein neues Grid; eine Bedienoberflaeche dafuer gibt es noch nicht, das
    # Datenmodell verhindert sie aber nicht.
    # ------------------------------------------------------------------

    def shifted(self, delta_s: float) -> BeatGrid:
        """Ganzes Raster um ``delta_s`` verschieben."""
        if self.beats_s:
            return replace(
                self,
                beats_s=tuple(t + delta_s for t in self.beats_s),
                first_beat_s=self.first_beat_s + delta_s,
            )
        return replace(self, first_beat_s=self.first_beat_s + delta_s)

    def with_bpm(self, bpm: float, *, anchor_s: float | None = None) -> BeatGrid:
        """Tempo aendern und dabei einen Punkt festhalten.

        ``anchor_s`` bleibt an derselben Stelle im Raster. Ohne Angabe wird
        der erste Beat festgehalten. Eine vorhandene explizite Beatliste wird
        verworfen, weil sie zum neuen Tempo nicht mehr passt.
        """
        if bpm <= 0:
            raise ValueError("BPM muss positiv sein")
        anchor = self.first_beat_s if anchor_s is None else anchor_s
        new_interval = 60.0 / bpm
        # Den Anker auf den naechsten Beat des alten Rasters legen und das
        # neue Raster von dort aus aufspannen.
        number = self.beat_number_at(anchor)
        anchor_beat = self.beat_time(number) if number >= 0 else anchor
        if anchor_beat is None:
            anchor_beat = anchor
        first = anchor_beat % new_interval
        return BeatGrid(
            first_beat_s=first,
            bpm=bpm,
            beats_per_bar=self.beats_per_bar,
            beats_s=(),
            first_downbeat_index=self.first_downbeat_index,
        )

    def with_downbeat_at(self, position_s: float) -> BeatGrid:
        """Den Beat bei ``position_s`` zum Taktanfang erklaeren."""
        number = self.beat_number_at(position_s)
        if number < 0:
            return self
        per_bar = max(1, self.beats_per_bar)
        return replace(self, first_downbeat_index=number % per_bar)

    def resegmented_from(
        self, position_s: float, bpm: float, until_s: float | None = None
    ) -> BeatGrid:
        """Ab ``position_s`` ein neues Tempo setzen, davor alles behalten.

        Ergebnis ist immer eine explizite Beatliste, weil das Grid dann kein
        konstantes Tempo mehr hat. Damit ist variables Tempo abgedeckt, ohne
        dass die uebrigen Schichten etwas davon wissen muessen.

        Args:
            position_s: Ab hier gilt das neue Tempo.
            bpm: Neues Tempo.
            until_s: Bis wohin Beats erzeugt werden - in der Regel die
                Tracklaenge. Bei einem Grid mit expliziter Beatliste wird
                sonst deren Ende verwendet.

        Raises:
            ValueError: bei ungueltigem Tempo, oder wenn ``until_s`` fehlt
                und sich kein Ende aus dem Grid ableiten laesst. Ein
                willkuerlicher Standardwert wuerde nur Fehler verdecken.
        """
        if bpm <= 0:
            raise ValueError("BPM muss positiv sein")
        if not self.is_valid:
            return self

        head: list[float] = []
        if self.beats_s:
            head = [t for t in self.beats_s if t < position_s]
            end = until_s if until_s is not None else self.beats_s[-1]
        else:
            if until_s is None:
                raise ValueError(
                    "Bei konstantem Tempo muss until_s angegeben werden - "
                    "sonst ist unbekannt, wie weit Beats zu erzeugen sind."
                )
            end = until_s
            number = 0
            while True:
                time_s = self.beat_time(number)
                if time_s is None or time_s >= position_s:
                    break
                head.append(time_s)
                number += 1

        interval = 60.0 / bpm
        start = head[-1] + interval if head else position_s
        tail: list[float] = []
        current = max(start, position_s)
        while current <= end:
            tail.append(current)
            current += interval
            if len(tail) > 200_000:  # pragma: no cover - Notbremse
                break

        beats = tuple(head + tail)
        return BeatGrid(
            first_beat_s=beats[0] if beats else self.first_beat_s,
            bpm=bpm,
            beats_per_bar=self.beats_per_bar,
            beats_s=beats,
            first_downbeat_index=self.first_downbeat_index,
        )


def _bisect_right(values: Sequence[float], target: float) -> int:
    low, high = 0, len(values)
    while low < high:
        mid = (low + high) // 2
        if target < values[mid]:
            high = mid
        else:
            low = mid + 1
    return low


@dataclass(frozen=True)
class WaveformData:
    """Vorberechnete Waveform-Peaks aus der Track-Analyse.

    Es findet hier **keine** Analyse statt. Die Oberflaeche rendert nur, was
    die Analyse geliefert hat.

    Die drei Baender entsprechen der RGB-Darstellung:
    ``low`` = rot (Bass), ``mid`` = gruen (Mitten), ``high`` = blau (Hoehen).
    Alle Werte sind auf 0.0 - 1.0 normiert. Die Sequenzen sind gleich lang;
    ein Eintrag deckt ``1 / peaks_per_second`` Sekunden ab.
    """

    peaks_per_second: float
    low: Sequence[float]
    mid: Sequence[float]
    high: Sequence[float]
    #: Breitband-Spitzenwert je Fenster - haelt Transienten.
    peak: Sequence[float] = ()
    #: Breitband-Effektivwert je Fenster - gibt der Waveform ihren Koerper.
    rms: Sequence[float] = ()

    @property
    def has_dynamics(self) -> bool:
        """Ob Spitzen- und Effektivwert vorliegen.

        Aeltere Analysen und einfache Quellen haben nur die drei Baender;
        die Darstellung faellt dann auf das lauteste Band zurueck.
        """
        return (
            len(self.peak) == len(self.low)
            and len(self.rms) == len(self.low)
            and len(self.low) > 0
        )

    @property
    def length(self) -> int:
        return len(self.low)

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
            and len(self.low) == len(self.mid) == len(self.high)
        )


@dataclass(frozen=True)
class WaveformSet:
    """Mehrere Aufloesungsstufen derselben Waveform.

    Die Analyse berechnet Uebersicht, Mittel und Detail einmalig vor. Die
    Oberflaeche waehlt daraus die passende Stufe - sie berechnet nichts und
    durchsucht nie die Audiodatei.
    """

    #: Name -> Peaks, z. B. ``overview``, ``medium``, ``detailed``.
    levels: dict[str, WaveformData] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.levels)

    def get(self, name: str) -> WaveformData | None:
        return self.levels.get(name)

    def is_consistent(self) -> bool:
        return bool(self.levels) and all(
            level.is_consistent() for level in self.levels.values()
        )

    @property
    def coarsest(self) -> WaveformData | None:
        if not self.levels:
            return None
        return min(self.levels.values(), key=lambda p: p.peaks_per_second)

    @property
    def finest(self) -> WaveformData | None:
        if not self.levels:
            return None
        return max(self.levels.values(), key=lambda p: p.peaks_per_second)

    def level_for(self, seconds_per_pixel: float) -> WaveformData | None:
        """Feinste Stufe, die noch mindestens einen Peak je Pixel liefert."""
        if not self.levels:
            return None
        if seconds_per_pixel <= 0:
            return self.finest
        needed = 1.0 / seconds_per_pixel
        ordered = sorted(self.levels.values(), key=lambda p: p.peaks_per_second)
        for level in ordered:
            if level.peaks_per_second >= needed:
                return level
        return ordered[-1]


@dataclass(frozen=True)
class TrackInfo:
    """Ein geladener Track samt vorberechneter Analyse."""

    track_id: str
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    label: str = ""
    duration_s: float = 0.0
    original_bpm: float = 0.0
    key: str = ""
    rating: int = 0
    file_path: str = ""
    artwork_path: str = ""
    source: str = ""  # z. B. "USB", "SD", "COLLECTION"
    color: str = ""

    waveform: WaveformSet | None = None
    beat_grid: BeatGrid | None = None
    hot_cues: tuple[HotCue, ...] = ()
    memory_cues: tuple[MemoryCue, ...] = ()

    #: Ergebnisse der Analyse, die nur zur Anzeige/Diagnose dienen.
    key_confidence: float = 0.0
    downbeat_confidence: float = 0.0
    analysis_version: int = 0

    def hot_cue(self, index: int) -> HotCue | None:
        for cue in self.hot_cues:
            if cue.index == index:
                return cue
        return None

    @property
    def display_title(self) -> str:
        return self.title or self.file_path or self.track_id


# --------------------------------------------------------------------------
# Deck-Zustand
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LoopState:
    """Zustand des aktuellen Loops.

    Reine Daten. Die Logik dazu steht in ``deck/loop.py``.

    ``active`` sagt ausschliesslich, ob der Loop laeuft. Ob die Pads A-H
    Beatloops ausloesen, steht in ``DeckState.pad_mode``; welcher Punkt am
    Jogwheel haengt, in ``adjust``. Die drei Zustaende sind getrennt.

    Die ``last_*``-Felder halten den zuletzt fertigen Loop - die Grundlage
    fuer RELOOP. Sie werden beim Erzeugen, beim Aendern der Laenge, am Ende
    eines Loop-Adjusts und beim Verlassen gesetzt, nicht bei jedem
    Jog-Schritt.
    """

    active: bool = False
    in_s: float | None = None
    out_s: float | None = None
    #: Laenge in Beats, sofern sie sauber im Beatgrid liegt. ``None`` bei
    #: einem von Hand gezogenen Loop, der auf keinem Beat-Vielfachen endet.
    beats: float | None = None
    #: Loop-Punkt, der gerade mit dem Jogwheel verschoben wird.
    adjust: LoopAdjust = LoopAdjust.NONE

    #: Beatloop-Pad, mit dem dieser Loop zuletzt gesetzt wurde
    #: (``Last_BeatLoop_Pad``). ``None`` heisst: dieser Loop kam **nicht**
    #: von einem Pad - dann darf ein Pad-Druck ihn auch nicht verlassen,
    #: sondern setzt seine Laenge.
    pad: int | None = None

    #: Grund der letzten Beendigung. Bleibt stehen, bis ein neuer Loop
    #: entsteht - so ist auch nachtraeglich sichtbar, warum der letzte
    #: aufgehoert hat. ``None`` heisst: dieser Loop wurde nie beendet.
    exit_reason: LoopExitReason | None = None

    #: Zuletzt gespeicherter Loop (RELOOP).
    last_in_s: float | None = None
    last_out_s: float | None = None
    last_beats: float | None = None

    @property
    def is_set(self) -> bool:
        return self.in_s is not None and self.out_s is not None

    @property
    def length_s(self) -> float:
        if not self.is_set:
            return 0.0
        return max(0.0, self.out_s - self.in_s)  # type: ignore[operator]

    @property
    def has_last(self) -> bool:
        """Ob ein Loop zum Wiederaufrufen gespeichert ist."""
        return (
            self.last_in_s is not None
            and self.last_out_s is not None
            and self.last_out_s > self.last_in_s
        )

    @property
    def pad_index(self) -> int | None:
        """Beatloop-Pad, dessen Laenge diesem Loop entspricht.

        Rein zur **Anzeige**: welches Pad leuchtet, wenn die Laenge zufaellig
        einer Pad-Laenge entspricht. Fuer die Bedienlogik ist das der
        falsche Wert - ob ein erneuter Pad-Druck den Loop verlaesst, haengt
        an ``pad`` (``Last_BeatLoop_Pad``), also daran, ob der Loop wirklich
        von diesem Pad kam. Ein mit CALL < erzeugter 4-Beat-Loop hat
        dieselbe Laenge wie Pad E, ist aber nicht von Pad E.
        """
        if self.beats is None:
            return None
        for index, length in enumerate(BEAT_LOOP_LENGTHS):
            if abs(self.beats - length) < 1e-9:
                return index
        return None

    def label(self) -> str:
        """Anzeigetext, z. B. ``1/4`` oder ``16``."""
        if self.beats is None:
            return ""
        if self.beats >= 1:
            return f"{self.beats:g}"
        return f"1/{int(round(1 / self.beats))}"


@dataclass(frozen=True)
class DeckState:
    """Vollstaendiger Zustand eines Decks - die einzige Wahrheit fuer die GUI."""

    deck_id: int

    #: Aktives Betriebs-Backend. Leer bei einer nackten ``Deck``-Instanz,
    #: ``MIDI`` bzw. ``CDJ`` sobald sie von einem Backend verwaltet wird.
    #: Als Text gehalten, damit der reine Deck-Zustand den ModeManager nicht
    #: importieren muss.
    operating_mode: str = ""
    #: Lebenszyklus-/Verbindungszustand des aktiven Backends. Die Mock-
    #: Backends melden ``MOCK_READY``; spaeter koennen hier z. B.
    #: ``CONNECTING`` oder ``CONNECTED`` stehen.
    connection_state: str = "DISCONNECTED"

    track: TrackInfo | None = None
    play_state: PlayState = PlayState.EMPTY

    position_s: float = 0.0
    duration_s: float = 0.0

    original_bpm: float = 0.0
    current_bpm: float = 0.0
    tempo_percent: float = 0.0
    tempo_range: float | None = 10.0
    master_tempo: bool = False
    tempo_reset: bool = False

    #: Beat im Takt (1..4) und Taktnummer. ``0`` = kein gueltiges Beatgrid.
    beat: int = 0
    bar: int = 0
    beat_phase: float = 0.0

    is_master: bool = False
    sync: bool = False
    #: QUANTIZE-Schalter. Rastert **neue** musikalische Punkte auf das
    #: Beatgrid - Cue, Hotcue, Loop In/Out, Beatloop. Er zieht **nicht** die
    #: Wiedergabeposition auf das Raster.
    quantize: bool = False
    #: SLIP-Schalter (``slipEnabled``). Dauerhafte Einstellung, nicht der
    #: Zustand einer laufenden Slip-Aktion - der steht in ``slip_state``.
    slip: bool = False
    #: Laufende Slip-Aktion samt Hintergrundposition. Logik in
    #: ``deck/slip.py``.
    slip_state: SlipState = field(default_factory=SlipState)

    loop: LoopState = field(default_factory=LoopState)
    cue_point_s: float | None = None

    jog_touch: bool = False
    jog_mode: JogMode = JogMode.VINYL
    direction: Direction = Direction.FWD
    pad_mode: PadMode = PadMode.HOT_CUE
    beat_jump_beats: float = 16.0
    vinyl_speed_adjust: float = 0.5

    key: str = ""
    #: Tonartverschiebung in Halbtoenen. Der Zustand wird gefuehrt und
    #: angezeigt; die Tonhoehenverschiebung im Audio fehlt noch (Kategorie B,
    #: siehe docs/CDJ3000_DISPLAY_ANALYSIS.md).
    key_shift: int = 0

    #: Anzeigefelder des CDJ-Displays.
    player_number: int = 0
    track_number: int = 0
    play_mode: PlayMode = PlayMode.SINGLE
    #: Rasterweite der Quantisierung in Beats (``quantizeBeatValue``):
    #: 0.125, 0.25, 0.5 oder 1.0 - siehe ``deck/quantize.py``.
    quantize_beats: float = 1.0
    hot_cue_auto_load: bool = False

    audio_status: AudioStatus = AudioStatus.NO_BACKEND

    #: Steigt bei jeder Zustandsaenderung. Die GUI kann damit Redraws sparen.
    generation: int = 0

    # -- abgeleitete Werte ------------------------------------------------

    @property
    def has_track(self) -> bool:
        return self.track is not None

    @property
    def is_playing(self) -> bool:
        return self.play_state in (PlayState.PLAYING, PlayState.CUEING)

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.duration_s - self.position_s)

    @property
    def progress(self) -> float:
        if self.duration_s <= 0:
            return 0.0
        return max(0.0, min(1.0, self.position_s / self.duration_s))

    @property
    def has_waveform(self) -> bool:
        return (
            self.track is not None
            and self.track.waveform is not None
            and self.track.waveform.is_consistent()
        )

    @property
    def displayed_key(self) -> str:
        """Tonart inklusive Verschiebung.

        Solange kein Pitch-Shifting im Audio existiert, ist das die
        **angekuendigte** Tonart. Der Rohwert steht in ``key``.
        """
        if not self.key or self.key_shift == 0:
            return self.key
        return f"{self.key} {self.key_shift:+d}"

    @property
    def slip_active(self) -> bool:
        """Ob gerade eine Slip-Aktion laeuft (``slipOperationActive``).

        Ausdruecklich **nicht** dasselbe wie ``slip``: der Schalter kann an
        sein, ohne dass etwas laeuft. LED und Anzeige unterscheiden beides.
        """
        return self.slip_state.active

    @property
    def slip_position_s(self) -> float | None:
        """Hintergrundposition der laufenden Slip-Aktion.

        ``None`` heisst: es laeuft keine, oder die Zeitachse wurde beim
        Trackwechsel verworfen.
        """
        return self.slip_state.position_s

    @property
    def has_beat_grid(self) -> bool:
        return (
            self.track is not None
            and self.track.beat_grid is not None
            and self.track.beat_grid.is_valid
        )

    def with_changes(self, **changes: object) -> DeckState:
        """Neuen Zustand mit erhoehter ``generation`` erzeugen."""
        changes.setdefault("generation", self.generation + 1)
        return replace(self, **changes)  # type: ignore[arg-type]


def empty_state(deck_id: int) -> DeckState:
    """Zustand eines Decks ohne Track. Der ehrliche Startzustand."""
    return DeckState(deck_id=deck_id)
