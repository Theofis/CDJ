"""Audio-Engine: Ausgabe und Stimmen.

Aufbau
------
``AudioEngine`` haelt einen PortAudio-Ausgabestream und je Deck eine
``DeckVoice``. Der Callback summiert die Stimmen in den Ausgabepuffer.

Echtzeitregeln (Abschnitt 8 der Vorgabe)
----------------------------------------
Der Callback

* laedt keine Dateien,
* berechnet keine Waveform und keine Analyse,
* alloziert keine grossen Puffer,
* aktualisiert keine GUI,
* wartet an keinem Lock.

Steuerbefehle kommen ueber eine ``SimpleQueue``, die der Callback ohne
Blockieren leert. Die Position schreibt der Callback in ein einfaches
Attribut; der GUI-Thread liest es.

Testbarkeit
-----------
``render_block`` enthaelt die gesamte Mischlogik und laeuft ohne PortAudio.
Die Tests rufen sie direkt auf und sind damit deterministisch.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from .format import CHANNELS, DTYPE, ENGINE_SAMPLE_RATE
from .metrics import AudioMetrics
from .tempo_proc import SimpleResamplerTempoProcessor, TempoProcessor

#: Standard-Blockgroesse. 512 Frames bei 44.1 kHz sind 11.6 ms.
DEFAULT_BLOCK_FRAMES = 512

#: Sicherheitsgrenze fuer Loop-Umlaeufe innerhalb eines Blocks.
MAX_LOOP_WRAPS_PER_BLOCK = 64

#: Kuerzeste Loop-Laenge in Samples, die die Ausgabe umlaufen kann.
#:
#: Ein kuerzerer Loop wird **auf diese Laenge gestreckt**, nicht abgeschaltet.
#: Frueher schaltete die Ausgabe ihn still ab: ``DeckState.loop.active``
#: blieb ``True``, die Oberflaeche zeigte weiter einen Loop, und die
#: Wiedergabe lief einfach hindurch. Das sah aus wie ein Loop, der von
#: selbst aufhoert. Die Ausgabe entscheidet nicht mehr ueber den
#: Loop-Zustand - das tut allein die Deck-Engine.
MIN_LOOP_FRAMES = 32


class VoiceCommand:
    """Basis der Befehle an eine Stimme. Unveraenderlich."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class _Load(VoiceCommand):
    samples: np.ndarray


@dataclass(frozen=True, slots=True)
class _SetPlaying(VoiceCommand):
    playing: bool


@dataclass(frozen=True, slots=True)
class _Seek(VoiceCommand):
    frame: float


@dataclass(frozen=True, slots=True)
class _Nudge(VoiceCommand):
    frames: float


@dataclass(frozen=True, slots=True)
class _SetSpeed(VoiceCommand):
    speed: float


@dataclass(frozen=True, slots=True)
class _SetLoop(VoiceCommand):
    active: bool
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _SetGain(VoiceCommand):
    gain: float


