"""Erzeugt bzw. aktualisiert config/hardware_mapping.json.

Vorhandene ``hardware``-Eintraege bleiben erhalten; neue Bedienelemente werden
mit ``null`` ergaenzt, entfallene entfernt.

    python tools/gen_hardware_mapping.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from virtual_cdj.core import hardware_map  # noqa: E402


def main() -> int:
    existing = hardware_map.load()
    fresh = hardware_map.template()

    kept = 0
    merged: dict[str, hardware_map.MappingEntry] = {}
    for entry in fresh:
        old = existing.get(entry.control_id)
        if old is not None and old.hardware is not None:
            merged[entry.control_id] = hardware_map.MappingEntry(
                control_id=entry.control_id,
                input_type=entry.input_type,
                hardware=old.hardware,
            )
            kept += 1
        else:
            merged[entry.control_id] = entry

    mapping = hardware_map.HardwareMapping(merged)
    path = mapping.save()

    dropped = [e.control_id for e in existing if e.control_id not in merged]
    print(f"geschrieben: {path}")
    print(f"Eintraege: {len(mapping)}, uebernommene Zuordnungen: {kept}")
    if dropped:
        print(f"entfernt (nicht mehr in controls.py): {', '.join(dropped)}")

    problems = mapping.validate()
    for problem in problems:
        print(f"WARNUNG: {problem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
