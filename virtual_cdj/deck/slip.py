"""Slip-Engine: die Hintergrund-Zeitachse eines Decks.

Was Slip ist
------------
Bei eingeschaltetem SLIP veraendern bestimmte **voruebergehende** Aktionen
zwar hoerbar die Wiedergabe, die normale Trackposition laeuft aber
unsichtbar weiter. Endet die Aktion, springt die Wiedergabe dorthin, wo der
Track ohne den Eingriff inzwischen waere.

```
hoerbar   30.0 --scratch--> irgendwo
Hintergrund 30.0 -> 31.0 -> 32.0 -> 33.0
loslassen                              -> Wiedergabe bei 33.0
```

Was hier steht
--------------
Eine reine Rechnung:

    (Slip-Zustand, Grund, Position) -> neuer Slip-Zustand

Kein Transport, kein Audio, keine Eingabe. Es gibt **keinen zweiten
Player**: die Hintergrundposition ist eine einzelne Zahl, die
weitergezaehlt wird - die logische Position, an der die normale Wiedergabe
ohne den Eingriff waere. ``deck/engine.py`` fuettert sie und wertet ihr
Ergebnis aus.

Mehrere Gruende gleichzeitig
----------------------------
Slip-Aktionen koennen sich ueberlagern: ein Loop laeuft, und waehrend er
laeuft wird gescratcht. Deshalb ist ``SlipState.reasons`` eine **Menge**.
Die Wiedergabe springt erst zurueck, wenn der **letzte** Grund endet - sonst
risse das Loslassen des Jogwheels den noch laufenden Loop auseinander.

Getrennte Zustaende
-------------------
* ``DeckState.slip``       - der SLIP-Schalter (dauerhafte Einstellung).
* ``SlipState.active``     - gerade laeuft eine Slip-Aktion.
* ``SlipState.position_s`` - die Hintergrundposition dieser Aktion.

Keiner folgt aus dem anderen: SLIP kann eingeschaltet sein, ohne dass eine
Aktion laeuft.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class SlipReason(str, Enum):
    """Warum gerade eine Slip-Aktion laeuft.

    Es gibt **keinen** anonymen Weg in oder aus einer Slip-Aktion: Anfang
    und Ende nennen ihren Grund. Damit ist im Log und in der
    Entwicklungsanzeige sichtbar, warum die Wiedergabe gesprungen ist,
    statt "das Deck springt manchmal".
    """

    #: PLAY/PAUSE bei eingeschaltetem SLIP - hoerbar Pause, Zeit laeuft.
    PAUSE = "PAUSE"
    #: Platte im Vinyl-Modus beruehrt - Vinyl-Stop bzw. Scratch.
    SCRATCH = "SCRATCH"
    #: Ein Loop laeuft (auch ein Beatloop von einem Pad).
    LOOP = "LOOP"
    #: Hotcue gedrueckt gehalten.
    HOT_CUE = "HOT_CUE"
    #: Richtungsschalter auf REV bzw. SLIP REV.
    REVERSE = "REVERSE"


@dataclass(frozen=True)
class SlipState:
    """Zustand der Hintergrund-Zeitachse. Reine Daten."""

    #: Laufende Slip-Gruende. Leer heisst: keine Slip-Aktion.
    reasons: frozenset[SlipReason] = frozenset()
    #: Hintergrundposition in Sekunden. ``None`` heisst ungueltig - es gibt
    #: gerade keine Zeitachse, auf die zurueckgesprungen werden koennte.
    position_s: float | None = None

    @property
    def active(self) -> bool:
        """Ob gerade eine Slip-Aktion laeuft (``slipOperationActive``)."""
        return bool(self.reasons)

    def has(self, reason: SlipReason) -> bool:
        return reason in self.reasons

    def label(self) -> str:
        """Anzeigetext der laufenden Gruende, z. B. ``LOOP+SCRATCH``."""
        return "+".join(sorted(reason.value for reason in self.reasons))


@dataclass(frozen=True)
class SlipEngine:
    """Slip-Logik zu genau einem geladenen Track.

    ``duration_s`` begrenzt die Hintergrundposition. Ohne Laenge (``0``)
    wird nicht begrenzt - das Deck kennt dann selbst kein Trackende.
    """

    duration_s: float = 0.0

    def begin(
        self, slip: SlipState, reason: SlipReason, position_s: float
    ) -> SlipState:
        """Eine Slip-Aktion beginnen.

        Die Hintergrundposition wird **nur beim ersten** Grund gesetzt. Eine
        zweite, ueberlagerte Aktion darf die bereits laufende Zeitachse nicht
        auf ihre eigene Startposition zuruecksetzen - sonst ginge die im
        Hintergrund vergangene Zeit verloren.
        """
        if reason in slip.reasons:
            return slip
        if slip.active and slip.position_s is not None:
            return replace(slip, reasons=slip.reasons | {reason})
        return SlipState(
            reasons=slip.reasons | {reason},
            position_s=self._clamped(position_s),
        )

    def end(
        self, slip: SlipState, reason: SlipReason
    ) -> tuple[SlipState, float | None]:
        """Eine Slip-Aktion beenden.

        Returns:
            Neuer Zustand und die Position, auf die die Wiedergabe springen
            muss. ``None`` heisst: kein Sprung - entweder lief diese Aktion
            gar nicht, oder es laeuft noch eine andere.
        """
        if reason not in slip.reasons:
            return (slip, None)
        remaining = slip.reasons - {reason}
        if remaining:
            return (replace(slip, reasons=remaining), None)
        return (SlipState(), slip.position_s)

    def advance(self, slip: SlipState, seconds: float) -> SlipState:
        """Hintergrund-Zeitachse um ``seconds`` weiterschieben.

        Immer vorwaerts: die Zeitachse beschreibt die normale Wiedergabe,
        und die laeuft auch dann vorwaerts, wenn hoerbar rueckwaerts
        gespielt oder gescratcht wird.
        """
        if not slip.active or slip.position_s is None or seconds <= 0:
            return slip
        return replace(slip, position_s=self._clamped(slip.position_s + seconds))

    def reset(self) -> SlipState:
        """Zeitachse verwerfen - neuer Track, Auswurf, SLIP ausgeschaltet."""
        return SlipState()

    def _clamped(self, position_s: float) -> float:
        value = max(0.0, position_s)
        if self.duration_s > 0:
            return min(value, self.duration_s)
        return value
