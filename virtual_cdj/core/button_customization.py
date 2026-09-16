"""Persistente Namen, Konsolentexte und LED-Farben fuer Taster.

Die technische Control-ID bleibt absichtlich unveraendert. Dadurch gelten
Hardware-Zuordnung und Deck-Mapping weiter, auch wenn der sichtbare Name im
virtuellen Bedienfeld angepasst wird.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from . import controls
from .model import Control, ControlType, EventType, InputEvent

DEFAULT_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "button_customizations.json"
)
BUTTON_CUSTOMIZATION_VERSION = 2
MAX_NAME_LENGTH = 40
MAX_CONSOLE_OUTPUT_LENGTH = 500

ChangeListener = Callable[[str | None], None]
OutputSink = Callable[[str], None]


class ButtonLedColor(str, Enum):
    """Im Editor angebotene LED-Ausfuehrungen."""

    NONE = "NONE"
    BLUE = "BLUE"
    ORANGE = "ORANGE"
    GREEN = "GREEN"


@dataclass(frozen=True)
class ButtonCustomization:
    """Optionale Abweichungen von der zentralen Tasterdefinition."""

    display_name: str = ""
    console_output: str = ""
    #: Leer bedeutet Vorgabe aus ``controls.py``.
    led_color: str = ""

    @classmethod
    def from_json(cls, data: object) -> ButtonCustomization | None:
        if not isinstance(data, dict):
            return None
        name = data.get("display_name", "")
        output = data.get("console_output", "")
        led_color = data.get("led_color", "")
        if (
            not isinstance(name, str)
            or not isinstance(output, str)
            or not isinstance(led_color, str)
        ):
            return None
        if led_color not in ("", *(color.value for color in ButtonLedColor)):
            led_color = ""
        return cls(
            display_name=name.strip()[:MAX_NAME_LENGTH],
            console_output=output.strip()[:MAX_CONSOLE_OUTPUT_LENGTH],
            led_color=led_color,
        )

    def to_json(self) -> dict[str, str]:
        return {
            "display_name": self.display_name,
            "console_output": self.console_output,
            "led_color": self.led_color,
        }


class ButtonCustomizationStore:
    """Laedt, speichert und verteilt Anpassungen fuer digitale Taster."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        output: OutputSink = print,
    ) -> None:
        self.path = Path(path) if path is not None else DEFAULT_PATH
        self.output = output
        self._entries: dict[str, ButtonCustomization] = {}
        self._listeners: list[ChangeListener] = []
        self.load()

    def load(self) -> None:
        """Gueltige bekannte Taster aus der JSON-Datei uebernehmen."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        raw_entries = data.get("buttons", {})
        if not isinstance(raw_entries, dict):
            return

        loaded: dict[str, ButtonCustomization] = {}
        for control_id, raw in raw_entries.items():
            if not isinstance(control_id, str) or not self.is_button(control_id):
                continue
            entry = ButtonCustomization.from_json(raw)
            if entry is not None and (
                entry.display_name or entry.console_output or entry.led_color
            ):
                loaded[control_id] = entry
        self._entries = loaded

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "version": BUTTON_CUSTOMIZATION_VERSION,
            "buttons": {
                control_id: entry.to_json()
                for control_id, entry in sorted(self._entries.items())
            },
        }
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def is_button(control_id: str) -> bool:
        control = controls.CONTROLS.get(control_id)
        return (
            control is not None
            and control.type is ControlType.DIGITAL_BUTTON
        )

    def get(self, control_id: str) -> ButtonCustomization:
        return self._entries.get(control_id, ButtonCustomization())

    def display_name(self, control: Control) -> str:
        custom = self.get(control.id).display_name
        return custom or control.short or control.label

    def display_control(self, control: Control) -> Control:
        """Kopie fuer die GUI; ID, Typ und Geometrie bleiben identisch."""
        entry = self.get(control.id)
        if not entry.display_name and not entry.led_color:
            return control
        changes: dict[str, object] = {}
        if entry.display_name:
            changes.update(label=entry.display_name, short=entry.display_name)
        if entry.led_color:
            changes["has_led"] = entry.led_color != ButtonLedColor.NONE.value
        return replace(control, **changes)

    def console_output(self, control_id: str) -> str | None:
        output = self.get(control_id).console_output
        return output or None

    @staticmethod
    def default_led_color(control: Control) -> ButtonLedColor:
        return ButtonLedColor.GREEN if control.has_led else ButtonLedColor.NONE

    def led_color(self, control: Control) -> ButtonLedColor:
        custom = self.get(control.id).led_color
        if custom:
            return ButtonLedColor(custom)
        return self.default_led_color(control)

    def has_led_override(self, control_id: str) -> bool:
        return bool(self.get(control_id).led_color)

    def set(
        self,
        control_id: str,
        *,
        display_name: str = "",
        console_output: str = "",
        led_color: ButtonLedColor | str = "",
    ) -> None:
        if not self.is_button(control_id):
            raise ValueError(f"Kein digitaler Taster: {control_id}")
        name = display_name.strip()
        output = console_output.strip()
        led = led_color.value if isinstance(led_color, ButtonLedColor) else led_color
        if led not in ("", *(color.value for color in ButtonLedColor)):
            raise ValueError(f"Unbekannte LED-Farbe: {led_color}")
        if len(name) > MAX_NAME_LENGTH:
            raise ValueError(
                f"Der Buttonname darf hoechstens {MAX_NAME_LENGTH} Zeichen haben."
            )
        if len(output) > MAX_CONSOLE_OUTPUT_LENGTH:
            raise ValueError(
                "Die Konsolenausgabe darf hoechstens "
                f"{MAX_CONSOLE_OUTPUT_LENGTH} Zeichen haben."
            )
        if name or output or led:
            self._entries[control_id] = ButtonCustomization(name, output, led)
        else:
            self._entries.pop(control_id, None)
        self.save()
        self._notify(control_id)

    def reset(self, control_id: str) -> None:
        if not self.is_button(control_id):
            raise ValueError(f"Kein digitaler Taster: {control_id}")
        self._entries.pop(control_id, None)
        self.save()
        self._notify(control_id)

    def reset_all(self) -> None:
        self._entries.clear()
        self.save()
        self._notify(None)

    def emit(self, event: InputEvent) -> bool:
        """Individuellen Text einmal beim Druecken ausgeben."""
        if event.event is not EventType.PRESS:
            return False
        text = self.console_output(event.control_id)
        if text is None:
            return False
        self.output(text)
        return True

    def subscribe(self, listener: ChangeListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _notify(self, control_id: str | None) -> None:
        for listener in tuple(self._listeners):
            listener(control_id)
