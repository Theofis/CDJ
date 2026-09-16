"""Austauschbare Backends fuer einen gemeinsamen DeckController.

Die Backends erhalten ausschliesslich ``DeckCommand``-Absichten und liefern
``DeckState``. Weder GUI noch Hardwarepfad kennen ihre konkrete Klasse.

In diesem Entwicklungsschritt sind MIDI- und CDJ-Anbindung bewusst Mock-
Adapter: Beide protokollieren alle Befehle. Fuer einen aussagekraeftigen
gemeinsamen State verwenden sie die bestehende ``Deck``-Logik; das
``CdjBackend`` kann dabei die bereits vorhandene lokale Audioausgabe nutzen.
Ein Ethernet-Transport bzw. eine neue AudioEngine wird hier nicht erfunden.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable

from .commands import CommandType, DeckCommand, command
from .engine import Deck
from .state import DeckState, PadMode, TrackInfo

StateListener = Callable[[DeckState], None]
OutputSink = Callable[[str], None]
OutputSuppression = Callable[[DeckCommand], bool]
LibrarySource = Callable[[], object | None]
LoadHandler = Callable[[int, str], None]


class DeckBackend(ABC):
    """Gemeinsamer Vertrag fuer lokale und entfernte Deck-Backends."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Kurzer Modusname fuer State und Diagnose (``MIDI``/``CDJ``)."""

    @property
    @abstractmethod
    def deck_ids(self) -> tuple[int, ...]:
        ...

    @abstractmethod
    def start(self) -> None:
        """Ressourcen bzw. spaetere Verbindungen oeffnen."""

    @abstractmethod
    def stop(self) -> None:
        """Ressourcen und Verbindungen kontrolliert schliessen."""

    @abstractmethod
    def send(self, cmd: DeckCommand) -> None:
        """Eine gemeinsame Deck-Absicht verarbeiten."""

    @abstractmethod
    def get_state(self, deck_id: int) -> DeckState:
        ...

    @abstractmethod
    def subscribe(
        self, deck_id: int, listener: StateListener
    ) -> Callable[[], None]:
        ...

    @abstractmethod
    def get_library(self) -> object | None:
        ...

    def tick(self, deck_id: int | None = None) -> None:
        """Backend regelmaessig bedienen; Netzwerkimplementierungen pollen."""

    # Die sprechenden Operationen bilden die stabile API fuer Aufrufer, die
    # nicht bereits ein DeckCommand besitzen. Hardware/InputMapper duerfen
    # weiterhin den generischen Command-Weg verwenden.
    def play(self, deck_id: int) -> None:
        if not self.get_state(deck_id).is_playing:
            self.send(command(CommandType.PLAY_PAUSE, deck_id, "CONTROLLER"))

    def pause(self, deck_id: int) -> None:
        if self.get_state(deck_id).is_playing:
            self.send(command(CommandType.PLAY_PAUSE, deck_id, "CONTROLLER"))

    def cue(self, deck_id: int, *, pressed: bool = True) -> None:
        self.send(
            command(
                CommandType.CUE, deck_id, "CONTROLLER", pressed=pressed
            )
        )

    def jog(
        self,
        deck_id: int,
        delta: int,
        velocity: float,
        direction: str,
        touched: bool,
        *,
        ticks_per_rev: int = 800,
    ) -> None:
        self.send(
            command(
                CommandType.JOG_MOVE,
                deck_id,
                "CONTROLLER",
                delta=delta,
                velocity=velocity,
                direction=direction,
                touched=touched,
                ticks_per_rev=ticks_per_rev,
            )
        )

    def set_tempo(self, deck_id: int, value: float) -> None:
        self.send(
            command(
                CommandType.TEMPO_SET,
                deck_id,
                "CONTROLLER",
                value=value,
            )
        )

    def load_track(self, deck_id: int, track_id: str) -> None:
        self.send(
            command(
                CommandType.LOAD,
                deck_id,
                "CONTROLLER",
                track_id=track_id,
            )
        )

    def set_hotcue(self, deck_id: int, index: int) -> None:
        self.send(
            command(
                CommandType.PAD,
                deck_id,
                "CONTROLLER",
                index=index,
                pressed=True,
            )
        )

    def delete_hotcue(self, deck_id: int, index: int) -> None:
        self.send(
            command(
                CommandType.DELETE,
                deck_id,
                "CONTROLLER",
                pressed=True,
            )
        )
        self.send(
            command(
                CommandType.PAD,
                deck_id,
                "CONTROLLER",
                index=index,
                pressed=True,
            )
        )
        self.send(
            command(
                CommandType.DELETE,
                deck_id,
                "CONTROLLER",
                pressed=False,
            )
        )

    def loop_in(self, deck_id: int) -> None:
        self.send(command(CommandType.LOOP_IN, deck_id, "CONTROLLER"))

    def loop_out(self, deck_id: int) -> None:
        self.send(command(CommandType.LOOP_OUT, deck_id, "CONTROLLER"))

    def exit_loop(self, deck_id: int) -> None:
        self.send(command(CommandType.RELOOP_EXIT, deck_id, "CONTROLLER"))

    def beat_jump(self, deck_id: int, amount: float) -> None:
        self.send(
            command(
                CommandType.BEAT_JUMP,
                deck_id,
                "CONTROLLER",
                direction=-1 if amount < 0 else 1,
                beats=abs(amount),
            )
        )

    def get_track_state(self, deck_id: int) -> TrackInfo | None:
        return self.get_state(deck_id).track

    def get_playback_state(self, deck_id: int) -> DeckState:
        return self.get_state(deck_id)


