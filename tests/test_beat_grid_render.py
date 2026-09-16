"""Beatgrid der vergroesserten laufenden Wellenform.

Gefordert ist eine einzige Linienform: jede Beatlinie hat dieselbe Hoehe und
dieselbe Breite. Unterschieden wird ausschliesslich ueber die Farbe - Beat 1
des Takts rot, die uebrigen grau.

Gezeichnet wird hier ohne Fenster. Die Pruefung ruft die **echten** Methoden
``_draw_beat_grid`` und ``_x_of`` von ``CdjScrollingWaveform`` auf und faengt
die Linien in einer Liste auf, statt sie an einen Tk-Canvas zu geben. So wird
genau der Code geprueft, der spaeter zeichnet, ohne dass ein Bildschirm noetig
ist.
"""

from __future__ import annotations

import unittest

from virtual_cdj.cdj_ui import theme
from virtual_cdj.cdj_ui.scrolling_waveform import (
    PLAYHEAD_FRACTION,
    CdjScrollingWaveform,
)
from virtual_cdj.deck.state import BeatGrid, DeckState, TrackInfo

WIDTH = 1024
HEIGHT = 200
BPM = 120.0
#: 120 BPM sind genau 0.5 s je Beat - damit sind alle Zeiten im Test exakt.
BEAT_S = 60.0 / BPM


def track(grid: BeatGrid) -> TrackInfo:
    return TrackInfo(
        track_id="t1",
        title="Grid",
        artist="Test",
        duration_s=600.0,
        original_bpm=BPM,
        beat_grid=grid,
    )


class Probe:
    """Nimmt die Linien auf, statt sie zu zeichnen.

    Die beiden gebundenen Methoden sind die Originale aus der Region; nur
    ``create_line`` und die Geometrie sind ersetzt.
    """

    _draw_beat_grid = CdjScrollingWaveform._draw_beat_grid
    _x_of = CdjScrollingWaveform._x_of

    def __init__(self, window_s: float = 8.0) -> None:
        self.w = WIDTH
        self.h = HEIGHT
        self.window_s = window_s
        self.metrics = theme.DEFAULT_METRICS
        self.lines: list[dict] = []

    def create_line(self, x0, y0, x1, y1, **kwargs) -> None:
        self.lines.append(
            {
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "fill": kwargs.get("fill"),
                "width": kwargs.get("width"),
            }
        )

    # ------------------------------------------------------------------

    def draw(self, grid: BeatGrid, position_s: float) -> list[dict]:
        self.lines.clear()
        state = DeckState(deck_id=1, track=track(grid), position_s=position_s)
        left = position_s - self.window_s * PLAYHEAD_FRACTION
        self._draw_beat_grid(state, left, left + self.window_s, self.h)
        return list(self.lines)

    def colours(self, grid: BeatGrid, position_s: float) -> list[str]:
        return [line["fill"] for line in self.draw(grid, position_s)]

    def time_of(self, line: dict, position_s: float) -> float:
        """Aus der x-Koordinate zurueck auf die Zeit.

        Damit laesst sich pruefen, dass ein bestimmter Beat seine Farbe
        behaelt - unabhaengig davon, an welcher Stelle im Bild er liegt.
        """
        seconds_per_px = self.window_s / self.w
        offset = line["x0"] - self.w * PLAYHEAD_FRACTION
        return position_s + offset * seconds_per_px


