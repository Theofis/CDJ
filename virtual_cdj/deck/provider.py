"""Zustandsquelle fuer die CDJ-Oberflaeche.

Die Oberflaeche spricht **nie** direkt mit einer ``Deck``-Instanz. Sie kennt
nur diese Schnittstelle. Dadurch ist es spaeter moeglich, dass ein CDJ-Display
auf einem anderen Rechner laeuft als die Deck-Engine, ohne dass an der
Oberflaeche etwas geaendert werden muss.

    CDJ-Oberflaeche
        -> DeckStateProvider   (diese Schnittstelle)
            -> LocalDeckStateProvider   -> Deck im selben Prozess
            -> NetworkDeckStateProvider -> Deck auf einem anderen Rechner
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from .commands import DeckCommand
from .engine import Deck
from .state import DeckState, empty_state

Listener = Callable[[DeckState], None]


class DeckStateProvider(ABC):
    """Liefert den Zustand eines Decks und nimmt Kommandos entgegen."""

    @property
    @abstractmethod
    def deck_id(self) -> int:
        ...

    @abstractmethod
    def get_state(self) -> DeckState:
        """Aktueller Zustand. Muss guenstig aufrufbar sein."""

    @abstractmethod
    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Bei Zustandsaenderung benachrichtigen. Rueckgabe: Abmeldefunktion."""

    @abstractmethod
    def send(self, cmd: DeckCommand) -> None:
        """Kommando an das Deck schicken."""

    def tick(self) -> None:
        """Regelmaessiger Aufruf durch die Anwendung. Optional."""


class LocalDeckStateProvider(DeckStateProvider):
    """Deck im selben Prozess."""

    def __init__(self, deck: Deck) -> None:
        self._deck = deck

    @property
    def deck_id(self) -> int:
        return self._deck.deck_id

    @property
    def deck(self) -> Deck:
        """Direktzugriff - nur fuer Anwendung und Tests, nicht fuer die GUI."""
        return self._deck

    def get_state(self) -> DeckState:
        return self._deck.state

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        return self._deck.subscribe(listener)

    def send(self, cmd: DeckCommand) -> None:
        self._deck.execute(cmd)

    def tick(self) -> None:
        self._deck.tick()


class NetworkDeckStateProvider(DeckStateProvider):
    """Deck auf einem anderen Rechner.

    Vorbereitet, aber noch ohne Transport. Sobald das Protokoll steht, wird
    hier serialisiert und versendet - die Oberflaeche bleibt unveraendert.
    """

    def __init__(self, deck_id: int, endpoint: str) -> None:
        self._deck_id = deck_id
        self.endpoint = endpoint
        self._state = empty_state(deck_id)
        self._listeners: list[Listener] = []

    @property
    def deck_id(self) -> int:
        return self._deck_id

    def get_state(self) -> DeckState:
        return self._state

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def send(self, cmd: DeckCommand) -> None:
        raise NotImplementedError(
            "Netzwerktransport ist noch nicht implementiert. "
            "Bis dahin LocalDeckStateProvider verwenden."
        )

    def on_state_received(self, state: DeckState) -> None:
        """Vom Transport aufzurufen, sobald ein Zustand eintrifft."""
        self._state = state
        for listener in list(self._listeners):
            listener(state)
