"""Loop-Engine: die vollstaendige Loop-Logik eines Decks.

Was hier steht
--------------
Quantisierung auf das Beatgrid, manuelles Loop In/Out, Loop-Laenge halbieren
und verdoppeln (CALL < / CALL >), die Beatloops der Pads A-H, das Verschieben
der Loop-Punkte mit dem Jogwheel, Exit/Reloop und die Loop-Grenze bei der
Wiedergabe.

Was hier **nicht** steht
------------------------
Transport, Audio, Eingabe. Die Engine ist eine reine Rechnung:

    (Loop-Zustand, Beatgrid, Position) -> neuer Loop-Zustand

Sie veraendert nichts, sie liefert Ergebnisse. Damit ist jeder Schritt
einzeln pruefbar, ohne Deck, ohne Audioausgabe und ohne Fenster. Die
Deck-Engine ruft sie auf und haengt das Ergebnis in den ``DeckState``; die
sample-genaue Loop-Grenze im Ton macht ``audio/engine.py``.

Getrennte Zustaende
-------------------
* ``LoopState.active``  - der Loop laeuft.
* ``LoopState.adjust``  - welcher Punkt am Jogwheel haengt (NONE / IN / OUT).
* ``DeckState.pad_mode`` - ob die Pads A-H Beatloops ausloesen.
* ``DeckState.quantize`` - ob neue Loop-Punkte auf das Beatgrid rasten.

Keiner der vier folgt aus einem anderen.

Gerastert wird nicht hier, sondern in ``deck/quantize.py`` - derselben
Stelle, die auch Cue und Hotcue benutzen. Die Weite steht in
``DeckState.quantize_beats`` (1/8, 1/4, 1/2, 1 Beat).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .quantize import DEFAULT_QUANTIZE_BEATS, quantize_position
from .state import (
    BEAT_LOOP_LENGTHS,
    BeatGrid,
    LoopAdjust,
    LoopExitReason,
    LoopState,
)

#: Grenzen der Loop-Laenge in Beats. Kuerzer als 1/64 Beat ist am Tempo
#: nicht mehr hoerbar, laenger als 64 Beats kein Loop mehr.
MIN_LOOP_BEATS = 1.0 / 64.0
MAX_LOOP_BEATS = 64.0

#: Laenge, die CALL < bzw. CALL > ohne laufenden Loop erzeugt.
CALL_LEFT_BEATS = 4.0
CALL_RIGHT_BEATS = 8.0

#: Toleranz beim Vergleich von Zeiten in Sekunden.
EPSILON = 1e-9


@dataclass(frozen=True)
class LoopEngine:
    """Loop-Logik zu genau einem Beatgrid.

    ``quantize`` ist der Zustand des QUANTIZE-Schalters (``DeckState``):

    * ``True``  - neue Loop-Punkte rasten auf das naechste Beatgrid.
    * ``False`` - neue Loop-Punkte liegen exakt auf der Wiedergabeposition.

    Ohne gueltiges Beatgrid (``grid is None`` oder leeres Raster) wird nicht
    quantisiert und es entsteht kein Beatloop - beides braucht Beats. Die
    Engine erfindet dafuer kein Tempo; der Aufrufer vermerkt den Fall.
    """

    grid: BeatGrid | None = None
    quantize: bool = False
    #: Rasterweite in Beats (``DeckState.quantize_beats``). Gilt nur, wenn
    #: ``quantize`` an ist.
    beat_value: float = DEFAULT_QUANTIZE_BEATS

    # ------------------------------------------------------------------
    # Quantisierung
    # ------------------------------------------------------------------

    @property
    def has_grid(self) -> bool:
        return self.grid is not None and self.grid.is_valid

    def nearest_beatgrid(self, position_s: float) -> float:
        """Naechstgelegener Rasterpunkt der eingestellten Weite.

        Gerechnet wird in ``deck/quantize.py`` - derselben Stelle, durch die
        auch Cue und Hotcue gehen. Ohne Beatgrid bleibt die Position
        unveraendert; eine erfundene Rasterung waere schlechter als keine.
        """
        return quantize_position(position_s, self.grid, self.beat_value)

    def position_for(self, position_s: float) -> float:
        """Wo ein neuer Loop-Punkt entsteht.

        Die eine Stelle, an der ueber QUANTIZE entschieden wird - benutzt
        von LOOP IN, LOOP OUT, CALL < / CALL > und den Beatloop-Pads. Das
        Verschieben mit dem Jogwheel geht bewusst **nicht** hier durch.
        """
        if not self.quantize:
            return position_s
        return self.nearest_beatgrid(position_s)

    def beat_seconds(self, at_s: float = 0.0) -> float:
        """Laenge eines Beats an dieser Stelle. ``0.0`` ohne Beatgrid."""
        if self.grid is None or not self.has_grid:
            return 0.0
        return max(0.0, self.grid.seconds_for_beats(1.0, at_s))

    def seconds_for_beats(self, beats: float, at_s: float) -> float:
        """Laenge von ``beats`` Beats ab ``at_s``.

        Ganze Beats folgen den echten Beatzeiten und halten damit auch bei
        wechselndem Tempo; Bruchteile werden aus der oertlichen Beatlaenge
        gerechnet. ``BeatGrid.seconds_for_beats`` allein rundet Bruchteile
        auf ganze Beats und wuerde bei 1/4 Beat 0 liefern.
        """
        if self.grid is None or not self.has_grid or beats <= 0:
            return 0.0
        whole = math.floor(beats + EPSILON)
        rest = beats - whole
        length = 0.0
        if whole >= 1:
            length = self.grid.seconds_for_beats(float(whole), at_s)
        if rest > EPSILON:
            length += self.beat_seconds(at_s + length) * rest
        return length

    def beats_for(self, length_s: float, at_s: float) -> float | None:
        """Laenge in Beats, falls sie sauber im Raster liegt.

        Nur Vielfache von 1/64 Beat gelten als sauber. Alles andere - etwa
        ein von Hand gezogener Loop - ergibt ``None``: dann zeigt die
        Oberflaeche keine Beat-Laenge an, statt eine falsche zu zeigen.
        """
        beat = self.beat_seconds(at_s)
        if beat <= 0 or length_s <= 0:
            return None
        value = length_s / beat
        rounded = round(value * 64.0) / 64.0
        if rounded <= 0 or abs(value - rounded) > 1e-6:
            return None
        return rounded

    def _limits(self, at_s: float) -> tuple[float, float]:
        """Kuerzeste und laengste erlaubte Loop-Laenge in Sekunden."""
        beat = self.beat_seconds(at_s)
        if beat <= 0:
            return (EPSILON, math.inf)
        return (MIN_LOOP_BEATS * beat, MAX_LOOP_BEATS * beat)

    # ------------------------------------------------------------------
    # Manueller Loop: LOOP IN / LOOP OUT
    # ------------------------------------------------------------------

    def loop_in_pressed(
        self, loop: LoopState, position_s: float
    ) -> LoopState:
        """LOOP IN.

        Ohne laufenden Loop wird der Loop-Anfang gesetzt: mit QUANTIZE auf
        den naechsten Beat, ohne QUANTIZE genau auf die Wiedergabeposition.
        Bei laufendem Loop schaltet die Taste den IN-Adjust-Modus ein;
        erneutes Druecken beendet ihn und sichert den Loop.
        """
        if loop.active:
            return self._toggled_adjust(loop, LoopAdjust.IN)
        return replace(
            loop,
            in_s=self.position_for(position_s),
            out_s=None,
            beats=None,
            adjust=LoopAdjust.NONE,
            pad=None,
        )

    def loop_out_pressed(
        self, loop: LoopState, position_s: float
    ) -> LoopState:
        """LOOP OUT.

        Ohne laufenden Loop wird das Loop-Ende gesetzt - mit QUANTIZE auf
        den naechsten Beat, sonst genau auf die Wiedergabeposition - und der
        Loop startet, sofern es hinter dem Anfang liegt. Bei laufendem Loop
        schaltet die Taste den OUT-Adjust-Modus ein.
        """
        if loop.active:
            return self._toggled_adjust(loop, LoopAdjust.OUT)
        if loop.in_s is None:
            return loop
        out_s = self.position_for(position_s)
        if out_s <= loop.in_s + EPSILON:
            return loop
        return self._saved(
            replace(
                loop,
                active=True,
                exit_reason=None,
                out_s=out_s,
                beats=self.beats_for(out_s - loop.in_s, loop.in_s),
                adjust=LoopAdjust.NONE,
                pad=None,
            )
        )

    def _toggled_adjust(
        self, loop: LoopState, mode: LoopAdjust
    ) -> LoopState:
        """Adjust-Modus ein- oder ausschalten.

        Dieselbe Taste erneut beendet das Anpassen und sichert den Loop -
        deshalb wird waehrend des Drehens nicht gesichert.
        """
        if loop.adjust is mode:
            return self._saved(replace(loop, adjust=LoopAdjust.NONE))
        return replace(loop, adjust=mode)

    # ------------------------------------------------------------------
    # Loop-Laenge: CALL < und CALL >
    # ------------------------------------------------------------------

    def call_left_pressed(
        self, loop: LoopState, position_s: float
    ) -> LoopState:
        """CALL <: ohne Loop ein 4-Beat-Loop, sonst halbe Laenge."""
        if loop.active:
            return self.scaled(loop, 0.5)
        return self.beat_loop(loop, CALL_LEFT_BEATS, position_s)

    def call_right_pressed(
        self, loop: LoopState, position_s: float
    ) -> LoopState:
        """CALL >: ohne Loop ein 8-Beat-Loop, sonst doppelte Laenge."""
        if loop.active:
            return self.scaled(loop, 2.0)
        return self.beat_loop(loop, CALL_RIGHT_BEATS, position_s)

    def scaled(self, loop: LoopState, factor: float) -> LoopState:
        """Loop-Laenge um ``factor`` aendern. Der Anfang bleibt stehen.

        Ausserhalb von 1/64 bis 64 Beats bleibt der Loop, wie er ist.
        """
        if not loop.is_set or factor <= 0:
            return loop
        in_s = loop.in_s
        assert in_s is not None  # durch is_set gesichert
        length = loop.length_s * factor
        minimum, maximum = self._limits(in_s)
        if length < minimum - EPSILON or length > maximum + EPSILON:
            return loop
        return self._saved(
            replace(
                loop,
                out_s=in_s + length,
                beats=self.beats_for(length, in_s),
                # Nach dem Skalieren passt die Laenge nicht mehr zum Pad,
                # das den Loop gesetzt hat - das Pad-Gedaechtnis faellt weg.
                pad=None,
            )
        )

    # ------------------------------------------------------------------
    # Beatloop
    # ------------------------------------------------------------------

    def beat_loop_pad_pressed(
        self, loop: LoopState, pad: int, position_s: float
    ) -> LoopState:
        """Pad A-H im Beatloop-Modus.

        **Dasselbe** Pad bei laufendem Loop verlaesst ihn, ein anderes
        aendert nur die Laenge, ohne Loop entsteht ein neuer.

        "Dasselbe Pad" heisst: der laufende Loop wurde wirklich mit diesem
        Pad gesetzt (``LoopState.pad``). Frueher wurde das aus der
        Loop-Laenge abgeleitet - dann verliess ein Druck auf Pad E (4 Beats)
        auch einen 4-Beat-Loop, der mit CALL < oder von Hand entstanden war.
        Der Loop verschwand also auf einen Tastendruck, der ihn haette
        setzen sollen.
        """
        if not 0 <= pad < len(BEAT_LOOP_LENGTHS):
            return loop
        if loop.active and loop.pad == pad:
            return self.exit_loop(loop, LoopExitReason.BEAT_LOOP_PAD)
        return self.beat_loop(loop, BEAT_LOOP_LENGTHS[pad], position_s, pad=pad)

    def beat_loop(
        self,
        loop: LoopState,
        beats: float,
        position_s: float,
        *,
        pad: int | None = None,
    ) -> LoopState:
        """Loop mit fester Beat-Laenge setzen.

        Laeuft bereits ein Loop, bleibt der Anfang stehen und nur das Ende
        wandert - sonst beginnt der Loop an der Wiedergabeposition, mit
        QUANTIZE auf dem naechsten Beat. Gilt fuer die Beatloop-Pads, das
        Touch-Panel und CALL < / CALL >.

        ``pad`` merkt sich, von welchem Beatloop-Pad der Loop kam. Ohne
        Angabe - also bei CALL, Touch-Panel oder Hotcue - wird das
        Pad-Gedaechtnis geloescht, damit ein spaeterer Pad-Druck den Loop
        setzt statt ihn zu verlassen.
        """
        if not self.has_grid or beats <= 0:
            return loop
        keep_start = loop.active and loop.is_set
        start = loop.in_s if keep_start else self.position_for(position_s)
        assert start is not None
        length = self.seconds_for_beats(beats, start)
        if length <= 0:
            return loop
        return self._saved(
            replace(
                loop,
                active=True,
                exit_reason=None,
                in_s=start,
                out_s=start + length,
                beats=beats,
                adjust=LoopAdjust.NONE,
                pad=pad,
            )
        )

    def activate(
        self, loop: LoopState, in_s: float, out_s: float
    ) -> LoopState:
        """Fertige Loop-Punkte uebernehmen - etwa aus einem Loop-Hotcue."""
        if out_s <= in_s:
            return loop
        return self._saved(
            replace(
                loop,
                active=True,
                exit_reason=None,
                in_s=in_s,
                out_s=out_s,
                beats=self.beats_for(out_s - in_s, in_s),
                adjust=LoopAdjust.NONE,
                pad=None,
            )
        )

    # ------------------------------------------------------------------
    # Jogwheel im Adjust-Modus
    # ------------------------------------------------------------------

    def adjust_with_jog(
        self, loop: LoopState, revolutions: float
    ) -> LoopState:
        """Loop-Punkt verschieben. Eine Umdrehung entspricht einem Beat.

        Ohne Adjust-Modus passiert nichts. Verschoben wird **ohne**
        Quantisierung - das ist der Sinn der Feineinstellung. Gesichert wird
        erst am Ende des Adjusts, nicht bei jedem Schritt.

        An den Grenzen (1/64 bis 64 Beats) wird **begrenzt, nicht
        abgewiesen**: der Punkt wandert bis zur Grenze und bleibt dort
        stehen. Frueher wurde ein Schritt, der die Grenze ueberschritten
        haette, komplett verworfen - bei einem kurzen Loop bewegte sich
        dann gar nichts mehr, und weil der Jog im Adjust-Modus ohnehin
        nicht mehr auf die Wiedergabe wirkt, wirkte das Geraet wie
        eingefroren.
        """
        if loop.adjust is LoopAdjust.NONE or not loop.is_set:
            return loop
        if abs(revolutions) < EPSILON:
            return loop
        in_s, out_s = loop.in_s, loop.out_s
        assert in_s is not None and out_s is not None

        current = out_s - in_s

        if loop.adjust is LoopAdjust.IN:
            moved = max(0.0, in_s + self.beat_seconds(in_s) * revolutions)
            minimum, maximum = self._band(moved, current)
            # Der Anfang darf hoechstens so weit nach rechts, dass die
            # kuerzeste Laenge bleibt, und hoechstens so weit nach links,
            # dass die laengste nicht ueberschritten wird.
            moved = max(0.0, min(max(moved, out_s - maximum), out_s - minimum))
            length = out_s - moved
            if length <= 0:
                return loop
            return replace(
                loop, in_s=moved, beats=self.beats_for(length, moved)
            )

        moved = out_s + self.beat_seconds(out_s) * revolutions
        minimum, maximum = self._band(in_s, current)
        moved = min(max(moved, in_s + minimum), in_s + maximum)
        length = moved - in_s
        if length <= 0:
            return loop
        return replace(
            loop, out_s=moved, beats=self.beats_for(length, in_s)
        )

    def ended_adjust(self, loop: LoopState) -> LoopState:
        """Adjust-Modus beenden und den Loop sichern.

        Dasselbe Ergebnis wie ein erneuter Druck auf dieselbe Adjust-Taste.
        Eigene Methode, weil es einen zweiten Ausloeser gibt: die
        10-Sekunden-Automatik des Geraets (Handbuch S. 58).
        """
        if loop.adjust is LoopAdjust.NONE:
            return loop
        return self._saved(replace(loop, adjust=LoopAdjust.NONE))

    # ------------------------------------------------------------------
    # Loop Move
    # ------------------------------------------------------------------

    def moved(self, loop: LoopState, beats: float) -> tuple[LoopState, float]:
        """Ganzen Loop um ``beats`` Beats verschieben (Handbuch S. 66/67).

        Anfang und Ende wandern gemeinsam, die Laenge bleibt. Deshalb
        bleiben auch ``beats`` und das Pad-Gedaechtnis stehen: es ist
        derselbe Loop, nur an einer anderen Stelle.

        Returns:
            Neuer Loop-Zustand und der Versatz in Sekunden. Der Aufrufer
            verschiebt die Wiedergabe um denselben Betrag - sonst stuende
            sie nach dem Verschieben ausserhalb des Loops.

        Gerechnet wird mit der Beatlaenge am **Loop-Anfang**. Bei
        wechselndem Tempo ist das eine Naeherung; ein beatweiser Versatz
        ueber eine Tempogrenze hinweg hat keine eindeutige Laenge.

        Ein Versatz, der den Anfang vor den Trackbeginn schoebe, wird
        verworfen - der Loop bleibt, wo er ist.
        """
        if not loop.is_set or abs(beats) < EPSILON:
            return (loop, 0.0)
        in_s, out_s = loop.in_s, loop.out_s
        assert in_s is not None and out_s is not None
        offset = self.seconds_for_beats(abs(beats), in_s)
        if offset <= 0:
            return (loop, 0.0)
        if beats < 0:
            offset = -offset
        if in_s + offset < 0:
            return (loop, 0.0)
        return (
            self._saved(
                replace(loop, in_s=in_s + offset, out_s=out_s + offset)
            ),
            offset,
        )

    def _band(self, at_s: float, current_length_s: float) -> tuple[float, float]:
        """Erlaubte Laengen beim Verschieben - inklusive der aktuellen.

        Ein von Hand gezogener Loop darf laenger als 64 Beats sein; LOOP
        IN/OUT kennen die Grenzen bewusst nicht. Beim Verschieben darf ein
        solcher Loop dann aber nicht schlagartig auf 64 Beats
        zusammengestaucht werden - die Grenze verhindert nur, dass er
        **weiter** aus dem Rahmen laeuft. Dasselbe umgekehrt fuer zu kurze.
        """
        minimum, maximum = self._limits(at_s)
        return (min(minimum, current_length_s), max(maximum, current_length_s))

    # ------------------------------------------------------------------
    # Exit / Reloop
    # ------------------------------------------------------------------

    def exit_reloop_pressed(
        self, loop: LoopState, position_s: float
    ) -> tuple[LoopState, float | None]:
        """RELOOP/EXIT - eine Taste, zwei Funktionen.

        Returns:
            Neuer Loop-Zustand und die Position, auf die gesprungen werden
            muss. ``None`` heisst: kein Sprung.
        """
        if loop.active:
            return (self.exit_loop(loop, LoopExitReason.RELOOP_EXIT), None)
        return self.reloop(loop, position_s)

    def exit_loop(
        self, loop: LoopState, reason: LoopExitReason
    ) -> LoopState:
        """Loop verlassen. Kein Sprung - der Track laeuft weiter.

        ``reason`` ist Pflicht. Es gibt damit keinen anonymen Weg aus einem
        Loop: jeder Wechsel von ``active = True`` auf ``False`` traegt
        seinen Grund mit sich und ist im Log und in der
        Entwicklungsanzeige sichtbar.
        """
        return replace(
            self._saved(loop),
            active=False,
            adjust=LoopAdjust.NONE,
            pad=None,
            exit_reason=reason,
        )

    def reloop(
        self, loop: LoopState, position_s: float
    ) -> tuple[LoopState, float | None]:
        """Zuletzt gespeicherten Loop wieder aktivieren.

        Liegt die Wiedergabe noch im alten Loop, bleibt sie stehen; sonst
        springt sie an dessen Anfang. Ohne gespeicherten Loop passiert
        nichts.
        """
        if not loop.has_last:
            return (loop, None)
        in_s, out_s = loop.last_in_s, loop.last_out_s
        assert in_s is not None and out_s is not None
        restored = replace(
            loop,
            active=True,
            # Der Loop laeuft wieder - der Grund der letzten Beendigung
            # gilt nicht mehr.
            exit_reason=None,
            in_s=in_s,
            out_s=out_s,
            beats=loop.last_beats,
            adjust=LoopAdjust.NONE,
            pad=None,
        )
        if in_s <= position_s < out_s:
            return (restored, None)
        return (restored, in_s)

    def _saved(self, loop: LoopState) -> LoopState:
        """Aktuellen Loop als "letzten Loop" sichern (Grundlage fuer RELOOP).

        Bewusst nicht bei jedem Jog-Schritt, sondern beim Erzeugen, beim
        Aendern der Laenge, am Ende eines Adjusts und beim Verlassen.
        """
        if not loop.is_set:
            return loop
        return replace(
            loop,
            last_in_s=loop.in_s,
            last_out_s=loop.out_s,
            last_beats=loop.beats,
        )

    # ------------------------------------------------------------------
    # Wiedergabe
    # ------------------------------------------------------------------

    def check_boundary(self, loop: LoopState, position_s: float) -> float:
        """Position an der Loop-Grenze zurueckfalten.

        Am Loop-Ende geht es am Loop-Anfang weiter, und zwar mit dem
        Ueberhang - so bleibt das Timing ueber viele Durchlaeufe stehen.
        Vor dem Loop-Anfang (etwa nach einem Jog-Ruecklauf) wird auf den
        Anfang gezogen.

        Nur fuer den Transport ueber die Wanduhr. Mit Audioausgabe macht
        ``audio/engine.py`` dasselbe sample-genau.
        """
        if not loop.active or not loop.is_set:
            return position_s
        in_s, out_s = loop.in_s, loop.out_s
        assert in_s is not None and out_s is not None
        length = out_s - in_s
        if length <= 0:
            return position_s
        if position_s >= out_s:
            return in_s + (position_s - out_s) % length
        if position_s < in_s:
            return in_s
        return position_s