class LineShapeTests(unittest.TestCase):
    """Hoehe und Breite sind fuer jede Linie gleich."""

    def setUp(self) -> None:
        self.probe = Probe()
        self.grid = BeatGrid(first_beat_s=0.0, bpm=BPM, beats_per_bar=4)

    def test_every_line_spans_the_full_height(self) -> None:
        lines = self.probe.draw(self.grid, 4.0)
        self.assertTrue(lines, "Es wurde keine Beatlinie gezeichnet")
        for line in lines:
            self.assertEqual(line["y0"], 0)
            self.assertEqual(line["y1"], HEIGHT)

    def test_every_line_has_the_same_width(self) -> None:
        lines = self.probe.draw(self.grid, 4.0)
        self.assertEqual({line["width"] for line in lines}, {1})

    def test_geometry_is_identical_for_red_and_grey(self) -> None:
        """Die rote Linie unterscheidet sich ausschliesslich in der Farbe."""
        lines = self.probe.draw(self.grid, 4.0)
        shapes = {
            (line["y0"], line["y1"], line["width"]) for line in lines
        }
        self.assertEqual(
            len(shapes), 1,
            f"Es gibt mehr als eine Linienform: {shapes}",
        )
        # Und es kommen wirklich beide Farben vor - sonst waere der Test
        # oben auch dann gruen, wenn gar keine roten Linien entstuenden.
        self.assertEqual(
            {line["fill"] for line in lines},
            {theme.GRID_DOWNBEAT, theme.GRID_BEAT},
        )

    def test_no_line_is_shortened(self) -> None:
        """Die frueheren kurzen Beatlinien darf es nicht mehr geben."""
        lines = self.probe.draw(self.grid, 4.0)
        for line in lines:
            self.assertEqual(
                line["y1"] - line["y0"], HEIGHT,
                "Eine Linie ist kuerzer als die volle Hoehe",
            )


class PatternTests(unittest.TestCase):
    """Rot - Grau - Grau - Grau, immer wieder."""

    def setUp(self) -> None:
        self.probe = Probe()
        self.red = theme.GRID_DOWNBEAT
        self.grey = theme.GRID_BEAT

    def test_four_four_repeats_red_grey_grey_grey(self) -> None:
        grid = BeatGrid(first_beat_s=0.0, bpm=BPM, beats_per_bar=4)
        # Fenster 0.0 - 8.0 s: Beat 0 liegt genau links, Beat 16 genau
        # rechts. Das ergibt 17 sichtbare Linien.
        colours = self.probe.colours(grid, 4.0)
        self.assertEqual(len(colours), 17)
        expected = [
            self.red if number % 4 == 0 else self.grey
            for number in range(17)
        ]
        self.assertEqual(colours, expected)

    def test_red_is_red_and_grey_is_grey(self) -> None:
        self.assertEqual(theme.GRID_DOWNBEAT, theme.RED)
        self.assertNotEqual(theme.GRID_BEAT, theme.GRID_DOWNBEAT)

    def test_the_pattern_follows_the_tracks_own_downbeat(self) -> None:
        """Beginnt der Takt erst bei Beat 2, faerbt sich auch Beat 2 rot."""
        grid = BeatGrid(
            first_beat_s=0.0, bpm=BPM, beats_per_bar=4,
            first_downbeat_index=2,
        )
        colours = self.probe.colours(grid, 4.0)
        expected = [
            self.red if (number - 2) % 4 == 0 else self.grey
            for number in range(17)
        ]
        self.assertEqual(colours, expected)

    def test_a_three_beat_bar_repeats_every_three(self) -> None:
        grid = BeatGrid(first_beat_s=0.0, bpm=BPM, beats_per_bar=3)
        colours = self.probe.colours(grid, 4.0)
        expected = [
            self.red if number % 3 == 0 else self.grey
            for number in range(17)
        ]
        self.assertEqual(colours, expected)

    def test_an_offset_first_beat_does_not_shift_the_pattern(self) -> None:
        """Das Grid beginnt bei 0.17 s - Beat 0 bleibt trotzdem rot."""
        grid = BeatGrid(first_beat_s=0.17, bpm=BPM, beats_per_bar=4)
        lines = self.probe.draw(grid, 4.0)
        for line in lines:
            time_s = self.probe.time_of(line, 4.0)
            number = round((time_s - 0.17) / BEAT_S)
            want = self.red if number % 4 == 0 else self.grey
            self.assertEqual(
                line["fill"], want,
                f"Beat {number} bei {time_s:.3f} s hat die falsche Farbe",
            )


