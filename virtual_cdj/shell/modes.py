"""Anwendungsmodus - welche Ebene der Oberflaeche gerade gilt.

Ein einziger Wert entscheidet, was der Bildschirm zeigt **und** was die
Hardware ausloest:

    PERFORMANCE   normale DJ-Oberflaeche (Standard beim Start)
    MENU          Auswahl der Bereiche
    TEST          Hardwarediagnose, keine DJ-Funktionen
    CALIBRATION   Sensoren einstellen und speichern
    SETTINGS      Einstellungen

Der Modus wird hier gefuehrt, nicht in einer GUI-Seite. Seiten und
Eingabeverteilung haengen sich als Abonnenten an - so kann eine weitere
Seite ergaenzt werden, ohne dass eine bestehende davon weiss.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class ApplicationMode(str, Enum):
    """Die Ebenen der Oberflaeche."""

    PERFORMANCE = "PERFORMANCE"
    MENU = "MENU"
    TEST = "TEST"
    CALIBRATION = "CALIBRATION"
    SETTINGS = "SETTINGS"

    @property
    def is_performance(self) -> bool:
        return self is ApplicationMode.PERFORMANCE

    @property
    def controls_deck(self) -> bool:
        """Ob Hardware-Eingaben in diesem Modus DJ-Funktionen ausloesen.

        Nur im Performance-Modus. Damit springt beim Pruefen eines
        Hotcue-Tasters kein Track.
        """
        return self is ApplicationMode.PERFORMANCE


@dataclass(frozen=True)
class MenuEntry:
    """Ein Eintrag im Menue."""

    mode: ApplicationMode
    label: str
    detail: str = ""


#: Die Bereiche des Menues, in dieser Reihenfolge.
MENU_ENTRIES: tuple[MenuEntry, ...] = (
    MenuEntry(
        ApplicationMode.PERFORMANCE, "PERFORMANCE",
        "Wellenform, Browse, Loop - die normale Bedienung",
    ),
    MenuEntry(
        ApplicationMode.TEST, "PRUEFUNG / TEST",
        "Alle Ein- und Ausgaenge live pruefen, ohne DJ-Funktion",
    ),
    MenuEntry(
        ApplicationMode.CALIBRATION, "KALIBRIERUNG",
        "Touch-Sensor, Jogwheel, Fader und Potis einstellen",
    ),
    MenuEntry(
        ApplicationMode.SETTINGS, "EINSTELLUNGEN",
        "Anzeige, Audio und gespeicherte Werte",
    ),
)

ModeListener = Callable[[ApplicationMode], None]


class ModeController:
    """Haelt den Anwendungsmodus und meldet jeden Wechsel.

    Die einzige Wahrheit darueber, in welcher Ebene die Anwendung ist.
    """

    def __init__(
        self, mode: ApplicationMode = ApplicationMode.PERFORMANCE
    ) -> None:
        self._mode = mode
        #: Modus, aus dem ins Menue gewechselt wurde - fuer "zurueck".
        self._previous = mode
        self._listeners: list[ModeListener] = []

    # ------------------------------------------------------------------

    @property
    def mode(self) -> ApplicationMode:
        return self._mode

    @property
    def previous(self) -> ApplicationMode:
        return self._previous

    def subscribe(self, listener: ModeListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    # ------------------------------------------------------------------

    def set_mode(self, mode: ApplicationMode) -> ApplicationMode:
        """Modus setzen. Derselbe Modus erneut aendert nichts."""
        if mode is self._mode:
            return self._mode
        self._previous = self._mode
        self._mode = mode
        for listener in list(self._listeners):
            listener(mode)
        return self._mode

    def toggle_menu(self) -> ApplicationMode:
        """Menue oeffnen oder zum vorherigen Bereich zurueck."""
        if self._mode is ApplicationMode.MENU:
            return self.set_mode(
                self._previous
                if self._previous is not ApplicationMode.MENU
                else ApplicationMode.PERFORMANCE
            )
        return self.set_mode(ApplicationMode.MENU)

    def to_performance(self) -> ApplicationMode:
        return self.set_mode(ApplicationMode.PERFORMANCE)

    @property
    def controls_deck(self) -> bool:
        return self._mode.controls_deck
