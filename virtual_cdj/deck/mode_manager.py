"""Zentraler, persistenter Wechsel zwischen MIDI- und CDJ-Backend."""

from __future__ import annotations

import json
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from .backend import DeckBackend


class OperatingMode(str, Enum):
    """Die zwei normalen Betriebsarten des Players."""

    MIDI = "MIDI"
    CDJ = "CDJ"


ModeListener = Callable[[OperatingMode], None]


class ModeManager:
    """Einzige Instanz, die das aktive Deck-Backend bestimmt.

    ``ApplicationMode`` (Performance, Menue, Test, Kalibrierung) bleibt davon
    getrennt. Ein Wechsel wird waehrend laufender Wiedergabe blockiert,
    anschliessend in der Reihenfolge stop -> start -> persist -> notify
    ausgefuehrt.
    """

    def __init__(
        self,
        midi_backend: DeckBackend,
        cdj_backend: DeckBackend,
        *,
        settings_path: str | Path | None = None,
        default: OperatingMode = OperatingMode.CDJ,
        #: Erzwungener Startmodus. Ist er gesetzt, wird die gespeicherte
        #: Einstellung beim Start **nicht** gelesen. Gedacht fuer Tests und
        #: fuer Kommandozeilenaufrufe: ein Test soll nicht davon abhaengen,
        #: welchen Modus die Person beim letzten Programmlauf gewaehlt hat.
        #: Ein spaeteres ``set_mode()`` speichert weiterhin normal.
        mode: OperatingMode | None = None,
    ) -> None:
        self._backends = {
            OperatingMode.MIDI: midi_backend,
            OperatingMode.CDJ: cdj_backend,
        }
        self.settings_path = (
            Path(settings_path)
            if settings_path is not None
            else Path(__file__).resolve().parents[2] / "config" / "settings.json"
        )
        self._listeners: list[ModeListener] = []
        self.last_error = ""
        self._current_mode = (
            mode if mode is not None else self._load_mode(default)
        )
        self._active_backend = self._backends[self._current_mode]
        self._active_backend.start()

    @property
    def current_mode(self) -> OperatingMode:
        return self._current_mode

    def get_mode(self) -> OperatingMode:
        return self._current_mode

    @property
    def active_backend(self) -> DeckBackend:
        """Das ausschliesslich hier ausgewaehlte Backend."""
        return self._active_backend

    def backend(self, mode: OperatingMode) -> DeckBackend:
        """Backend gezielt abfragen, etwa fuer Diagnose und Tests."""
        return self._backends[mode]

    def subscribe(self, listener: ModeListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def set_mode(self, mode: OperatingMode | str) -> bool:
        """Kontrolliert wechseln; ``False`` bedeutet sicher blockiert."""
        try:
            target = mode if isinstance(mode, OperatingMode) else OperatingMode(mode)
        except ValueError:
            self.last_error = f"Unbekannter Betriebsmodus: {mode}"
            return False

        if target is self._current_mode:
            self.last_error = ""
            return True

        playing = [
            deck_id
            for deck_id in self._active_backend.deck_ids
            if self._active_backend.get_state(deck_id).is_playing
        ]
        if playing:
            decks = ", ".join(str(deck_id) for deck_id in playing)
            self.last_error = (
                f"Moduswechsel blockiert: Wiedergabe auf Deck {decks} stoppen."
            )
            return False

        previous_mode = self._current_mode
        previous_backend = self._active_backend
        next_backend = self._backends[target]

        previous_backend.stop()
        try:
            next_backend.start()
        except Exception:
            # Ein fehlgeschlagener Start darf die Anwendung nicht ohne
            # aktives Backend zuruecklassen.
            previous_backend.start()
            self._current_mode = previous_mode
            self._active_backend = previous_backend
            raise

        self._current_mode = target
        self._active_backend = next_backend
        self.last_error = ""
        self.persist_mode()
        for listener in list(self._listeners):
            listener(target)
        return True

    def switch_backend(self, mode: OperatingMode | str) -> bool:
        """Expliziter Name fuer Aufrufer, die den Lebenszyklus betonen."""
        return self.set_mode(mode)

    def persist_mode(self) -> bool:
        """Aktuellen Modus speichern und fremde Settings-Schluessel erhalten."""
        data: dict[str, object] = {}
        try:
            if self.settings_path.exists():
                parsed = json.loads(self.settings_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    data = parsed
        except (OSError, json.JSONDecodeError):
            # Eine defekte Datei wird beim naechsten Speichern repariert.
            data = {}

        data["operating_mode"] = self._current_mode.value
        temporary = self.settings_path.with_suffix(
            self.settings_path.suffix + ".tmp"
        )
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.settings_path)
        except OSError as exc:
            self.last_error = f"Betriebsmodus konnte nicht gespeichert werden: {exc}"
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        return True

    def tick(self, deck_id: int | None = None) -> None:
        self._active_backend.tick(deck_id)

    def close(self) -> None:
        self._active_backend.stop()

    def _load_mode(self, default: OperatingMode) -> OperatingMode:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return OperatingMode(str(data.get("operating_mode", default.value)))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return default