class DeckVoice:
    """Eine abspielende Stimme - ein Deck.

    Die oeffentlichen Methoden werden vom Steuer-Thread aufgerufen und legen
    nur einen Befehl in die Queue. Alles, was den Zustand aendert, passiert
    im Audio-Thread in ``render``.
    """

    def __init__(
        self,
        deck_id: int,
        sample_rate: int = ENGINE_SAMPLE_RATE,
        processor: TempoProcessor | None = None,
    ) -> None:
        self.deck_id = deck_id
        self.sample_rate = sample_rate
        self.processor = processor or SimpleResamplerTempoProcessor()
        self._commands: queue.SimpleQueue[VoiceCommand] = queue.SimpleQueue()

        # -- Zustand, nur im Audio-Thread veraendert -----------------------
        self._samples: np.ndarray | None = None
        self._total_frames = 0
        self._position = 0.0
        self._playing = False
        self._speed = 1.0
        self._gain = 1.0
        self._loop_active = False
        self._loop_start = 0
        self._loop_end = 0

        # -- vom Audio-Thread geschrieben, vom GUI-Thread gelesen ----------
        self.position_frames = 0.0
        self.reached_end = False
        self.loop_wraps = 0

        # -- vom Steuer-Thread gefuehrt ------------------------------------
        # ``is_ready`` muss sofort nach ``load`` stimmen und darf nicht auf
        # den ersten Audio-Block warten.
        self._loaded_frames = 0
        self._loaded_bytes = 0
        # Was der Steuer-Thread zuletzt als Loop geschickt hat. Nur seine
        # eigene Sicht - der Audio-Thread fuehrt seinen Loop getrennt.
        self._view_loop_active = False
        self._view_loop_start = 0
        self._view_loop_end = 0

    # ------------------------------------------------------------------
    # Steuer-Thread
    # ------------------------------------------------------------------

    def load(self, samples: np.ndarray) -> None:
        """Track uebernehmen. ``samples`` muss ``(n, 2)`` float32 sein."""
        array = np.ascontiguousarray(samples, dtype=DTYPE)
        if array.ndim != 2 or array.shape[1] != CHANNELS:
            raise ValueError(f"Erwartet (n, {CHANNELS}), erhalten {array.shape}")
        self._loaded_frames = int(array.shape[0])
        self._loaded_bytes = int(array.nbytes)
        self.position_frames = 0.0
        self.reached_end = False
        # Ein neuer Track hat keinen Loop - der Audio-Thread setzt ihn bei
        # ``_Load`` ebenfalls zurueck. Beide Sichten bleiben so einig.
        self._set_loop_view(False, 0, 0)
        self._commands.put(_Load(array))

    def unload(self) -> None:
        self._loaded_frames = 0
        self._loaded_bytes = 0
        self._set_loop_view(False, 0, 0)
        self._commands.put(_Load(np.zeros((0, CHANNELS), dtype=DTYPE)))

    def set_playing(self, playing: bool) -> None:
        self._commands.put(_SetPlaying(bool(playing)))

    def seek_seconds(self, seconds: float) -> None:
        frame = float(seconds) * self.sample_rate
        # Position sofort mitschreiben. Sonst liest der Steuer-Thread bis zum
        # naechsten Audio-Block noch den alten Wert; der Audio-Thread setzt
        # danach denselben Wert, beide bleiben also einig.
        self.position_frames = _clamp(
            frame, 0.0, max(0.0, self._loaded_frames - 1.0)
        )
        self._commands.put(_Seek(frame))

    def nudge_seconds(self, seconds: float) -> None:
        """Position relativ verschieben - fuer Jog, Backspin und Pitch Bend.

        Ein aktiver Loop gilt auch hier: es wird umgelaufen statt hinaus-
        zulaufen, in beide Richtungen. Sonst traegt ein Backspin die
        Wiedergabe aus dem Loop heraus, obwohl niemand ihn verlassen hat.
        """
        frames = float(seconds) * self.sample_rate
        self.position_frames = self._contained(self.position_frames + frames)
        self._commands.put(_Nudge(frames))

    def set_speed(self, speed: float) -> None:
        self._commands.put(_SetSpeed(float(speed)))

    def set_loop(
        self, active: bool, start_s: float | None, end_s: float | None
    ) -> None:
        if start_s is None or end_s is None:
            self._set_loop_view(False, 0, 0)
            self._commands.put(_SetLoop(False, 0, 0))
            return
        start = int(round(start_s * self.sample_rate))
        end = int(round(end_s * self.sample_rate))
        # Zu kurze Loops werden gestreckt, nicht verworfen: die Ausgabe
        # darf einen vom Deck gesetzten Loop nicht stillschweigend
        # abschalten - sonst weichen Anzeige und Ton voneinander ab.
        end = max(end, start + MIN_LOOP_FRAMES)
        self._set_loop_view(bool(active), start, end)
        self._commands.put(_SetLoop(bool(active), start, end))

    # -- Sicht des Steuer-Threads auf den Loop -----------------------------
    #
    # ``_loop_active``/``_loop_start``/``_loop_end`` gehoeren dem Audio-
    # Thread und duerfen von hier nicht gelesen werden. Der Steuer-Thread
    # setzt den Loop aber selbst - er darf sich also merken, was er
    # geschickt hat, und ``nudge_seconds`` sofort danach richten. Sonst
    # zeigte die Oberflaeche bis zum naechsten Audioblock eine Position
    # ausserhalb des Loops.

    def _set_loop_view(self, active: bool, start: int, end: int) -> None:
        self._view_loop_active = active
        self._view_loop_start = start
        self._view_loop_end = end

    def _contained(self, position: float) -> float:
        """Position in ihre Grenzen zwingen - Loop vor Trackrand.

        Dieselbe modulare Rechnung wie ``LoopEngine.check_boundary`` und
        ``_wrap_at_boundary``: der Loop ist ein Ring, vorwaerts wie
        rueckwaerts.
        """
        if self._view_loop_active:
            length = float(self._view_loop_end - self._view_loop_start)
            if length > 0:
                return (
                    self._view_loop_start
                    + (position - self._view_loop_start) % length
                )
        return _clamp(position, 0.0, max(0.0, self._loaded_frames - 1.0))

    def set_gain(self, gain: float) -> None:
        self._commands.put(_SetGain(float(gain)))

    # -- Rueckmeldung -----------------------------------------------------

    @property
    def position_seconds(self) -> float:
        return self.position_frames / self.sample_rate

    @property
    def is_ready(self) -> bool:
        """Ob ein Track geladen ist - aus Sicht des Steuer-Threads."""
        return self._loaded_frames > 0

    @property
    def duration_seconds(self) -> float:
        return self._loaded_frames / self.sample_rate

    @property
    def loaded_bytes(self) -> int:
        return self._loaded_bytes

    # ------------------------------------------------------------------
    # Audio-Thread
    # ------------------------------------------------------------------

    def _drain_commands(self) -> None:
        """Befehle uebernehmen. ``get_nowait`` blockiert nicht."""
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            if isinstance(command, _Load):
                self._samples = command.samples
                self._total_frames = int(command.samples.shape[0])
                self._position = 0.0
                self._playing = False
                self._loop_active = False
                self.reached_end = False
            elif isinstance(command, _SetPlaying):
                if command.playing and self._total_frames == 0:
                    continue
                self._playing = command.playing
                if command.playing:
                    self.reached_end = False
            elif isinstance(command, _Seek):
                self._position = _clamp(
                    command.frame, 0.0, max(0.0, self._total_frames - 1.0)
                )
                self.reached_end = False
            elif isinstance(command, _Nudge):
                # Jog und Backspin bleiben im Loop, statt ihn zu verlassen.
                self._position = self._wrap_into_loop(
                    self._position + command.frames
                )
            elif isinstance(command, _SetSpeed):
                self._speed = command.speed
            elif isinstance(command, _SetLoop):
                self._loop_active = command.active
                self._loop_start = command.start
                self._loop_end = command.end
            elif isinstance(command, _SetGain):
                self._gain = command.gain

    def render(self, out: np.ndarray, frames: int) -> bool:
        """``frames`` Samples nach ``out`` **addieren**.

        Returns:
            Ob die Stimme Audio geliefert hat.
        """
        self._drain_commands()

        source = self._samples
        if source is None or self._total_frames < 2 or not self._playing:
            self.position_frames = self._position
            return False
        if self._speed == 0.0:
            self.position_frames = self._position
            return False

        scratch = _scratch_buffer(frames)
        produced = 0
        wraps = 0

        while produced < frames and wraps < MAX_LOOP_WRAPS_PER_BLOCK:
            # Steht die Position schon auf oder hinter der Grenze, wird
            # zuerst umgelaufen. Das passiert, wenn Loop Out waehrend des
            # Laufs hinter den Playhead gezogen wurde.
            if self._at_or_past_boundary():
                if not self._wrap_at_boundary():
                    break
                wraps += 1
                continue

            # Mindestens ein Sample je Durchgang. Bleibt bis zur Grenze
            # weniger als ein ganzes Sample, liefert die Rechnung 0 - dann
            # kaeme die Position nie an der Grenze an, und der Umlauf liefe
            # mit negativem Ueberhang auf der Stelle. Genau so blieb die
            # Wiedergabe frueher exakt auf Loop Out stehen.
            available = max(1, self._frames_until_boundary())
            chunk = min(frames - produced, available)
            view = scratch[produced:produced + chunk]
            self._position = self.processor.render(
                source, self._position, chunk, self._speed, view
            )
            produced += chunk

            # Noch im selben Block zurueckfalten, damit nach aussen nie eine
            # Position auf oder hinter Loop Out sichtbar wird.
            if self._at_or_past_boundary():
                if not self._wrap_at_boundary():
                    break
                wraps += 1

        if produced == 0:
            self.position_frames = self._position
            return False

        block = scratch[:produced]
        if self._gain != 1.0:
            np.multiply(block, self._gain, out=block)
        np.add(out[:produced], block, out=out[:produced])

        self.position_frames = self._position
        self.loop_wraps += wraps
        return True

    # -- Grenzen ----------------------------------------------------------

    def _wrap_into_loop(self, position: float) -> float:
        """Im Audio-Thread: Position in den aktiven Loop zurueckfalten.

        Gegenstueck zu ``_contained`` auf der Steuerseite - dieselbe
        Rechnung, nur auf dem Loop, den der Audio-Thread wirklich fuehrt.
        """
        if self._loop_active:
            length = float(self._loop_end - self._loop_start)
            if length > 0:
                return (
                    self._loop_start
                    + (position - self._loop_start) % length
                )
        return _clamp(position, 0.0, max(0.0, self._total_frames - 1.0))

    def _boundary(self) -> float:
        """Naechste Grenze in Quell-Samples, in Laufrichtung."""
        if self._loop_active:
            return (
                float(self._loop_end) if self._speed > 0
                else float(self._loop_start)
            )
        return float(self._total_frames - 1) if self._speed > 0 else 0.0

    def _frames_until_boundary(self) -> int:
        """Wie viele Ausgangssamples bis zur Grenze passen."""
        distance = (self._boundary() - self._position) / self._speed
        if distance <= 0:
            return 0
        return int(distance)

    def _at_or_past_boundary(self) -> bool:
        if self._speed > 0:
            return self._position >= self._boundary()
        return self._position <= self._boundary()

    def _wrap_at_boundary(self) -> bool:
        """Loop schliessen oder Wiedergabe beenden.

        Wird **nur** gerufen, wenn ``_at_or_past_boundary()`` gilt. Damit ist
        der Ueberhang nie negativ. Ohne diese Bedingung bildet das Modulo
        einen negativen Ueberhang auf dieselbe Position zurueck, und die
        Stimme steht still.

        Returns:
            Ob weiter Audio geliefert werden kann.
        """
        if self._loop_active:
            # Nie abschalten, nur begrenzen. Der Loop-Zustand gehoert der
            # Deck-Engine; hier wird ausschliesslich umgelaufen.
            length = max(
                float(self._loop_end - self._loop_start),
                float(MIN_LOOP_FRAMES),
            )
            if self._speed > 0:
                # Ueberhang exakt uebertragen - keine Timingdrift.
                overshoot = self._position - self._loop_end
                self._position = self._loop_start + (overshoot % length)
            else:
                overshoot = self._loop_start - self._position
                self._position = self._loop_end - (overshoot % length)
            return True

        if self._speed > 0:
            self._position = float(max(0, self._total_frames - 1))
            self._playing = False
            self.reached_end = True
        else:
            self._position = 0.0
            self._playing = False
        return False


