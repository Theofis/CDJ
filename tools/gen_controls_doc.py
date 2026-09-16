"""Erzeugt docs/CONTROLS.md aus der zentralen Komponentenliste.

Damit kann die Referenztabelle nicht von der Implementierung abweichen.

    python tools/gen_controls_doc.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from virtual_cdj.core import controls, hardware_map  # noqa: E402
from virtual_cdj.core.model import ControlType, Status  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "CONTROLS.md"

TYPE_TEXT = {
    ControlType.DIGITAL_BUTTON: "Button",
    ControlType.ANALOG_FADER: "Analog (Fader)",
    ControlType.ANALOG_POT: "Analog (Poti)",
    ControlType.ENCODER: "Encoder",
    ControlType.SWITCH: "Schalter",
    ControlType.JOG: "Jogwheel",
    ControlType.JOYSTICK: "Joystick",
    ControlType.LED: "LED (Ausgang)",
}


def main() -> int:
    mapping = hardware_map.load()

    lines: list[str] = []
    lines.append("# Bedienelemente des virtuellen CDJ")
    lines.append("")
    lines.append(
        "Automatisch erzeugt aus `virtual_cdj/core/controls.py` "
        "(`python tools/gen_controls_doc.py`). Nicht von Hand aendern - "
        "Aenderungen gehoeren in `controls.py`."
    )
    lines.append("")
    lines.append(
        f"Eingaenge: **{len(controls.INPUT_CONTROLS)}**, "
        f"reine Anzeigen: **{len(controls.OUTPUT_CONTROLS)}**, "
        f"ungeklaert: **{len(controls.unresolved())}**"
    )
    lines.append("")

    for group in controls.GROUP_ORDER:
        group_controls = controls.by_group(group)
        if not group_controls:
            continue
        lines.append(f"## {group}")
        lines.append("")
        lines.append(
            "| ID | Beschriftung | Typ | LED | Hardware | Funktion | Status |"
        )
        lines.append(
            "| -- | ------------ | --- | --- | -------- | -------- | ------ |"
        )
        for control in group_controls:
            entry = mapping.get(control.id)
            hardware = "nicht zugeordnet"
            if entry is not None and entry.hardware is not None:
                hardware = f"`{entry.hardware}`"
            status = (
                "ungeklaert"
                if control.status is Status.UNRESOLVED
                else "virtuell"
            )
            lines.append(
                f"| `{control.id}` | {control.label} | "
                f"{TYPE_TEXT[control.type]} | "
                f"{'ja' if control.has_led else '-'} | {hardware} | "
                f"nicht zugeordnet | {status} |"
            )
        lines.append("")

    unresolved = controls.unresolved()
    if unresolved:
        lines.append("## Offene Punkte")
        lines.append("")
        lines.append(
            "Diese Elemente sind im Bild erkennbar, ihre Beschriftung oder "
            "Funktion ist aber nicht gesichert. Sie erzeugen bereits "
            "Input-Ereignisse und werden umbenannt, sobald geklaert ist, "
            "welches Bedienelement gemeint ist."
        )
        lines.append("")
        for control in unresolved:
            lines.append(f"* `{control.id}` - {control.note}")
        lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"geschrieben: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
