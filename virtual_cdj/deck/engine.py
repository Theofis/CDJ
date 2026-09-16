"""Deck-Engine: Transport und Zustand eines Decks.

Was diese Engine ist
--------------------
Der Besitzer des ``DeckState``. Sie fuehrt Position, Tempo, Beat/Takt, Loop,
Cue, Hotcues und die Sync-Flags. Alle Werte werden aus echten Daten berechnet:
die Position aus einer echten Uhr, Beat und Takt aus dem Beatgrid des
geladenen Tracks.

Was diese Engine **nicht** ist
------------------------------
Kein Audio-Player. Sie dekodiert nichts, gibt nichts aus und analysiert nichts.
``DeckState.audio_status`` meldet deshalb ``NO_BACKEND``, solange keine
Audio-Engine existiert.

Sie erfindet auch keine Trackdaten. Ohne geladenen Track bleibt der Zustand
leer, ohne Beatgrid bleiben Beat und Takt auf ``0``, ohne Waveform-Analyse
bleibt ``track.waveform`` ``None``.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Callable

from ..jog import STEPS_PER_REV
from .commands import CommandType, DeckCommand
from .loop import LoopEngine
from .playback import NullPlaybackPort, PlaybackPort
from .display_state import KEY_SHIFT_LIMIT
from .quantize import (
    next_beat_value,
    normalise_beat_value,
    quantize_position,
)
from .slip import SlipEngine, SlipReason, SlipState
from .state import (
    AudioStatus,
    BEAT_JUMP_BEAT_VALUES,
    CueKind,
    DeckState,
    Direction,
    HotCue,
    JogMode,
    LoopAdjust,
    LoopExitReason,
    LoopState,
    PadMode,
    PlayState,
    TEMPO_RANGES,
    TrackInfo,
    empty_state,
)

log = logging.getLogger(__name__)

Listener = Callable[[DeckState], None]

#: Sekunden pro Jog-Umdrehung beim Scratchen **ohne** Beatgrid. Entspricht
#: der Vinyl-Anmutung eines echten CDJ (rund 33 1/3 U/min). Mit Beatgrid
#: gilt stattdessen die Vorgabe "eine Umdrehung = ein Beat", die Platte
#: folgt dann also dem Tempo des Tracks.
JOG_SECONDS_PER_REV = 1.8

#: Faktor fuer Pitch Bend am Jog-Rand: Ticks -> Sekunden Positionsversatz.
JOG_BEND_SECONDS_PER_REV = 0.25

#: Geschwindigkeit des Schnellvor-/ruecklaufs als Vielfaches.
SEARCH_SPEED = 8.0

#: SEARCH gehalten **und** Jogwheel gedreht: Sekunden je Umdrehung. Das
#: ist die schnelle Suche des Geraets - eine Umdrehung deckt eine halbe
#: Minute ab, damit ein langer Track in wenigen Drehungen durchlaufen ist.
SEARCH_JOG_SECONDS_PER_REV = 30.0

#: Tempobereich fuer WIDE in Prozent.
WIDE_RANGE_PERCENT = 100.0

#: Ab dieser Position gilt ein Track als "mitten drin": TRACK SEARCH
#: zurueck springt dann erst an den Trackanfang (Handbuch S. 48). Ein
#: zweiter Druck steht damit ohnehin bei 0 und geht zum vorherigen Track.
TRACK_RESTART_S = 0.01

#: SEARCH gehalten im Loop-Adjust: Beats je Sekunde. Derselbe Massstab
#: wie am Jogwheel, wo eine Umdrehung einen Beat verschiebt.
LOOP_ADJUST_BEATS_PER_S = 1.0

#: Ohne Bedienung endet der Loop-Adjust nach dieser Zeit (Handbuch S. 58).
LOOP_ADJUST_TIMEOUT_S = 10.0


class Deck:
    """Ein Deck. Haelt und veraendert genau einen ``DeckState``."""

    def __init__(
        self,
        deck_id: int,
        *,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        self.deck_id = deck_id
        self._time = time_source
        self._state = empty_state(deck_id)
        self._listeners: list[Listener] = []
        self._last_tick = self._time()
        #: Eigene Zeitmarke der Slip-Zeitachse. Getrennt von ``_last_tick``,
        #: weil die Transportpfade diese Marke unterschiedlich fortschreiben
        #: und die Hintergrundzeit trotzdem auf **jedem** Takt laufen muss.
        self._slip_tick = self._last_tick
        #: Zeitpunkt der letzten Bedienung - Grundlage der 10-Sekunden-
        #: Automatik des Loop-Adjusts (Handbuch S. 58).
        self._adjust_tick = self._last_tick
        #: Audioausgabe. Ohne angehaengte Ausgabe wird kein Ton erzeugt und
        #: die Position laeuft ueber die Wanduhr.
        self.playback: PlaybackPort = NullPlaybackPort()
        #: Modifikatoren, die als gehaltene Taste wirken.
        self._delete_held = False
        self._search_direction = 0
        self._cue_held = False
        #: Pad-Ereignisse, fuer die noch keine Logik festgelegt ist.
        self.deferred_pad_events: list[tuple[int, PadMode]] = []
        #: Kommandos, die mangels Backend nicht ausgefuehrt werden konnten.
        self.unsupported: list[str] = []
        #: Angeforderte Trackwechsel (``-1`` / ``+1``). Das Deck kennt
        #: keine Trackliste; es vermerkt den Wunsch, die Anwendung fuehrt
        #: ihn aus - derselbe Weg wie bei LOAD.
        self.track_requests: list[int] = []
        #: Beatgrid vor der ersten Rasterverschiebung - fuer RESET (S. 72).
        self._original_grid: object | None = None

    # ------------------------------------------------------------------
    # Zustand
    # ------------------------------------------------------------------

    @property
    def state(self) -> DeckState:
        return self._state

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _update(self, **changes: object) -> DeckState:
        state = self._state.with_changes(**changes)
        self._state = self._with_derived(state)
        for listener in list(self._listeners):
            listener(self._state)
        return self._state

    def _with_derived(self, state: DeckState) -> DeckState:
        """Beat, Takt, Phase und aktuelle BPM aus dem Beatgrid ableiten."""
        grid = state.track.beat_grid if state.track else None
        if grid is not None and grid.is_valid:
            bar, beat = grid.bar_and_beat_at(state.position_s)
            phase = grid.phase_at(state.position_s)
        else:
            bar, beat, phase = 0, 0, 0.0

        speed = _speed(state)
        current_bpm = state.original_bpm * speed if state.original_bpm else 0.0

        return dataclasses.replace(
            state,
            bar=bar,
            beat=beat,
            beat_phase=phase,
            current_bpm=round(current_bpm, 2),
        )

    def set_backend_context(
        self, operating_mode: str, connection_state: str
    ) -> DeckState:
        """Vom Backend sichtbare Laufzeitdaten in den gemeinsamen State legen.

        Die GUI braucht dadurch weder den ``ModeManager`` noch eine konkrete
        Audio-/Netzwerkimplementierung zu kennen.
        """
        if (
            self._state.operating_mode == operating_mode
            and self._state.connection_state == connection_state
        ):
            return self._state
        return self._update(
            operating_mode=operating_mode,
            connection_state=connection_state,
        )

    # ------------------------------------------------------------------
    # Track
    # ------------------------------------------------------------------

    def attach_playback(self, port: PlaybackPort) -> None:
        """Audioausgabe anhaengen. Ab dann ist sie die Quelle der Position."""
        self.playback = port
        self._sync_playback()
        self._update(audio_status=self._audio_status())

    def detach_playback(self) -> None:
        self.playback.set_playing(False)
        self.playback = NullPlaybackPort()
        self._update(audio_status=AudioStatus.NO_BACKEND)

    def _audio_status(self) -> AudioStatus:
        if isinstance(self.playback, NullPlaybackPort):
            return AudioStatus.NO_BACKEND
        return (
            AudioStatus.RUNNING if self.playback.is_ready else AudioStatus.IDLE
        )

    def load_track(
        self, track: TrackInfo, samples: object | None = None
    ) -> DeckState:
        """Track uebernehmen. Die Analyse muss bereits erfolgt sein.

        Args:
            track: Trackdaten samt Analyse.
            samples: Audiodaten ``(n, 2)`` float32 in Engine-Rate. Ohne
                Samples wird nur der Zustand gesetzt und kein Ton erzeugt.
        """
        self._last_tick = self._time()
        self._slip_tick = self._last_tick
        if samples is not None:
            self.playback.load(samples)  # type: ignore[arg-type]
        self._log_track_change_exit()
        state = self._update(
            track=track,
            play_state=PlayState.STOPPED,
            position_s=0.0,
            duration_s=track.duration_s,
            original_bpm=track.original_bpm,
            key=track.key,
            cue_point_s=0.0,
            # Trackwechsel ist der zweite erlaubte Weg aus einem Loop
            # (neben RELOOP/EXIT). Der Grund wird mitgefuehrt.
            loop=LoopState(exit_reason=LoopExitReason.TRACK_CHANGED),
            # Ein neuer Track ist kein temporaerer Eingriff: die
            # Hintergrund-Zeitachse des alten Tracks gilt nicht mehr
            # (Abschnitt 39). ``slip``, ``quantize`` und ``jog_mode``
            # bleiben als Einstellungen stehen.
            slip_state=SlipState(),
            bar=0,
            beat=0,
            beat_phase=0.0,
            audio_status=self._audio_status(),
        )
        self._sync_playback()
        return state

    def _log_track_change_exit(self) -> None:
        """Loop-Ende durch Trackwechsel protokollieren.

        Track laden und auswerfen setzen den Loop-Zustand als Ganzes neu
        und gehen deshalb nicht durch ``_set_loop``. Der Wechsel von
        ``active = True`` auf ``False`` soll trotzdem seinen Grund nennen.
        """
        loop = self._state.loop
        if not loop.active:
            return
        log.info(
            "LOOP EXIT   deck=%d in=%.4f out=%.4f beats=%s "
            "ACTIVE: true -> false  reason=%s",
            self.deck_id, loop.in_s or 0.0, loop.out_s or 0.0, loop.beats,
            LoopExitReason.TRACK_CHANGED.value,
        )

    def eject(self) -> DeckState:
        self._log_track_change_exit()
        self.playback.unload()
        return self._update(
            track=None,
            play_state=PlayState.EMPTY,
            position_s=0.0,
            duration_s=0.0,
            original_bpm=0.0,
            key="",
            cue_point_s=None,
            # Trackwechsel ist der zweite erlaubte Weg aus einem Loop
            # (neben RELOOP/EXIT). Der Grund wird mitgefuehrt.
            loop=LoopState(exit_reason=LoopExitReason.TRACK_CHANGED),
            slip_state=SlipState(),
            audio_status=self._audio_status(),
        )

    # ------------------------------------------------------------------
    # Abgleich mit der Audioausgabe
    # ------------------------------------------------------------------

    def _sync_playback(self) -> None:
        """Transport, Tempo und Loop an die Ausgabe weitergeben."""
        port = self.playback
        if isinstance(port, NullPlaybackPort):
            return
        state = self._state
        speed = _speed(state)
        if state.direction in (Direction.REV, Direction.SLIP_REV):
            speed = -speed
        port.set_speed(speed)
        loop = state.loop
        port.set_loop(loop.active and loop.is_set, loop.in_s, loop.out_s)
        # Die angefasste Platte im Vinyl-Modus haelt den Ton an - genau
        # einmal entschieden, damit Anzeige und Ausgabe nicht auseinander
        # laufen koennen.
        port.set_playing(
            state.play_state in (PlayState.PLAYING, PlayState.CUEING)
            and not self._vinyl_hold()
        )

    # ------------------------------------------------------------------
    # Takt: Position fortschreiben
    # ------------------------------------------------------------------

    def tick(self, now: float | None = None) -> DeckState:
        """Position fortschreiben.

        Mit angehaengter Audioausgabe ist der Audio-Thread die Quelle der
        Wahrheit - die Position wird dort sample-genau gefuehrt und hier nur
        abgelesen. Ohne Ausgabe laeuft der Transport ueber die Wanduhr.

        Gehaltenes SEARCH ist ein eigener Transportmodus und geht vor:
        solange gesucht wird, bewegt sich der Track mit der Suchgeschwindig-
        keit - ob er gerade spielt oder pausiert.

        Die Slip-Zeitachse laeuft **vor** allen Transportpfaden und
        unabhaengig von ihnen: sie beschreibt gerade den Fall, in dem der
        hoerbare Transport etwas anderes tut.
        """
        now = self._time() if now is None else now
        self._advance_slip(now)
        self._expire_adjust(now)
        if self._search_direction:
            return self._tick_search(now)
        if self.playback.is_ready:
            return self._tick_from_audio()
        return self._tick_from_clock(now)

    # ------------------------------------------------------------------
    # Loop-Adjust: Feineinstellung und ihre Automatik
    # ------------------------------------------------------------------

    def _adjusting_loop(self) -> bool:
        """Ob gerade ein Loop-Punkt am Jogwheel bzw. an SEARCH haengt.

        Nur mit wirklich laufendem, gesetztem Loop. Ein Adjust-Modus ohne
        Loop darf weder das Jogwheel stillegen noch SEARCH umwidmen.
        """
        loop = self._state.loop
        return loop.adjust is not LoopAdjust.NONE and loop.active and loop.is_set

    def _note_adjust(self) -> None:
        """Bedienung vermerken - die 10-Sekunden-Automatik faengt neu an."""
        self._adjust_tick = self._time()

    def _expire_adjust(self, now: float) -> None:
        """Adjust-Modus nach 10 s ohne Bedienung beenden (Handbuch S. 58).

        Ohne diese Automatik bleibt das Jogwheel unbegrenzt vom Transport
        abgekoppelt: das Geraet wirkt dann eingefroren, obwohl es nur auf
        einen zweiten Tastendruck wartet.
        """
        if not self._adjusting_loop():
            return
        if now - self._adjust_tick < LOOP_ADJUST_TIMEOUT_S:
            return
        self._set_loop(self._loop.ended_adjust(self._state.loop))

    def _vinyl_hold(self) -> bool:
        """Platte im Vinyl-Modus angefasst - die Wiedergabe steht still.

        Das ist die Vinyl-Bremse des Geraets in ihrer einfachsten ehrlichen
        Form: **ohne** Auslauf- und Anlauframpe. Die Rampe haengt am
        ``VINYL SPEED ADJUST`` und fehlt weiterhin; der Halt selbst fehlte
        bisher ganz, und ohne ihn liefen Scratch und Wiedergabe gleichzeitig.

        Der Transportzustand bleibt ``PLAYING`` - so wie am Geraet, wo die
        PLAY-Anzeige beim Anfassen der Platte nicht ausgeht.
        """
        state = self._state
        return (
            state.jog_mode is JogMode.VINYL
            and state.jog_touch
            and state.play_state is PlayState.PLAYING
        )

    def _transport_speed(self) -> float:
        """Geschwindigkeit der normalen Wiedergabe, ``0.0`` bei Pause."""
        state = self._state
        if state.play_state not in (PlayState.PLAYING, PlayState.CUEING):
            return 0.0
        if self._vinyl_hold():
            return 0.0
        speed = _speed(state)
        if state.direction in (Direction.REV, Direction.SLIP_REV):
            speed = -speed
        return speed

    def _tick_search(self, now: float | None = None) -> DeckState:
        """Schnellvor-/ruecklauf, solange die Taste gehalten wird.

        Es gibt keine Ereigniswarteschlange: gehalten wird ein **Zustand**
        (``_search_direction``), und jeder Takt rechnet daraus die Strecke
        seit dem letzten Takt. Loslassen wirkt sofort, weil der Zustand
        sofort 0 wird - es bleiben keine alten Wiederholungen liegen.

        Mit Audioausgabe fuehrt der Audio-Thread die Position weiter; dann
        wird nur die **Differenz** zur Suchgeschwindigkeit nachgeschoben.
        Sonst ergaebe Suchen waehrend der Wiedergabe Wiedergabe + Suche.
        """
        now = self._time() if now is None else now
        elapsed = now - self._last_tick
        self._last_tick = now
        state = self._state
        if elapsed <= 0 or not state.has_track:
            return state

        if self._adjusting_loop():
            # Im Adjust-Modus ist SEARCH die Feineinstellung des
            # Loop-Punkts (Handbuch S. 58), nicht der Schnellvorlauf. Eine
            # Sekunde Halten verschiebt um einen Beat - derselbe Massstab
            # wie eine Jog-Umdrehung, und dieselbe Rechnung.
            self._note_adjust()
            self._set_loop(
                self._loop.adjust_with_jog(
                    state.loop,
                    LOOP_ADJUST_BEATS_PER_S * elapsed * self._search_direction,
                )
            )
            return self._state

        target = SEARCH_SPEED * self._search_direction
        if self.playback.is_ready:
            self.playback.nudge_seconds(
                (target - self._transport_speed()) * elapsed
            )
            return self._update(position_s=self.playback.position_seconds)
        return self._seek(state.position_s + target * elapsed, wrap_loop=True)

    def _tick_from_audio(self) -> DeckState:
        state = self._state
        position = self.playback.position_seconds

        if self.playback.reached_end and state.play_state is PlayState.PLAYING:
            return self._update(
                position_s=position, play_state=PlayState.STOPPED
            )
        if abs(position - state.position_s) < 1e-9:
            return state
        return self._update(position_s=position)

    def _tick_from_clock(self, now: float | None = None) -> DeckState:
        now = self._time() if now is None else now
        elapsed = now - self._last_tick
        self._last_tick = now
        if elapsed <= 0:
            return self._state

        state = self._state
        if not state.has_track:
            return state

        # Gehaltenes SEARCH faengt ``tick()`` bereits ab; hier bleibt die
        # normale Wiedergabe.
        speed = self._transport_speed()

        if speed == 0.0:
            return state

        return self._seek(state.position_s + elapsed * speed, wrap_loop=True)

    # ------------------------------------------------------------------
    # Positionierung
    # ------------------------------------------------------------------

    def _seek(self, position_s: float, *, wrap_loop: bool = False) -> DeckState:
        state = self._state
        duration = state.duration_s
        loop = state.loop

        if self.playback.is_ready:
            # Mit Audio uebernimmt die Ausgabe die Loop- und Randbehandlung
            # sample-genau. Hier wird nur gesprungen.
            target = max(0.0, min(position_s, duration))
            self.playback.seek_seconds(target)
            return self._update(position_s=target)

        if wrap_loop:
            # Loop-Grenze: eine einzige Stelle dafuer, in der Loop-Engine.
            position_s = self._loop.check_boundary(loop, position_s)

        if position_s < 0.0:
            position_s = 0.0
            if state.play_state is PlayState.PLAYING:
                return self._update(
                    position_s=0.0, play_state=PlayState.PAUSED
                )
        if duration > 0 and position_s >= duration:
            return self._update(
                position_s=duration, play_state=PlayState.STOPPED
            )

        return self._update(position_s=position_s)

    # ------------------------------------------------------------------
    # Quantisierung
    #
    # Gerechnet wird in ``deck/quantize.py``. Hier wird nur entschieden,
    # **ob** gerastert wird, und die eingestellte Weite mitgegeben. Es gibt
    # keine zweite Rundung irgendwo sonst im Deck.
    # ------------------------------------------------------------------

    def _quantized(self, position_s: float) -> float:
        """Wo eine musikalische Aktion ihren Punkt setzt.

        Benutzt von CUE und den Hotcues; Loop In/Out, CALL und die
        Beatloops gehen ueber ``LoopEngine.position_for``, das dieselbe
        Funktion mit derselben Weite aufruft.

        Bei ausgeschaltetem QUANTIZE ist das exakt die uebergebene Position -
        es gibt keine versteckte Beatgrid-Korrektur.
        """
        state = self._state
        if not state.quantize or state.track is None:
            return position_s
        return quantize_position(
            position_s, state.track.beat_grid, state.quantize_beats
        )

    # ------------------------------------------------------------------
    # Slip
    #
    # Die Rechnung steht in ``deck/slip.py``. Hier wird sie an die
    # Bedienschritte gehaengt: welcher Eingriff eine Slip-Aktion beginnt,
    # welcher sie beendet, und wann die Hintergrundzeit laeuft.
    # ------------------------------------------------------------------

    @property
    def _slip(self) -> SlipEngine:
        return SlipEngine(duration_s=self._state.duration_s)

    def _slip_begin(self, reason: SlipReason, *, force: bool = False) -> None:
        """Eine Slip-Aktion beginnen, sofern SLIP eingeschaltet ist.

        ``force`` gilt fuer die Stellung SLIP REV des Richtungsschalters:
        die ist am Geraet die Slip-Stellung des Hebels und wirkt auch ohne
        den SLIP-Taster.

        Eine Zeitachse entsteht nur, wenn der Track ohne den Eingriff
        wirklich weiterliefe - auf einem stehenden Deck gibt es nichts, was
        im Hintergrund vergeht.
        """
        state = self._state
        if not (state.slip or force) or not state.has_track:
            return
        slip = state.slip_state
        if slip.has(reason):
            return
        if not slip.active and not state.is_playing:
            return
        self._slip_tick = self._time()
        self._update(
            slip_state=self._slip.begin(slip, reason, state.position_s)
        )

    def _slip_end(self, reason: SlipReason) -> None:
        """Eine Slip-Aktion beenden und ggf. zur Hintergrundposition springen.

        Gesprungen wird erst, wenn der **letzte** Grund endet. Loslassen des
        Jogwheels waehrend eines laufenden Loops reisst den Loop also nicht
        auseinander.
        """
        before = self._state.slip_state
        slip, resume_s = self._slip.end(before, reason)
        if slip == before:
            return
        log.info(
            "SLIP END    deck=%d reason=%s resume=%s",
            self.deck_id, reason.value,
            "-" if resume_s is None else f"{resume_s:.4f}",
        )
        self._update(slip_state=slip)
        if resume_s is not None:
            self._seek(resume_s)

    def _slip_reset(self) -> None:
        """Zeitachse verwerfen, **ohne** zu springen.

        Fuer bewusste Trackwechsel und fuer das Ausschalten von SLIP: dort
        will niemand noch einmal an eine alte Hintergrundposition geworfen
        werden.
        """
        if not self._state.slip_state.active:
            return
        self._update(slip_state=self._slip.reset())

    def _advance_slip(self, now: float) -> None:
        """Hintergrundzeit fortschreiben - auf jedem Takt, vor dem Transport."""
        elapsed = now - self._slip_tick
        self._slip_tick = now
        slip = self._state.slip_state
        if not slip.active or elapsed <= 0:
            return
        # Immer vorwaerts und mit dem eingestellten Tempo: die Zeitachse
        # beschreibt die normale Wiedergabe, nicht den hoerbaren Eingriff.
        advanced = self._slip.advance(slip, elapsed * abs(_speed(self._state)))
        if advanced != slip:
            self._update(slip_state=advanced)

    def _sync_slip_loop(self, before: LoopState, after: LoopState) -> None:
        """Ein startender Loop beginnt eine Slip-Aktion, ein endender sie.

        Haengt an ``_set_loop`` - der einzigen Stelle, die ``DeckState.loop``
        schreibt. Damit gibt es keinen Loop, der an Slip vorbeikaeme, egal ob
        er von einem Pad, von CALL, von LOOP IN/OUT oder aus einem Hotcue
        stammt.
        """
        if after.active and not before.active:
            self._slip_begin(SlipReason.LOOP)
        elif before.active and not after.active:
            self._slip_end(SlipReason.LOOP)

    # ------------------------------------------------------------------
    # Loop
    #
    # Die Loop-Logik selbst steht in ``deck/loop.py``. Hier wird sie nur
    # mit dem Beatgrid des geladenen Tracks versorgt und ihr Ergebnis in
    # den Zustand gehaengt.
    # ------------------------------------------------------------------

    @property
    def _loop(self) -> LoopEngine:
        state = self._state
        track = state.track
        return LoopEngine(
            grid=track.beat_grid if track else None,
            quantize=state.quantize,
            beat_value=state.quantize_beats,
        )

    def _set_loop(self, loop: LoopState) -> None:
        """Neuen Loop-Zustand uebernehmen, falls er sich geaendert hat.

        **Die** Stelle, durch die jede Loop-Aenderung geht. Es gibt keinen
        zweiten Schreibweg auf ``DeckState.loop`` - deshalb genuegt hier
        eine Protokollierung, um jeden Zustandswechsel zu sehen.
        """
        before = self._state.loop
        if loop == before:
            return
        self._update(loop=loop)
        self._log_loop_change(before, loop)
        self._sync_slip_loop(before, loop)

    def _log_loop_change(self, before: LoopState, after: LoopState) -> None:
        """Loop-Ereignisse protokollieren (Entwicklungsmodus).

        Wichtigster Fall: **jeder** Wechsel von ``active = True`` auf
        ``False`` nennt seinen Grund. Ein unerwarteter Ausstieg ist damit
        sofort sichtbar, statt als "der Loop hoert manchmal einfach auf".
        """
        if before.active and not after.active:
            log.info(
                "LOOP EXIT   deck=%d in=%.4f out=%.4f beats=%s "
                "ACTIVE: true -> false  reason=%s",
                self.deck_id, before.in_s or 0.0, before.out_s or 0.0,
                before.beats,
                after.exit_reason.value if after.exit_reason else "UNBEKANNT",
            )
            if after.exit_reason is None:
                # Darf nicht vorkommen: ``exit_loop`` verlangt einen Grund.
                log.warning(
                    "LOOP EXIT ohne Grund - deck=%d", self.deck_id
                )
            return

        if not before.active and after.active:
            kind = "RELOOP" if before.has_last and (
                after.in_s == before.last_in_s
                and after.out_s == before.last_out_s
            ) else "CREATED"
            log.info(
                "LOOP %s deck=%d in=%.4f out=%.4f beats=%s",
                kind, self.deck_id, after.in_s or 0.0, after.out_s or 0.0,
                after.beats,
            )
            return

        if after.active and (
            before.in_s != after.in_s or before.out_s != after.out_s
        ):
            log.info(
                "LOOP RESIZED deck=%d in=%.4f out=%.4f beats=%s "
                "(vorher %.4f-%.4f, beats=%s)",
                self.deck_id, after.in_s or 0.0, after.out_s or 0.0,
                after.beats, before.in_s or 0.0, before.out_s or 0.0,
                before.beats,
            )

    # ------------------------------------------------------------------
    # Kommandos
    # ------------------------------------------------------------------

    def execute(self, cmd: DeckCommand) -> DeckState:
        """Ein Deck-Kommando ausfuehren."""
        if cmd.deck_id != self.deck_id:
            return self._state
        handler = _HANDLERS.get(cmd.type)
        if handler is None:
            self.unsupported.append(cmd.type.value)
            return self._state
        # Jedes Kommando ist eine Bedienung des Geraets und setzt damit die
        # 10-Sekunden-Automatik des Loop-Adjusts zurueck (Handbuch S. 58:
        # "warten Sie mit der Bedienung des Geraets").
        self._note_adjust()
        handler(self, cmd)
        # Nach jeder Zustandsaenderung Transport, Tempo und Loop an die
        # Audioausgabe weitergeben. Ein einziger Ort dafuer - so kann keine
        # Anzeige von der Ausgabe abweichen.
        self._sync_playback()
        return self._state

    # -- Transport --------------------------------------------------------

    def _cmd_play_pause(self, cmd: DeckCommand) -> None:
        state = self._state
        if not state.has_track:
            return
        if state.play_state is PlayState.PLAYING:
            # Mit SLIP ist die Pause ein temporaerer Eingriff: hoerbar steht
            # der Track, die Zeitachse laeuft weiter (Abschnitt 16).
            self._slip_begin(SlipReason.PAUSE)
            self._update(play_state=PlayState.PAUSED)
        else:
            self._last_tick = self._time()
            self._update(play_state=PlayState.PLAYING)
            # Erst spielen, dann springen: ``_slip_end`` setzt die Position
            # auf die Hintergrundzeit.
            self._slip_end(SlipReason.PAUSE)

    def _cmd_cue(self, cmd: DeckCommand) -> None:
        state = self._state
        if not state.has_track:
            return
        pressed = cmd.pressed
        self._cue_held = pressed

        if pressed:
            if state.play_state is PlayState.PLAYING:
                # Waehrend der Wiedergabe: zurueck zum Cue-Punkt, Pause.
                # CUE ist ein ausdruecklicher Sprung, kein temporaerer
                # Eingriff (Abschnitt 22): eine laufende Slip-Aktion endet,
                # ohne die Wiedergabe wieder vom Cue-Punkt wegzureissen.
                target = state.cue_point_s or 0.0
                self._slip_reset()
                self._update(play_state=PlayState.PAUSED)
                self._leave_loop_if_outside(target)
                self._seek(target)
                return
            at_cue = (
                state.cue_point_s is not None
                and abs(state.position_s - state.cue_point_s) < 1e-3
            )
            if at_cue:
                # Cue Point Sampler: spielt, solange gehalten.
                self._last_tick = self._time()
                self._update(play_state=PlayState.CUEING)
            else:
                # Neuen Cue-Punkt setzen.
                self._update(cue_point_s=self._quantized(state.position_s))
            return

        # Loslassen
        if self._state.play_state is PlayState.CUEING:
            self._slip_reset()
            self._update(play_state=PlayState.PAUSED)
            target = self._state.cue_point_s or 0.0
            self._leave_loop_if_outside(target)
            self._seek(target)

    def _leave_loop_if_outside(self, position_s: float) -> None:
        """Ein bewusster Sprung aus dem Loop heraus verlaesst ihn.

        Ohne das ist ein laufender Loop eine Sackgasse: die Loop-Grenze
        zieht die Position im naechsten Bild wieder zurueck, und jeder
        Sprung - Nadelsetzen auf der Uebersicht, Hotcue, Cue - sieht aus,
        als reagiere das Geraet nicht mehr.

        Nur fuer **ausdrueckliche** Spruenge. Jog, Scratch und Pitch Bend
        gehen weiter durch die Loop-Grenze und bleiben damit im Loop; das
        ist beim Scratchen gewollt.
        """
        loop = self._state.loop
        if not loop.active or not loop.is_set:
            return
        if loop.in_s <= position_s < loop.out_s:  # type: ignore[operator]
            return
        self._set_loop(
            self._loop.exit_loop(loop, LoopExitReason.JUMPED_OUT)
        )

    def _cmd_seek(self, cmd: DeckCommand) -> None:
        """Ausdruecklicher Sprung - Nadelsetzen auf der Uebersicht.

        Kein temporaerer Eingriff: eine laufende Slip-Aktion endet hier ohne
        Sprung zurueck. SLIP darf nicht jeden bewussten Sprung wieder
        einkassieren (Abschnitt 22).
        """
        if not self._state.has_track:
            return
        target = float(cmd.get("position_s", 0.0))
        self._slip_reset()
        self._leave_loop_if_outside(target)
        self._seek(target)

    def _cmd_search(self, cmd: DeckCommand) -> None:
        direction = int(cmd.get("direction", 0))
        self._search_direction = direction if cmd.pressed else 0
        self._last_tick = self._time()

    def _cmd_track_search(self, cmd: DeckCommand) -> None:
        """TRACK SEARCH |<< und >>| (Handbuch S. 48).

        |<< springt zunaechst an den **Anfang des laufenden Tracks**; ein
        zweiter Druck steht dann bei 0 und geht zum vorherigen Track. >>|
        geht immer zum naechsten.

        Den Sprung an den Trackanfang macht das Deck selbst. Den
        Trackwechsel kann es nicht: es kennt keine Liste. Er wird in
        ``track_requests`` vermerkt und von der Anwendung ausgefuehrt -
        derselbe Weg wie bei LOAD, und derselbe Grund: die Bibliothek
        gehoert nicht in die Deck-Engine.

        Ein bewusster Trackwechsel ist keine temporaere Slip-Aktion: die
        Hintergrund-Zeitachse wird verworfen, nicht angesprungen.
        """
        self._slip_reset()
        state = self._state
        if not state.has_track:
            return
        direction = int(cmd.get("direction", 0))
        if direction == 0:
            return
        if direction < 0 and state.position_s > TRACK_RESTART_S:
            # Zurueck an den Anfang des laufenden Tracks. Das ist ein
            # ausdruecklicher Sprung und verlaesst deshalb einen Loop.
            self._leave_loop_if_outside(0.0)
            self._seek(0.0)
            return
        self.track_requests.append(1 if direction > 0 else -1)

    def take_track_requests(self) -> list[int]:
        """Angeforderte Trackwechsel abholen und die Liste leeren.

        Vom Backend nach dem Ausfuehren eines Kommandos gerufen. Das Deck
        selbst fuehrt nie einen Trackwechsel aus.
        """
        requests, self.track_requests = self.track_requests, []
        return requests

    def _cmd_direction(self, cmd: DeckCommand) -> None:
        """Richtungsschalter FWD / REV / SLIP REV.

        Rueckwaerts ist ein temporaerer Eingriff: hoerbar laeuft der Track
        rueckwaerts, die Hintergrundzeit normal vorwaerts weiter. Zurueck auf
        FWD setzt die Wiedergabe dort fort (Abschnitt 21). Eine eigene
        Reverse-Engine entsteht dabei nicht - die Richtung wirkt weiterhin
        allein ueber ``_transport_speed`` bzw. ``_sync_playback``.
        """
        position = cmd.get("position")
        if not isinstance(position, Direction):
            return
        if position is self._state.direction:
            return
        self._update(direction=position)
        if position in (Direction.REV, Direction.SLIP_REV):
            # SLIP REV ist am Geraet die Slip-Stellung des Hebels; sie wirkt
            # auch ohne den SLIP-Taster.
            self._slip_begin(
                SlipReason.REVERSE, force=position is Direction.SLIP_REV
            )
            return
        self._slip_end(SlipReason.REVERSE)

    # -- Pads -------------------------------------------------------------

    def _cmd_pad(self, cmd: DeckCommand) -> None:
        if not cmd.pressed:
            # Loslassen beendet eine Hotcue-Slip-Aktion (Abschnitt 20). Lief
            # keine, passiert nichts - die uebrigen Pad-Modi bleiben davon
            # unberuehrt.
            self._slip_end(SlipReason.HOT_CUE)
            return
        state = self._state
        if not state.has_track or state.track is None:
            return
        index = int(cmd.get("index", -1))
        if not 0 <= index < 8:
            return

        if state.pad_mode is PadMode.BEAT_LOOP:
            # Pads A-H = 1/4 bis 32 Beats. Gleiches Pad erneut verlaesst
            # den Loop, ein anderes aendert nur die Laenge.
            engine = self._loop
            if not engine.has_grid:
                self.unsupported.append("BEAT_LOOP (kein Beatgrid)")
                return
            self._set_loop(
                engine.beat_loop_pad_pressed(
                    state.loop, index, state.position_s
                )
            )
            return

        if state.pad_mode is not PadMode.HOT_CUE:
            # Die Pad-Logik der uebrigen Modi ist noch nicht festgelegt.
            self.deferred_pad_events.append((index, state.pad_mode))
            return

        cue = state.track.hot_cue(index)

        if self._delete_held:
            if cue is not None:
                self._set_hot_cues(
                    tuple(c for c in state.track.hot_cues if c.index != index)
                )
            return

        if cue is None:
            loop = state.loop
            if loop.active and loop.is_set:
                # Waehrend der Loop-Wiedergabe speichert das Pad den
                # **Loop** statt eines Punkts (Handbuch S. 62). Die
                # Loop-Punkte sind bereits gerastert; hier wird nicht noch
                # einmal quantisiert, sonst waere der Hotcue-Loop nicht
                # mehr derselbe wie der laufende.
                new = HotCue(
                    index=index,
                    position_s=loop.in_s,
                    color="",
                    kind=CueKind.LOOP,
                    loop_end_s=loop.out_s,
                )
            else:
                new = HotCue(
                    index=index,
                    position_s=self._quantized(state.position_s),
                    color="",
                    kind=CueKind.CUE,
                )
            self._set_hot_cues(state.track.hot_cues + (new,))
            return

        # Mit SLIP ist der Hotcue eine temporaere Wiedergabe: gedrueckt
        # halten spielt ab dem Hotcue, Loslassen kehrt zur Hintergrundzeit
        # zurueck. Der Beginn steht **vor** dem Loop-Ausstieg, damit dieser
        # nicht als letzter Grund endet und sofort zurueckspringt.
        self._slip_begin(SlipReason.HOT_CUE)
        # Ein Hotcue ausserhalb des laufenden Loops verlaesst ihn - sonst
        # zieht die Loop-Grenze sofort wieder zurueck und das Pad wirkt tot.
        # Ein Loop-Hotcue setzt gleich darunter ohnehin einen neuen Loop.
        self._leave_loop_if_outside(cue.position_s)
        self._seek(cue.position_s)
        if cue.kind is CueKind.LOOP and cue.loop_end_s is not None:
            self._set_loop(
                self._loop.activate(
                    self._state.loop, cue.position_s, cue.loop_end_s
                )
            )
        self._last_tick = self._time()
        self._update(play_state=PlayState.PLAYING)

    def _set_hot_cues(self, cues: tuple[HotCue, ...]) -> None:
        track = self._state.track
        if track is None:
            return
        self._update(track=dataclasses.replace(track, hot_cues=cues))

    def _cmd_pad_mode(self, cmd: DeckCommand) -> None:
        mode = cmd.get("mode")
        if isinstance(mode, PadMode):
            self._update(pad_mode=mode)

    def _cmd_delete(self, cmd: DeckCommand) -> None:
        self._delete_held = cmd.pressed

    def _cmd_memory(self, cmd: DeckCommand) -> None:
        # Braucht persistenten Cue-Speicher am Track - noch nicht vorhanden.
        self.unsupported.append("MEMORY (kein Cue-Speicher)")

    def _cmd_cue_loop_call(self, cmd: DeckCommand) -> None:
        """CALL < und CALL >: Loop-Laenge.

        Ohne laufenden Loop entsteht ein 4- bzw. 8-Beat-Loop, mit laufendem
        Loop wird die Laenge halbiert bzw. verdoppelt. Am Pioneer-Geraet
        blaettern diese Taster durch gespeicherte Cue- und Loop-Punkte; hier
        sind sie bewusst die Loop-Groessentasten (kein Cue-Speicher).
        """
        state = self._state
        if not state.has_track:
            return
        engine = self._loop
        if not state.loop.active and not engine.has_grid:
            self.unsupported.append("CUE_LOOP_CALL (kein Beatgrid)")
            return
        forward = int(cmd.get("direction", -1)) > 0
        press = (
            engine.call_right_pressed if forward else engine.call_left_pressed
        )
        self._set_loop(press(state.loop, state.position_s))

    # -- Loop -------------------------------------------------------------

    def _cmd_loop_in(self, cmd: DeckCommand) -> None:
        """LOOP IN/CUE: Loop-Anfang **und** Cue-Punkt setzen.

        Die Taste heisst am Geraet ``LOOP IN/CUE (IN ADJUST)`` und tut
        genau das, was ihr Name sagt: derselbe Druck setzt den Loop-Anfang
        (Handbuch S. 57) und den Cue-Punkt (S. 54). Beides an derselben,
        gegebenenfalls quantisierten Stelle - zwei getrennte Rundungen
        waeren zwei verschiedene Punkte.

        Bei laufendem Loop schaltet die Taste stattdessen den IN-Adjust um;
        dann bleibt der Cue-Punkt, wo er ist.
        """
        state = self._state
        if not state.has_track:
            return
        loop = self._loop.loop_in_pressed(state.loop, state.position_s)
        self._set_loop(loop)
        if state.loop.active or loop.in_s is None:
            return
        self._update(cue_point_s=loop.in_s)

    def _cmd_loop_out(self, cmd: DeckCommand) -> None:
        """LOOP OUT: Ende setzen und starten, oder OUT-Adjust umschalten."""
        state = self._state
        if not state.has_track:
            return
        self._set_loop(
            self._loop.loop_out_pressed(state.loop, state.position_s)
        )

    def _cmd_reloop_exit(self, cmd: DeckCommand) -> None:
        """RELOOP/EXIT: laufenden Loop verlassen oder den letzten aufrufen."""
        state = self._state
        if not state.has_track:
            return
        loop, jump_s = self._loop.exit_reloop_pressed(
            state.loop, state.position_s
        )
        self._set_loop(loop)
        if jump_s is not None:
            self._seek(jump_s)

    def _cmd_beat_loop(self, cmd: DeckCommand) -> None:
        state = self._state
        if state.track is None:
            return
        engine = self._loop
        if not engine.has_grid:
            self.unsupported.append("BEAT_LOOP (kein Beatgrid)")
            return
        beats = float(cmd.get("beats", 4.0))
        self._set_loop(engine.beat_loop(state.loop, beats, state.position_s))

    def _cmd_loop_halve(self, cmd: DeckCommand) -> None:
        self._set_loop(self._loop.scaled(self._state.loop, 0.5))

    def _cmd_loop_double(self, cmd: DeckCommand) -> None:
        self._set_loop(self._loop.scaled(self._state.loop, 2.0))

    # -- Beat Jump --------------------------------------------------------

    def _cmd_beat_jump(self, cmd: DeckCommand) -> None:
        """BEAT JUMP - bei laufendem Loop wird der Loop verschoben.

        Handbuch S. 66/67: "Wenn dieser Vorgang waehrend der
        Loop-Wiedergabe erfolgt, wird stattdessen ein Loop verschoben."
        Die Wiedergabe wandert mit und steht danach an derselben Stelle im
        Loop - ohne das faende sie sich ausserhalb wieder und der Loop
        zoege sie im naechsten Bild zurueck.

        CALL/DELETE gehalten: die Taste aendert die Sprungweite, statt zu
        springen (S. 66). Erkannt wird das daran, dass die Richtungstaster
        keine ``beats`` mitschicken - das Touch-Panel tut es und springt
        deshalb immer.
        """
        state = self._state
        if state.track is None:
            return
        direction = int(cmd.get("direction", 1))
        if self._delete_held and cmd.get("beats") is None:
            self._step_beat_jump_beats(direction)
            return
        grid = state.track.beat_grid
        if grid is None or not grid.is_valid:
            self.unsupported.append("BEAT_JUMP (kein Beatgrid)")
            return
        beats = float(cmd.get("beats", state.beat_jump_beats))

        if state.loop.active and state.loop.is_set:
            loop, offset = self._loop.moved(state.loop, beats * direction)
            self._set_loop(loop)
            if offset:
                self._seek(state.position_s + offset)
            return

        offset = grid.seconds_for_beats(beats, state.position_s) * direction
        self._seek(state.position_s + offset)

    def _cmd_beat_jump_beats(self, cmd: DeckCommand) -> None:
        """Sprungweite setzen oder weiterschalten (1/2 ... 64 Beats)."""
        value = cmd.get("beats")
        if value is None:
            self._cycle_beat_jump_beats(int(cmd.get("direction", 1)))
            return
        beats = _nearest(BEAT_JUMP_BEAT_VALUES, float(value))
        if abs(beats - self._state.beat_jump_beats) > 1e-9:
            self._update(beat_jump_beats=beats)

    def _step_beat_jump_beats(self, direction: int) -> None:
        """Einen Schritt in der Reihe weiter - an den Enden bleibt es stehen.

        So verhaelt sich der Taster am Geraet: aus 64 wird nicht wieder 1/2.
        """
        values = BEAT_JUMP_BEAT_VALUES
        index = values.index(_nearest(values, self._state.beat_jump_beats))
        index = max(0, min(index + (1 if direction > 0 else -1), len(values) - 1))
        if abs(values[index] - self._state.beat_jump_beats) > 1e-9:
            self._update(beat_jump_beats=values[index])

    def _cycle_beat_jump_beats(self, direction: int) -> None:
        """Durch die Reihe blaettern, am Ende wieder von vorn.

        Fuer eine einzelne Schaltflaeche, die alle Werte erreichen muss -
        dieselbe Bedienung wie bei der Rasterweite der Quantisierung.
        """
        values = BEAT_JUMP_BEAT_VALUES
        index = values.index(_nearest(values, self._state.beat_jump_beats))
        step = 1 if direction >= 0 else -1
        self._update(beat_jump_beats=values[(index + step) % len(values)])

    # -- Tempo ------------------------------------------------------------

    def _cmd_tempo_set(self, cmd: DeckCommand) -> None:
        value = float(cmd.get("value", 0.5))
        state = self._state
        span = (
            WIDE_RANGE_PERCENT if state.tempo_range is None else state.tempo_range
        )
        # Fader oben (1.0) = schneller, unten (0.0) = langsamer.
        percent = (value - 0.5) * 2.0 * span
        self._update(tempo_percent=round(percent, 3))

    def _cmd_tempo_range_cycle(self, cmd: DeckCommand) -> None:
        state = self._state
        try:
            index = TEMPO_RANGES.index(state.tempo_range)
        except ValueError:
            index = 0
        self._update(tempo_range=TEMPO_RANGES[(index + 1) % len(TEMPO_RANGES)])

    def _cmd_master_tempo_toggle(self, cmd: DeckCommand) -> None:
        self._update(master_tempo=not self._state.master_tempo)

    def _cmd_tempo_reset_toggle(self, cmd: DeckCommand) -> None:
        self._update(tempo_reset=not self._state.tempo_reset)

    # -- Sync / Master ----------------------------------------------------

    def _cmd_sync_toggle(self, cmd: DeckCommand) -> None:
        self._update(sync=not self._state.sync)

    def _cmd_master_set(self, cmd: DeckCommand) -> None:
        # Der endgueltige Master wird vom MasterClock ueber alle Decks
        # bestimmt; hier wird nur die Absicht gesetzt.
        self._update(is_master=not self._state.is_master)

    def _cmd_key_sync(self, cmd: DeckCommand) -> None:
        self.unsupported.append("KEY_SYNC (kein Pitch-Shifting)")

    def _cmd_key_shift(self, cmd: DeckCommand) -> None:
        """Tonart verschieben.

        Der Zustand wird gefuehrt und angezeigt. Die Tonhoehe im Audio
        aendert sich **nicht** - dafuer fehlt ein Pitch-Shifting-Verfahren.
        Das wird ausdruecklich vermerkt statt vorgetaeuscht.
        """
        semitones = int(cmd.get("semitones", 0))
        if semitones == 0:
            return
        limit = KEY_SHIFT_LIMIT
        value = max(-limit, min(limit, self._state.key_shift + semitones))
        if value == self._state.key_shift:
            return
        self.unsupported.append("KEY_SHIFT (kein Pitch-Shifting im Audio)")
        self._update(key_shift=value)

    def _cmd_key_shift_reset(self, cmd: DeckCommand) -> None:
        if self._state.key_shift == 0:
            return
        self._update(key_shift=0)

    # -- Beatgrid (Handbuch S. 72) ----------------------------------------

    def _cmd_beatgrid_shift(self, cmd: DeckCommand) -> None:
        """Beatgrid verschieben - Rastereinstellungsmodus des Drehreglers.

        Verschoben wird entweder um ``delta_s`` oder um einen Bruchteil
        eines Beats (``beats``). Das Raster des geladenen Tracks aendert
        sich wirklich; die Aenderung wird nicht in die Datei geschrieben.
        """
        track = self._state.track
        if track is None or track.beat_grid is None:
            return
        grid = track.beat_grid
        if not grid.is_valid:
            return

        delta_s = float(cmd.get("delta_s", 0.0))
        beats = float(cmd.get("beats", 0.0))
        if beats and grid.bpm > 0:
            delta_s += beats * 60.0 / grid.bpm
        if abs(delta_s) < 1e-9:
            return

        if self._original_grid is None:
            self._original_grid = grid
        self._update(
            track=dataclasses.replace(track, beat_grid=grid.shifted(delta_s))
        )

    def _cmd_beatgrid_reset(self, cmd: DeckCommand) -> None:
        """Urspruengliches Beatgrid wiederherstellen (S. 72: RESET)."""
        track = self._state.track
        if track is None or self._original_grid is None:
            return
        self._update(
            track=dataclasses.replace(track, beat_grid=self._original_grid)
        )
        self._original_grid = None

    def _cmd_quantize_toggle(self, cmd: DeckCommand) -> None:
        """QUANTIZE ein/aus.

        Wirkt ausschliesslich auf **kuenftige** Aktionen. Ein laufender Loop
        bleibt unberuehrt - weder ``active`` noch ``in_s``/``out_s`` aendern
        sich (Abschnitt 10).
        """
        self._update(quantize=not self._state.quantize)

    def _cmd_quantize_beats(self, cmd: DeckCommand) -> None:
        """Rasterweite setzen oder weiterschalten (1/8, 1/4, 1/2, 1 Beat)."""
        value = cmd.get("beats")
        current = self._state.quantize_beats
        beats = (
            next_beat_value(current) if value is None
            else normalise_beat_value(float(value))
        )
        if abs(beats - current) < 1e-9:
            return
        self._update(quantize_beats=beats)

    def _cmd_slip_toggle(self, cmd: DeckCommand) -> None:
        """SLIP ein/aus.

        Beim Einschalten waehrend eines laufenden Loops beginnt die
        Hintergrundzeit sofort - sonst haette ein bereits laufender Loop
        keinen Bezugspunkt, zu dem er zurueckkehren koennte.

        Beim Ausschalten enden laufende Slip-Aktionen **ohne** Sprung: wer
        den Modus verlaesst, will nicht noch einmal an eine alte
        Hintergrundposition geworfen werden.
        """
        enabled = not self._state.slip
        self._update(slip=enabled)
        if not enabled:
            self._slip_reset()
            return
        if self._state.loop.active:
            self._slip_begin(SlipReason.LOOP)

    # -- Jogwheel ---------------------------------------------------------

    def _cmd_jog_touch(self, cmd: DeckCommand) -> None:
        """Beruehrung der Platte melden.

        Die Eingabeseite meldet nur **ob** beruehrt wird - fuer virtuelles
        und spaeter echtes Jogwheel dasselbe Ereignis. Was daraus wird,
        entscheidet allein der Jog-Modus, und zwar hier: im Vinyl-Modus
        Vinyl-Stop bzw. Scratch (und mit SLIP eine Slip-Aktion), im
        CDJ-Modus nichts ausser der Meldung selbst.
        """
        self._update(jog_touch=cmd.pressed)
        if cmd.pressed:
            if self._state.jog_mode is JogMode.VINYL:
                self._slip_begin(SlipReason.SCRATCH)
            return
        self._slip_end(SlipReason.SCRATCH)

    def _cmd_jog_move(self, cmd: DeckCommand) -> None:
        """Jogwheel-Bewegung. ``delta`` ist die Summe seit dem letzten Mal.

        Die Sensorseite (``jog/``) zaehlt jeden Schritt mit; hier kommt
        immer nur die Bewegung seit der letzten Verarbeitung an. Eine
        Umdrehung entspricht einem Beat - ``ticks_per_rev`` sagt, aus wie
        vielen Schritten sie besteht (800 am gebauten Jogwheel).
        """
        state = self._state
        if not state.has_track:
            return
        delta = int(cmd.get("delta", 0))
        if delta == 0:
            return
        ticks_per_rev = float(cmd.get("ticks_per_rev", STEPS_PER_REV))
        if ticks_per_rev <= 0:
            return
        revolutions = delta / ticks_per_rev

        if self._search_direction:
            # SEARCH gehalten und dabei am Jogwheel drehen: schnelle Suche
            # durch den Track, in der Richtung der **Drehung**. Es entsteht
            # keine zweite Jog-Auswertung - dieselben Umdrehungen wie sonst,
            # nur ein anderer Massstab.
            self._seek(
                state.position_s + revolutions * SEARCH_JOG_SECONDS_PER_REV
            )
            return

        if self._adjusting_loop():
            # Loop-Adjust: eine Umdrehung verschiebt den gewaehlten
            # Loop-Punkt um einen Beat. Die Wiedergabe bleibt, wo sie ist.
            #
            # Geschluckt wird die Bewegung nur, wenn es wirklich einen Loop
            # zu verschieben gibt. Sonst faellt sie durch auf Scratch bzw.
            # Pitch Bend - ein Adjust-Modus ohne Loop darf das Jogwheel
            # nicht stillegen.
            #
            # Die Richtung bleibt hier die der Drehung: die Feineinstellung
            # ist ein Bearbeitungsschritt, kein Transport, und dreht sich
            # deshalb bei Reverse nicht mit um.
            self._note_adjust()
            self._set_loop(self._loop.adjust_with_jog(state.loop, revolutions))
            return

        # Der Jog-Modus entscheidet hier - und nur hier -, was aus einer
        # Drehung wird. Die Eingabeseite kennt weder VINYL noch CDJ; sie
        # meldet ausschliesslich Beruehrung und Schritte.
        #
        #                | spielt                     | pausiert
        #   -------------+----------------------------+--------------
        #   VINYL, Platte| Scratch                    | Frame Search
        #   VINYL, Rand  | Pitch Bend                 | Pitch Bend
        #   CDJ          | Pitch Bend (auch beruehrt) | Frame Search
        #
        # Im CDJ-Modus wird die Beruehrung bewusst nicht ausgewertet: dort
        # gibt es weder Scratch noch Vinyl-Stop, nur Rand und Platte tun
        # dasselbe.
        playing = state.play_state in (PlayState.PLAYING, PlayState.CUEING)
        if state.jog_mode is JogMode.VINYL:
            direct = state.jog_touch
        else:
            direct = not playing
        if direct:
            # Direkter Zugriff auf die Trackposition. Eine Umdrehung ist ein
            # Beat, folgt also dem Tempo des Tracks. Ohne Beatgrid gibt es
            # keine Beats - dann bleibt es bei der Plattenlaenge eines
            # Vinyl-Tellers.
            beat_s = self._loop.beat_seconds(state.position_s)
            offset = revolutions * (beat_s or JOG_SECONDS_PER_REV)
        else:
            # Pitch Bend am Rand: kleiner Positionsversatz. Wird spaeter
            # durch eine echte Geschwindigkeitsrampe ersetzt.
            offset = revolutions * JOG_BEND_SECONDS_PER_REV

        if state.direction in (Direction.REV, Direction.SLIP_REV):
            # "Die Bedienung des Jog-Wheels erfolgt ebenfalls auf umgekehrte
            # Weise" (Handbuch S. 47). Betroffen ist nur der Transport -
            # Scratch, Frame Search und Pitch Bend. Die Loop-Feineinstellung
            # und die Suche mit gehaltenem SEARCH bleiben richtungstreu;
            # beide bearbeiten bzw. navigieren, sie spielen nicht ab.
            offset = -offset

        if self.playback.is_ready:
            # Relativ verschieben, damit die laufende Wiedergabe nicht
            # stockt - die Ausgabe rechnet sample-genau weiter.
            self.playback.nudge_seconds(offset)
            self._update(position_s=self.playback.position_seconds)
            return
        self._seek(state.position_s + offset, wrap_loop=True)

    def _cmd_jog_mode_toggle(self, cmd: DeckCommand) -> None:
        """VINYL <-> CDJ.

        Ein reiner Moduswechsel: er haelt die Wiedergabe nicht an, aendert
        weder Tempo noch Loop und setzt ``loop.active`` nicht zurueck
        (Abschnitte 32/33). Betroffen ist allein, was das Jogwheel bewirkt.

        Wird die Platte dabei gerade gehalten, endet bzw. beginnt die
        Scratch-Slip-Aktion - im CDJ-Modus gibt es kein Scratchen.
        """
        mode = (
            JogMode.CDJ
            if self._state.jog_mode is JogMode.VINYL
            else JogMode.VINYL
        )
        self._update(jog_mode=mode)
        if mode is JogMode.CDJ:
            self._slip_end(SlipReason.SCRATCH)
        elif self._state.jog_touch:
            self._slip_begin(SlipReason.SCRATCH)

    def _cmd_vinyl_speed_adjust(self, cmd: DeckCommand) -> None:
        self._update(vinyl_speed_adjust=float(cmd.get("value", 0.5)))

    # -- Rest -------------------------------------------------------------

    def _cmd_noop(self, cmd: DeckCommand) -> None:
        """Kommandos, die nicht das Deck betreffen (Browser, Ansicht, USB)."""


def _nearest(values: tuple[float, ...], value: float) -> float:
    """Naechstgelegener Wert der Reihe.

    Ein Kommando mit einem Wert, den es am Geraet nicht gibt, soll nicht
    stillschweigend eine eigene Stufe aufmachen.
    """
    return min(values, key=lambda candidate: abs(candidate - value))


def _speed(state: DeckState) -> float:
    """Wiedergabegeschwindigkeit als Faktor."""
    if state.tempo_reset:
        return 1.0
    return 1.0 + state.tempo_percent / 100.0


_HANDLERS: dict[CommandType, Callable[[Deck, DeckCommand], None]] = {
    CommandType.PLAY_PAUSE: Deck._cmd_play_pause,
    CommandType.CUE: Deck._cmd_cue,
    CommandType.SEEK: Deck._cmd_seek,
    CommandType.SEARCH: Deck._cmd_search,
    CommandType.TRACK_SEARCH: Deck._cmd_track_search,
    CommandType.DIRECTION: Deck._cmd_direction,
    CommandType.PAD: Deck._cmd_pad,
    CommandType.PAD_MODE: Deck._cmd_pad_mode,
    CommandType.DELETE: Deck._cmd_delete,
    CommandType.MEMORY: Deck._cmd_memory,
    CommandType.CUE_LOOP_CALL: Deck._cmd_cue_loop_call,
    CommandType.LOOP_IN: Deck._cmd_loop_in,
    CommandType.LOOP_OUT: Deck._cmd_loop_out,
    CommandType.RELOOP_EXIT: Deck._cmd_reloop_exit,
    CommandType.BEAT_LOOP: Deck._cmd_beat_loop,
    CommandType.LOOP_HALVE: Deck._cmd_loop_halve,
    CommandType.LOOP_DOUBLE: Deck._cmd_loop_double,
    CommandType.BEAT_JUMP: Deck._cmd_beat_jump,
    CommandType.TEMPO_SET: Deck._cmd_tempo_set,
    CommandType.TEMPO_RANGE_CYCLE: Deck._cmd_tempo_range_cycle,
    CommandType.MASTER_TEMPO_TOGGLE: Deck._cmd_master_tempo_toggle,
    CommandType.TEMPO_RESET_TOGGLE: Deck._cmd_tempo_reset_toggle,
    CommandType.SYNC_TOGGLE: Deck._cmd_sync_toggle,
    CommandType.MASTER_SET: Deck._cmd_master_set,
    CommandType.KEY_SYNC: Deck._cmd_key_sync,
    CommandType.KEY_SHIFT: Deck._cmd_key_shift,
    CommandType.KEY_SHIFT_RESET: Deck._cmd_key_shift_reset,
    CommandType.BEATGRID_SHIFT: Deck._cmd_beatgrid_shift,
    CommandType.BEATGRID_RESET: Deck._cmd_beatgrid_reset,
    CommandType.QUANTIZE_TOGGLE: Deck._cmd_quantize_toggle,
    CommandType.QUANTIZE_BEATS: Deck._cmd_quantize_beats,
    CommandType.SLIP_TOGGLE: Deck._cmd_slip_toggle,
    CommandType.JOG_MOVE: Deck._cmd_jog_move,
    CommandType.JOG_TOUCH: Deck._cmd_jog_touch,
    CommandType.JOG_MODE_TOGGLE: Deck._cmd_jog_mode_toggle,
    CommandType.VINYL_SPEED_ADJUST: Deck._cmd_vinyl_speed_adjust,
    CommandType.BROWSE_ROTATE: Deck._cmd_noop,
    CommandType.BROWSE_PRESS: Deck._cmd_noop,
    CommandType.VIEW: Deck._cmd_noop,
    CommandType.BACK: Deck._cmd_noop,
    CommandType.LOAD: Deck._cmd_noop,
    CommandType.OPEN_SETTINGS: Deck._cmd_noop,
    CommandType.USB_STOP: Deck._cmd_noop,
    # Reine Anzeigebefehle: der Bildschirm behandelt sie selbst. Falls
    # sie doch hier ankommen, aendern sie bewusst nichts.
    CommandType.PANEL: Deck._cmd_noop,
    CommandType.TIME_MODE: Deck._cmd_noop,
    CommandType.WAVEFORM_ZOOM: Deck._cmd_noop,
    CommandType.WAVEFORM_MODE: Deck._cmd_noop,
    CommandType.HEADER_TOGGLE: Deck._cmd_noop,
    CommandType.NAV_SELECT: Deck._cmd_noop,
    CommandType.NAV_ENTER: Deck._cmd_noop,
    CommandType.NAV_TOP: Deck._cmd_noop,
    CommandType.BROWSE_SORT: Deck._cmd_noop,
    CommandType.BROWSE_TOGGLE: Deck._cmd_noop,
    CommandType.JUMP_MODE: Deck._cmd_noop,
    CommandType.ROTARY_MODE: Deck._cmd_noop,
    CommandType.NEEDLE_LOCK: Deck._cmd_noop,
}