# --------------------------------------------------------------------------
# Arbeitspuffer
# --------------------------------------------------------------------------

_SCRATCH: dict[int, np.ndarray] = {}


def _scratch_buffer(frames: int) -> np.ndarray:
    """Wiederverwendeter Mischpuffer je Thread-unabhaengiger Groesse.

    Wird beim ersten Block einer Groesse angelegt und danach nur noch
    wiederverwendet - im Dauerbetrieb also allokationsfrei.
    """
    buffer = _SCRATCH.get(frames)
    if buffer is None:
        buffer = np.zeros((frames, CHANNELS), dtype=DTYPE)
        _SCRATCH[frames] = buffer
    return buffer


def _clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------


class AudioEngine:
    """Ausgabestream und Stimmenverwaltung."""

    def __init__(
        self,
        sample_rate: int = ENGINE_SAMPLE_RATE,
        block_frames: int = DEFAULT_BLOCK_FRAMES,
        device: int | str | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.block_frames = block_frames
        self.device = device
        self.voices: dict[int, DeckVoice] = {}
        self.metrics = AudioMetrics(
            sample_rate=sample_rate, block_frames=block_frames
        )
        self._stream: Any = None
        self._lock = threading.Lock()
        self.last_error: str = ""

    # ------------------------------------------------------------------

    def voice(self, deck_id: int) -> DeckVoice:
        """Stimme holen oder anlegen."""
        with self._lock:
            existing = self.voices.get(deck_id)
            if existing is not None:
                return existing
            created = DeckVoice(
                deck_id,
                self.sample_rate,
                SimpleResamplerTempoProcessor(max_frames=self.block_frames * 4),
            )
            self.voices[deck_id] = created
            return created

    def remove_voice(self, deck_id: int) -> None:
        with self._lock:
            self.voices.pop(deck_id, None)

    # ------------------------------------------------------------------

    def render_block(self, frames: int, out: np.ndarray | None = None) -> np.ndarray:
        """Einen Block mischen. Identisch zum Callback, aber ohne PortAudio."""
        started = time.perf_counter()
        if out is None:
            out = np.zeros((frames, CHANNELS), dtype=DTYPE)
        else:
            out[:frames] = 0.0

        active = 0
        # ``list`` auf dict_values ist billig und vermeidet, dass eine
        # gleichzeitige Aenderung den Callback stoert.
        for voice in list(self.voices.values()):
            if voice.render(out, frames):
                active += 1

        np.clip(out[:frames], -1.0, 1.0, out=out[:frames])

        self.metrics.active_voices = active
        self.metrics.note_callback(
            (time.perf_counter() - started) * 1000.0, frames
        )
        return out

    def _callback(self, outdata, frames, time_info, status) -> None:
        if status:
            if getattr(status, "output_underflow", False):
                self.metrics.underruns += 1
            if getattr(status, "output_overflow", False):
                self.metrics.overruns += 1
        self.render_block(frames, outdata)

    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._stream is not None and getattr(
            self._stream, "active", False
        )

    def start(self) -> bool:
        """Stream oeffnen. ``False``, wenn kein Ausgabegeraet verfuegbar ist."""
        if self._stream is not None:
            return True
        try:
            import sounddevice as sd

            self._stream = sd.OutputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_frames,
                channels=CHANNELS,
                dtype="float32",
                device=self.device,
                callback=self._callback,
            )
            self._stream.start()
            self.metrics.sample_rate = int(self._stream.samplerate)
            self.metrics.block_frames = self.block_frames
            self.last_error = ""
            return True
        except Exception as error:
            self._stream = None
            self.last_error = str(error)
            return False

    def stop(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:  # pragma: no cover - Geraet bereits weg
            pass

    def close(self) -> None:
        self.stop()
        with self._lock:
            self.voices.clear()

    # ------------------------------------------------------------------

    def memory_mb(self) -> float:
        """Ungefaehrer Speicherbedarf der geladenen Tracks."""
        total = sum(voice.loaded_bytes for voice in list(self.voices.values()))
        value = total / (1024 * 1024)
        self.metrics.memory_mb = round(value, 1)
        return value
