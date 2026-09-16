"""Tests der zentralen Komponentenliste und der Hardware-Zuordnung."""

from __future__ import annotations

import unittest

from virtual_cdj.core import controls, hardware_map, ids
from virtual_cdj.core.model import ControlType, Shape, Status


class ComponentListTests(unittest.TestCase):
    def test_ids_are_unique(self) -> None:
        seen = [c.id for c in controls.CONTROL_LIST]
        self.assertEqual(len(seen), len(set(seen)))

    def test_id_naming_convention(self) -> None:
        for control in controls.CONTROL_LIST:
            self.assertRegex(control.id, r"^[A-Z][A-Z0-9_]*$", control.id)

    def test_every_control_has_a_known_group(self) -> None:
        for control in controls.CONTROL_LIST:
            self.assertIn(control.group, controls.GROUP_ORDER, control.id)

    def test_expected_controls_exist(self) -> None:
        expected = (
            ids.PLAY, ids.CUE,
            ids.LOOP_IN, ids.LOOP_OUT, ids.RELOOP_EXIT,
            ids.BEAT_LOOP_4, ids.BEAT_LOOP_8,
            ids.TAG_LIST, ids.PLAYLIST,
            ids.BACK, ids.TAG_TRACK_REMOVE,
            ids.TRACK_FILTER_EDIT, ids.SHORTCUT,
            *ids.PADS,
            ids.TEMPO_FADER, ids.JOG_MOVE, ids.JOG_TOUCH,
        )
        for control_id in expected:
            self.assertIn(control_id, controls.CONTROLS, control_id)

    def test_eight_pads(self) -> None:
        pads = controls.by_group(controls.G_PADS)
        self.assertEqual(len(pads), 8)
        self.assertEqual(tuple(p.id for p in pads), ids.PADS)

    def test_six_buttons_are_above_the_display_in_confirmed_order(self) -> None:
        row = tuple(
            control.id
            for control in sorted(
                controls.by_group(controls.G_SCREEN), key=lambda item: item.x
            )
        )
        self.assertEqual(row, ids.SCREEN_BUTTONS)

    def test_four_confirmed_buttons_surround_the_browse_wheel(self) -> None:
        wheel = controls.get(ids.BROWSE_ROTATE)
        back = controls.get(ids.BACK)
        tag = controls.get(ids.TAG_TRACK_REMOVE)
        track_filter = controls.get(ids.TRACK_FILTER_EDIT)
        shortcut = controls.get(ids.SHORTCUT)
        self.assertLess(back.x, wheel.x)
        self.assertLess(back.y, wheel.y)
        self.assertGreater(tag.x, wheel.x)
        self.assertLess(tag.y, wheel.y)
        self.assertLess(track_filter.x, wheel.x)
        self.assertGreater(track_filter.y, wheel.y)
        self.assertGreater(shortcut.x, wheel.x)
        self.assertGreater(shortcut.y, wheel.y)

    def test_beat_jump_is_rectangular_and_beat_loop_is_round(self) -> None:
        for control_id in (ids.BEAT_JUMP_PREV, ids.BEAT_JUMP_NEXT):
            self.assertIs(controls.get(control_id).shape, Shape.RECT)
        for control_id in ids.BEAT_LOOPS:
            self.assertIs(controls.get(control_id).shape, Shape.ROUND)
        self.assertLess(
            controls.get(ids.BEAT_LOOP_4).y,
            controls.get(ids.BEAT_JUMP_PREV).y,
        )
        self.assertLess(
            controls.get(ids.BEAT_JUMP_PREV).y,
            controls.get(ids.DIRECTION).y,
        )

    def test_no_auto_cue_anywhere(self) -> None:
        for control in controls.CONTROL_LIST:
            self.assertNotIn("AUTO_CUE", control.id)
            self.assertNotIn("auto cue", control.label.lower())

    def test_jog_has_its_own_type(self) -> None:
        self.assertIs(controls.get(ids.JOG_MOVE).type, ControlType.JOG)
        self.assertEqual(len(controls.by_type(ControlType.JOG)), 1)

    def test_analog_controls_use_normalised_defaults(self) -> None:
        for control in controls.CONTROL_LIST:
            if control.type in (
                ControlType.ANALOG_FADER, ControlType.ANALOG_POT
            ):
                self.assertGreaterEqual(control.default_value, 0.0)
                self.assertLessEqual(control.default_value, 1.0)
                self.assertLess(control.raw_min, control.raw_max)

    def test_unresolved_controls_carry_a_note(self) -> None:
        for control in controls.unresolved():
            self.assertTrue(control.note.strip(), control.id)

    def test_unresolved_controls_still_produce_input(self) -> None:
        # Ungeklaerte Elemente sollen bedienbar sein, nur nicht benannt.
        unresolved_inputs = [c for c in controls.unresolved() if c.is_input]
        self.assertGreater(len(unresolved_inputs), 0)

    def test_status_values_are_known(self) -> None:
        for control in controls.CONTROL_LIST:
            self.assertIn(control.status, (Status.VIRTUAL, Status.UNRESOLVED))

    def test_positions_are_inside_the_reference_image(self) -> None:
        width, height = controls.PANEL_REFERENCE_SIZE
        for control in controls.CONTROL_LIST:
            self.assertGreater(control.x, 0, control.id)
            self.assertLess(control.x, width, control.id)
            self.assertGreater(control.y, 0, control.id)
            self.assertLess(control.y, height, control.id)


class HardwareMappingTests(unittest.TestCase):
    def test_template_covers_every_control(self) -> None:
        mapping = hardware_map.template()
        self.assertEqual(len(mapping), len(controls.CONTROL_LIST))
        for control in controls.CONTROL_LIST:
            self.assertIn(control.id, mapping)

    def test_template_is_completely_unassigned(self) -> None:
        mapping = hardware_map.template()
        self.assertEqual(mapping.assigned(), ())
        self.assertEqual(len(mapping.unassigned()), len(controls.CONTROL_LIST))

    def test_input_types_match_the_control_types(self) -> None:
        mapping = hardware_map.template()
        self.assertEqual(mapping.validate(), [])
        self.assertEqual(
            mapping.get(ids.PLAY).input_type, "digital"  # type: ignore[union-attr]
        )
        self.assertEqual(
            mapping.get(ids.TEMPO_FADER).input_type,  # type: ignore[union-attr]
            "analog",
        )
        self.assertEqual(
            mapping.get(ids.JOG_MOVE).input_type,  # type: ignore[union-attr]
            "encoder",
        )

    def test_committed_mapping_file_is_consistent(self) -> None:
        mapping = hardware_map.load()
        self.assertEqual(mapping.validate(), [])

    def test_assigned_hardware_survives_a_round_trip(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        mapping = hardware_map.template()
        entries = {e.control_id: e for e in mapping}
        entries[ids.PLAY] = hardware_map.MappingEntry(
            control_id=ids.PLAY,
            input_type="digital",
            hardware={"driver": "mcp23017", "device": 1, "pin": "GPA0"},
        )
        mapping = hardware_map.HardwareMapping(entries)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapping.json"
            mapping.save(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["mapping"][ids.PLAY]["input_type"], "digital")

            reloaded = hardware_map.load(path)
            self.assertEqual(
                reloaded.hardware_for(ids.PLAY),
                {"driver": "mcp23017", "device": 1, "pin": "GPA0"},
            )
            self.assertIsNone(reloaded.hardware_for(ids.CUE))
            self.assertEqual(reloaded.validate(), [])


if __name__ == "__main__":
    unittest.main()