class _MockBackend(DeckBackend):
    """Gemeinsame, funktionierende Mock-Implementierung."""

    def __init__(
        self,
        name: str,
        decks: dict[int, Deck] | Iterable[Deck],
        *,
        library: LibrarySource | None = None,
        load_handler: LoadHandler | None = None,
        output: OutputSink = print,
        suppress_output: OutputSuppression | None = None,
    ) -> None:
        self._name = name
        if isinstance(decks, dict):
            self.decks = dict(decks)
        else:
            self.decks = {deck.deck_id: deck for deck in decks}
        self._library = library
        self._load_handler = load_handler
        self._output = output
        self._suppress_output = suppress_output
        self.running = False
        self.lifecycle: list[str] = []
        self.command_log: list[str] = []
        self._delete_held: dict[int, bool] = {}
        for deck in self.decks.values():
            deck.set_backend_context(self.name, "STOPPED")

    @property
    def name(self) -> str:
        return self._name

    @property
    def deck_ids(self) -> tuple[int, ...]:
        return tuple(self.decks)

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.lifecycle.append("START")
        for deck in self.decks.values():
            deck.set_backend_context(self.name, "MOCK_READY")

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self.lifecycle.append("STOP")
        self._delete_held.clear()
        for deck in self.decks.values():
            deck.set_backend_context(self.name, "STOPPED")

    def get_state(self, deck_id: int) -> DeckState:
        return self._deck(deck_id).state

    def subscribe(
        self, deck_id: int, listener: StateListener
    ) -> Callable[[], None]:
        return self._deck(deck_id).subscribe(listener)

    def get_library(self) -> object | None:
        return self._library() if self._library is not None else None

    def tick(self, deck_id: int | None = None) -> None:
        if not self.running:
            return
        decks = self.decks.values() if deck_id is None else (self._deck(deck_id),)
        for deck in decks:
            deck.tick()

    def send(self, cmd: DeckCommand) -> None:
        if not self.running:
            raise RuntimeError(f"{self.name}-Backend ist nicht gestartet")
        deck = self._deck(cmd.deck_id)
        before = deck.state
        line = self._describe(cmd, before)
        deck.execute(cmd)

        if cmd.type is CommandType.DELETE:
            self._delete_held[cmd.deck_id] = cmd.pressed
        if (
            cmd.type is CommandType.LOAD
            and self._load_handler is not None
        ):
            track_id = str(cmd.get("track_id", ""))
            if track_id:
                self._load_handler(cmd.deck_id, track_id)

        if cmd.type is CommandType.TEMPO_SET:
            line = f"[{self.name}] TEMPO {deck.state.tempo_percent:+g} %"
        if line:
            self.command_log.append(line)
            if self._suppress_output is None or not self._suppress_output(cmd):
                self._output(line)

    def _deck(self, deck_id: int) -> Deck:
        try:
            return self.decks[deck_id]
        except KeyError:
            raise KeyError(
                f"Deck {deck_id} gehoert nicht zum {self.name}-Backend"
            ) from None

    def _describe(self, cmd: DeckCommand, state: DeckState) -> str | None:
        prefix = f"[{self.name}]"
        kind = cmd.type
        if kind is CommandType.PLAY_PAUSE:
            return f"{prefix} {'PAUSE' if state.is_playing else 'PLAY'}"
        if kind is CommandType.CUE:
            return f"{prefix} CUE" if cmd.pressed else None
        if kind is CommandType.JOG_MOVE:
            delta = int(cmd.get("delta", 0))
            direction = cmd.get("direction")
            direction = getattr(direction, "value", direction)
            if not direction:
                direction = "CW" if delta > 0 else "CCW" if delta < 0 else "NONE"
            velocity = abs(float(cmd.get("velocity", 0.0)))
            touched = str(bool(cmd.get("touched", False))).lower()
            return (
                f"{prefix} JOG delta={delta} velocity={velocity:g} "
                f"direction={direction} touched={touched}"
            )
        if kind is CommandType.JOG_TOUCH:
            return f"{prefix} JOG TOUCH {str(cmd.pressed).lower()}"
        if kind is CommandType.TEMPO_SET:
            return None  # Nach dem Ausfuehren aus dem DeckState formatiert.
        if kind is CommandType.PAD and cmd.pressed:
            index = int(cmd.get("index", -1))
            label = chr(ord("A") + index) if 0 <= index < 8 else str(index)
            if state.pad_mode is PadMode.HOT_CUE:
                action = (
                    "DELETE HOTCUE"
                    if self._delete_held.get(cmd.deck_id, False)
                    else "HOTCUE"
                )
                return f"{prefix} {action} {label}"
            return f"{prefix} PAD {label} mode={state.pad_mode.value}"
        if kind is CommandType.PAD and not cmd.pressed:
            return None
        if kind is CommandType.DELETE:
            return None
        if kind is CommandType.LOAD:
            return f"{prefix} LOAD TRACK {cmd.get('track_id', '')}"
        if kind is CommandType.LOOP_IN:
            return f"{prefix} LOOP IN"
        if kind is CommandType.LOOP_OUT:
            return f"{prefix} LOOP OUT"
        if kind is CommandType.RELOOP_EXIT:
            return f"{prefix} EXIT LOOP"
        if kind is CommandType.BEAT_JUMP:
            amount = float(cmd.get("beats", state.beat_jump_beats))
            amount *= -1 if int(cmd.get("direction", 1)) < 0 else 1
            return f"{prefix} BEAT JUMP {amount:+g}"
        return f"{prefix} {kind.value.replace('_', ' ')}"


class MidiBackend(_MockBackend):
    """Mock fuer die spaetere Ethernet-Verbindung zur RekordboxBridge."""

    def __init__(
        self,
        decks: dict[int, Deck] | Iterable[Deck],
        *,
        library: LibrarySource | None = None,
        output: OutputSink = print,
        suppress_output: OutputSuppression | None = None,
    ) -> None:
        super().__init__(
            "MIDI",
            decks,
            library=library,
            output=output,
            suppress_output=suppress_output,
        )


class CdjBackend(_MockBackend):
    """Mock-Routing vor der bereits vorhandenen lokalen Deck-/AudioEngine."""

    def __init__(
        self,
        decks: dict[int, Deck] | Iterable[Deck],
        *,
        library: LibrarySource | None = None,
        load_handler: LoadHandler | None = None,
        output: OutputSink = print,
        suppress_output: OutputSuppression | None = None,
    ) -> None:
        super().__init__(
            "CDJ",
            decks,
            library=library,
            load_handler=load_handler,
            output=output,
            suppress_output=suppress_output,
        )
