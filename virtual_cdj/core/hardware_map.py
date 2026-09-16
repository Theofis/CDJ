"""Hardware-Zuordnung.

Diese Schicht beantwortet genau eine Frage: welche physikalische Quelle
gehoert zu welcher Control-ID? Sie enthaelt keine CDJ-Logik und wird von der
CDJ-Logik auch nicht gelesen. Aendert sich ein Pin, aendert sich nur die
JSON-Datei.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import controls
from .model import ControlType

#: Abbildung Control-Typ -> ``input_type`` in der JSON-Datei.
INPUT_TYPE_NAMES: dict[ControlType, str] = {
    ControlType.DIGITAL_BUTTON: "digital",
    ControlType.ANALOG_FADER: "analog",
    ControlType.ANALOG_POT: "analog",
    ControlType.ENCODER: "encoder",
    ControlType.SWITCH: "switch",
    ControlType.JOG: "encoder",
    ControlType.JOYSTICK: "joystick",
    ControlType.LED: "led",
}

DEFAULT_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "hardware_mapping.json"
)

MAPPING_VERSION = 1

_NOTE = (
    "Automatisch aus virtual_cdj/core/controls.py erzeugt. "
    "'hardware' ist null, solange kein physikalischer Eingang zugeordnet ist. "
    "Beispiele fuer belegte Eintraege siehe docs/HARDWARE_MAPPING.md."
)


@dataclass(frozen=True)
class MappingEntry:
    control_id: str
    input_type: str
    hardware: dict[str, Any] | None = None

    @property
    def assigned(self) -> bool:
        return self.hardware is not None

    def to_json(self) -> dict[str, Any]:
        return {"input_type": self.input_type, "hardware": self.hardware}


class HardwareMapping:
    """Geladene Hardware-Zuordnung inklusive Pruefung."""

    def __init__(self, entries: dict[str, MappingEntry]) -> None:
        self._entries = entries

    # -- Zugriff -----------------------------------------------------------

    def __contains__(self, control_id: object) -> bool:
        return control_id in self._entries

    def __iter__(self):
        return iter(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, control_id: str) -> MappingEntry | None:
        return self._entries.get(control_id)

    def hardware_for(self, control_id: str) -> dict[str, Any] | None:
        entry = self._entries.get(control_id)
        return entry.hardware if entry else None

    def assigned(self) -> tuple[MappingEntry, ...]:
        return tuple(e for e in self._entries.values() if e.assigned)

    def unassigned(self) -> tuple[MappingEntry, ...]:
        return tuple(e for e in self._entries.values() if not e.assigned)

    # -- Pruefung ----------------------------------------------------------

    def validate(self) -> list[str]:
        """Abweichungen gegenueber der Komponentenliste als Textliste."""
        problems: list[str] = []
        for control_id in controls.CONTROLS:
            if control_id not in self._entries:
                problems.append(f"fehlt in der Zuordnung: {control_id}")
        for entry in self._entries.values():
            control = controls.CONTROLS.get(entry.control_id)
            if control is None:
                problems.append(
                    f"unbekannte ID in der Zuordnung: {entry.control_id}"
                )
                continue
            expected = INPUT_TYPE_NAMES[control.type]
            if entry.input_type != expected:
                problems.append(
                    f"{entry.control_id}: input_type ist {entry.input_type!r}, "
                    f"erwartet {expected!r}"
                )
        return problems

    # -- Serialisierung ----------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "version": MAPPING_VERSION,
            "note": _NOTE,
            "mapping": {
                entry.control_id: entry.to_json()
                for entry in self._entries.values()
            },
        }

    def save(self, path: Path | str = DEFAULT_PATH) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_json(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path


def template() -> HardwareMapping:
    """Leere Zuordnung fuer alle Elemente der Komponentenliste."""
    entries = {
        control.id: MappingEntry(
            control_id=control.id,
            input_type=INPUT_TYPE_NAMES[control.type],
            hardware=None,
        )
        for control in controls.CONTROL_LIST
    }
    return HardwareMapping(entries)


def load(path: Path | str = DEFAULT_PATH) -> HardwareMapping:
    """Zuordnung laden. Fehlt die Datei, wird die leere Vorlage benutzt."""
    path = Path(path)
    if not path.exists():
        return template()

    doc = json.loads(path.read_text(encoding="utf-8"))
    raw = doc.get("mapping", doc) if isinstance(doc, dict) else {}

    entries: dict[str, MappingEntry] = {}
    for control_id, value in raw.items():
        if control_id.startswith("_") or control_id in ("version", "note"):
            continue
        if not isinstance(value, dict):
            continue
        entries[control_id] = MappingEntry(
            control_id=control_id,
            input_type=value.get("input_type", ""),
            hardware=value.get("hardware"),
        )
    return HardwareMapping(entries)