class StabilityTests(unittest.TestCase):
    """Das Muster darf nicht verrutschen."""

    def setUp(self) -> None:
        self.grid = BeatGrid(first_beat_s=0.0, bpm=BPM, beats_per_bar=4)
        self.red = theme.GRID_DOWNBEAT
        self.grey = theme.GRID_BEAT

    def colour_by_beat(
        self, probe: Probe, position_s: float
    ) -> dict[int, str]:
        """Farbe je Beatnummer - aus der gezeichneten x-Koordinate."""
        found: dict[int, str] = {}
        for line in probe.draw(self.grid, position_s):
            time_s = probe.time_of(line, position_s)
            found[round(time_s / BEAT_S)] = line["fill"]
        return found

    def test_playback_never_shifts_the_pattern(self) -> None:
        """Ueber 3000 Bilder - 50 s Wiedergabe - bleibt jede Farbe stehen."""
        probe = Probe()
        seen: dict[int, str] = {}
        for frame in range(3000):
            position_s = 4.0 + frame * (1.0 / 60.0)
            for number, colour in self.colour_by_beat(probe, position_s).items():
                want = self.red if number % 4 == 0 else self.grey
                self.assertEqual(
                    colour, want,
                    f"Beat {number} ist bei {position_s:.3f} s falsch",
                )
                if number in seen:
                    self.assertEqual(
                        colour, seen[number],
                        f"Beat {number} hat die Farbe gewechselt",
                    )
                seen[number] = colour
        self.assertGreater(len(seen), 100, "Zu wenig Beats geprueft")

    def test_zooming_never_shifts_the_pattern(self) -> None:
        for window_s in (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0):
            probe = Probe(window_s=window_s)
            found = self.colour_by_beat(probe, 20.0)
            self.assertTrue(found, f"Fenster {window_s} s zeichnet nichts")
            for number, colour in found.items():
                want = self.red if number % 4 == 0 else self.grey
                self.assertEqual(
                    colour, want,
                    f"Fenster {window_s} s: Beat {number} ist falsch",
                )

    def test_scrolling_far_into_the_track_keeps_the_pattern(self) -> None:
        probe = Probe()
        for position_s in (0.0, 4.0, 37.5, 120.25, 301.75, 599.0):
            for number, colour in self.colour_by_beat(probe, position_s).items():
                want = self.red if number % 4 == 0 else self.grey
                self.assertEqual(
                    colour, want,
                    f"Bei {position_s} s ist Beat {number} falsch",
                )


class AbsenceTests(unittest.TestCase):
    """Ohne Grid wird nichts erfunden."""

    def test_without_a_beat_grid_nothing_is_drawn(self) -> None:
        probe = Probe()
        state = DeckState(
            deck_id=1, track=track(BeatGrid()), position_s=4.0,
        )
        probe._draw_beat_grid(state, 0.0, 8.0, HEIGHT)
        self.assertEqual(probe.lines, [])

    def test_without_a_track_nothing_is_drawn(self) -> None:
        probe = Probe()
        probe._draw_beat_grid(DeckState(deck_id=1), 0.0, 8.0, HEIGHT)
        self.assertEqual(probe.lines, [])

    def test_a_broken_grid_does_not_flood_the_canvas(self) -> None:
        """Sicherheitsgrenze: lieber nichts als tausende Linien."""
        probe = Probe(window_s=3600.0)
        grid = BeatGrid(first_beat_s=0.0, bpm=BPM, beats_per_bar=4)
        probe._draw_beat_grid(grid and DeckState(
            deck_id=1, track=track(grid), position_s=1800.0,
        ), 0.0, 3600.0, HEIGHT)
        self.assertEqual(probe.lines, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
