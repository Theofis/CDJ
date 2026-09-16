"""Sichtqualitaet der Oberflaeche - automatisch geprueft.

Diese Tests halten fest, was sich optisch nicht mehr verschlechtern darf:

* Schriften in Pixeln, damit die Bildschirm-DPI die Groesse nicht verzerrt
* feine Linien pixelgenau, damit sie scharf bleiben
* kein Text laeuft aus seiner Flaeche
* kein Text ueberdeckt anderen Text
* Farben kommen aus ``theme``, nicht aus einzelnen Komponenten
* keine Schriftzeichen als Symbole

Gearbeitet wird in einem unsichtbaren Fenster ausserhalb des Bildschirms.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.withdraw()
    _probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - kopfloser Rechner
    TK_AVAILABLE = False

from virtual_cdj.cdj_ui import theme

UI_DIR = pathlib.Path(theme.__file__).parent


# --------------------------------------------------------------------------
# Ohne Oberflaeche pruefbar
# --------------------------------------------------------------------------


class FontTests(unittest.TestCase):
    def test_sizes_are_pixels_not_points(self) -> None:
        """Negative Groesse heisst in Tk Pixel - unabhaengig von der DPI.

        Mit Punktgroessen multipliziert Tk mit ``tk scaling``; auf einem
        125-%-Bildschirm waere jeder Text ein Drittel zu gross fuer seine
        Flaeche, waehrend das Layout in Pixeln rechnet.
        """
        metrics = theme.Metrics()
        for size in (6, 8, 10, 14, 26):
            self.assertLess(metrics.font(size)[1], 0, f"Stufe {size}")
            self.assertLess(metrics.mono(size)[1], 0, f"Stufe {size}")

    def test_sizes_grow_with_the_window(self) -> None:
        small = theme.Metrics(1024, 600)
        large = theme.Metrics(1280, 800)
        self.assertGreater(abs(large.font(10)[1]), abs(small.font(10)[1]))

    def test_numbers_have_a_fixed_width(self) -> None:
        """Zeitanzeige und BPM duerfen beim Zaehlen nicht springen."""
        if not TK_AVAILABLE:
            self.skipTest("keine Tk-Anzeige verfuegbar")
        import tkinter.font as tkfont

        root = tk.Tk()
        root.geometry("200x100+3000+3000")
        root.attributes("-alpha", 0.0)
        try:
            font = tkfont.Font(root=root, font=theme.Metrics().mono(26, "bold"))
            widths = {font.measure(digit) for digit in "0123456789"}
            self.assertEqual(len(widths), 1, f"Ziffernbreiten: {widths}")
            self.assertEqual(
                font.measure("154.9"), font.measure("155.0")
            )
        finally:
            root.destroy()

    def test_snap_puts_lines_on_a_pixel(self) -> None:
        metrics = theme.Metrics()
        for value in (0.0, 10.0, 10.4, 511.7, 1023.0):
            self.assertAlmostEqual(metrics.snap(value) % 1.0, 0.5)


class PaletteTests(unittest.TestCase):
    def test_all_colours_live_in_the_theme(self) -> None:
        """Keine Komponente definiert eigene Farbwerte."""
        offenders: list[str] = []
        for path in UI_DIR.glob("*.py"):
            if path.name == "theme.py":
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if '"#' in line and len(line.split('"#')[1]) >= 6:
                    offenders.append(f"{path.name}:{number}: {stripped[:60]}")
        self.assertEqual(offenders, [], f"Farben ausserhalb theme: {offenders}")

    def test_surfaces_form_a_ladder(self) -> None:
        """Die Flaechen sind verschieden dunkel, aber nur wenig."""

        def brightness(colour: str) -> int:
            red, green, blue = theme.rgb(colour)
            return red + green + blue

        ladder = [
            theme.WAVE_BG, theme.BG, theme.PANEL,
            theme.PANEL_HI, theme.PANEL_ACTIVE,
        ]
        values = [brightness(colour) for colour in ladder]
        self.assertEqual(
            values, sorted(values), f"keine Leiter: {values}"
        )
        # Keine hellgrauen Karten: auch die hellste Flaeche bleibt dunkel.
        self.assertLess(brightness(theme.PANEL_ACTIVE), 130)

    def test_text_has_four_distinct_steps(self) -> None:
        def brightness(colour: str) -> int:
            return sum(theme.rgb(colour))

        steps = [
            theme.TEXT, theme.TEXT_SECOND, theme.TEXT_DIM,
            theme.TEXT_MUTED, theme.TEXT_DISABLED,
        ]
        values = [brightness(colour) for colour in steps]
        self.assertEqual(values, sorted(values, reverse=True), str(values))
        # Kein reines Weiss fuer Text.
        self.assertLess(brightness(theme.TEXT), 3 * 255)

    def test_no_glyphs_are_used_as_icons(self) -> None:
        """Symbole werden gezeichnet, nicht als Schriftzeichen gesetzt."""
        allowed = {
            "—",  # Gedankenstrich als Platzhalter
            "–",  # Bis-Strich in Titeln
            "·",  # Trennpunkt
            "±",  # Tempobereich
            "Ø",  # Kuenstlername
        }
        offenders: list[str] = []
        for path in UI_DIR.glob("*.py"):
            if path.name == "icons.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            # Dokumentation darf die alten Zeichen nennen - geprueft wird,
            # was gezeichnet wird.
            docstrings = {
                id(node.body[0].value)
                for node in ast.walk(tree)
                if isinstance(
                    node,
                    (ast.Module, ast.ClassDef, ast.FunctionDef,
                     ast.AsyncFunctionDef),
                )
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant):
                    continue
                if not isinstance(node.value, str):
                    continue
                if id(node) in docstrings:
                    continue
                for char in node.value:
                    if ord(char) > 0x2000 and char not in allowed:
                        offenders.append(
                            f"{path.name}:{node.lineno}: {char!r}"
                        )
        self.assertEqual(offenders, [], f"Zeichen als Symbol: {offenders}")


class IconTests(unittest.TestCase):
    @unittest.skipUnless(TK_AVAILABLE, "keine Tk-Anzeige verfuegbar")
    def test_every_icon_draws_something_at_a_consistent_size(self) -> None:
        from virtual_cdj.cdj_ui import icons

        root = tk.Tk()
        root.geometry("200x200+3000+3000")
        root.attributes("-alpha", 0.0)
        canvas = tk.Canvas(root, width=200, height=200)
        canvas.pack()
        root.update()
        try:
            for name in icons.ICONS:
                canvas.delete("all")
                items = icons.draw(canvas, name, 100, 100, 20, "#ffffff")
                self.assertTrue(items, name)
                x0, y0, x1, y1 = canvas.bbox("all")
                # Alle Symbole halten dieselbe optische Groesse ein.
                self.assertLessEqual(x1 - x0, 26, f"{name} zu breit")
                self.assertLessEqual(y1 - y0, 26, f"{name} zu hoch")
                self.assertGreaterEqual(x1 - x0, 6, f"{name} zu klein")
                self.assertGreaterEqual(y1 - y0, 6, f"{name} zu klein")
        finally:
            root.destroy()


# --------------------------------------------------------------------------
# Mit Oberflaeche
# --------------------------------------------------------------------------


@unittest.skipUnless(TK_AVAILABLE, "keine Tk-Anzeige verfuegbar")
class ScreenLayoutTests(unittest.TestCase):
    """Text muss in seine Flaeche passen - in jeder Ansicht."""

    @classmethod
    def setUpClass(cls) -> None:
        from virtual_cdj.demo import DemoTrackProvider

        cls.demo = DemoTrackProvider()

    def setUp(self) -> None:
        from virtual_cdj.cdj_ui.screen import CdjScreen
        from virtual_cdj.deck.commands import CommandType, command
        from virtual_cdj.deck.display_state import MasterDeckView
        from virtual_cdj.deck.engine import Deck
        from virtual_cdj.deck.library import (
            MediaLibrary,
            SourceInfo,
            SourceKind,
            TrackListLibrary,
        )
        from virtual_cdj.deck.provider import LocalDeckStateProvider

        model = MediaLibrary()
        model.add(
            TrackListLibrary(
                SourceInfo(
                    source_id="DEMO", name="DEMO TRACKS",
                    kind=SourceKind.DEMO, player_number=2, has_library=True,
                    note="Demo-Quelle: synthetische Analysedaten",
                ),
                self.demo.infos,
                history=lambda: (self.demo.by_index(0).info.track_id,),
            )
        )
        self.deck = Deck(2)
        self.deck.load_track(self.demo.by_index(1).info)
        self.master = Deck(1)
        self.master.load_track(self.demo.by_index(0).info)
        self.master.execute(command(CommandType.MASTER_SET, 1, "TEST"))

        self.provider = LocalDeckStateProvider(self.deck)
        self.root = tk.Tk()
        self.root.geometry("1024x600+3000+3000")
        self.root.attributes("-alpha", 0.0)
        self.screen = CdjScreen(
            self.root, self.provider,
            master_view=lambda: MasterDeckView.from_deck_state(
                self.master.state
            ),
        )
        self.screen.set_model(model)
        self.screen.pack(fill="both", expand=True)
        self.root.update()
        for setup in (
            command(CommandType.SEEK, 2, "TEST", position_s=150.0),
            command(CommandType.TEMPO_SET, 2, "TEST", value=0.617),
            command(CommandType.SYNC_TOGGLE, 2, "TEST"),
            command(CommandType.QUANTIZE_TOGGLE, 2, "TEST"),
            command(CommandType.BEAT_LOOP, 2, "TEST", beats=4),
        ):
            self.provider.send(setup)
        self.redraw()

    def tearDown(self) -> None:
        self.screen.stop()
        self.root.destroy()

    def redraw(self) -> None:
        self.root.update()
        self.screen.refresh(force=True)
        self.root.update()

    # ------------------------------------------------------------------

    def findings(self, region, name: str) -> list[str]:
        width, height = region.winfo_width(), region.winfo_height()
        if width <= 1 or height <= 1:
            return []
        found: list[str] = []
        boxes = []
        for item in region.find_all():
            if region.type(item) != "text":
                continue
            content = region.itemcget(item, "text")
            if not content.strip():
                continue
            x0, y0, x1, y1 = region.bbox(item)
            boxes.append((content, x0, y0, x1, y1))
            if x0 < -1 or x1 > width + 1 or y0 < -1 or y1 > height + 1:
                found.append(
                    f"{name}: '{content[:24]}' laeuft aus der Flaeche"
                )

        # Tk liefert den Zeilenkasten samt Ober- und Unterlaenge; die Glyphen
        # fuellen ihn nicht aus. Deshalb wird senkrecht ein Fuenftel je Seite
        # abgezogen, sonst melden benachbarte Zeilen falsche Treffer.
        def ink(y0: float, y1: float) -> tuple[float, float]:
            inset = (y1 - y0) * 0.2
            return (y0 + inset, y1 - inset)

        for index, (a, ax0, ay0, ax1, ay1) in enumerate(boxes):
            top_a, bottom_a = ink(ay0, ay1)
            for b, bx0, by0, bx1, by1 in boxes[index + 1:]:
                top_b, bottom_b = ink(by0, by1)
                if (
                    min(ax1, bx1) - max(ax0, bx0) > 2
                    and min(bottom_a, bottom_b) - max(top_a, top_b) > 1
                ):
                    found.append(
                        f"{name}: '{a[:20]}' ueberdeckt '{b[:20]}'"
                    )
        return found

    def sweep(self, label: str) -> list[str]:
        self.redraw()
        found: list[str] = []
        for name in (
            "top_bar", "waveform_header", "master_wave", "scrolling",
            "status_bar", "overview", "browser", "source_screen",
        ):
            region = getattr(self.screen, name)
            if region.winfo_ismapped():
                found += self.findings(region, f"{label}/{name}")
        for panel, widget in self.screen.panels.items():
            if widget.winfo_ismapped():
                found += self.findings(widget, f"{label}/{panel.value}")
        return found

    # ------------------------------------------------------------------

    def test_playback_screen_has_no_clipped_or_overlapping_text(self) -> None:
        self.assertEqual(self.sweep("Wiedergabe"), [])

    def test_panels_have_no_clipped_or_overlapping_text(self) -> None:
        from virtual_cdj.deck.commands import CommandType, command
        from virtual_cdj.deck.display_state import TouchPanel

        found: list[str] = []
        for panel in (
            TouchPanel.BEAT_LOOP, TouchPanel.KEY_SHIFT,
            TouchPanel.BEAT_JUMP, TouchPanel.TRACK_INFO,
        ):
            self.screen.send_command(
                command(CommandType.PANEL, 2, "TEST", panel=panel)
            )
            found += self.sweep(panel.value)
        self.assertEqual(found, [])

    def test_browse_and_source_have_no_clipped_or_overlapping_text(self) -> None:
        from virtual_cdj.deck.commands import CommandType, Views, command

        found: list[str] = []
        self.screen.set_view(Views.SOURCE)
        found += self.sweep("SOURCE")
        self.screen.set_view(Views.BROWSE)
        found += self.sweep("BROWSE")
        self.screen.send_command(
            command(CommandType.NAV_SELECT, 2, "TEST", path=("TRACK",))
        )
        self.screen.send_command(
            command(CommandType.BROWSE_TOGGLE, 2, "TEST", option="INFO")
        )
        found += self.sweep("BROWSE Tracks")
        self.screen.send_command(
            command(CommandType.BROWSE_TOGGLE, 2, "TEST", option="FONT")
        )
        found += self.sweep("BROWSE grosse Schrift")
        self.assertEqual(found, [])

    def test_fine_lines_sit_on_whole_pixels(self) -> None:
        """Beatgrid und Playhead muessen scharf bleiben."""
        offenders: list[str] = []
        for item in self.screen.scrolling.find_all():
            if self.screen.scrolling.type(item) != "line":
                continue
            coords = self.screen.scrolling.coords(item)
            width = float(
                self.screen.scrolling.itemcget(item, "width") or 1
            )
            if width != 1.0:
                continue
            # Senkrechte Linien: x muss auf einer Pixelmitte liegen.
            if len(coords) == 4 and coords[0] == coords[2]:
                if abs(coords[0] % 1.0 - 0.5) > 1e-6:
                    offenders.append(f"x={coords[0]}")
        self.assertEqual(
            offenders, [], f"unscharfe 1-px-Linien: {offenders[:6]}"
        )

    def test_waveform_image_is_reused_while_scrolling(self) -> None:
        """Die laufende Wellenform wird verschoben, nicht neu gerechnet.

        Ein 1024x392-Bild in jedem Bild neu zu rechnen und nach Tk zu
        uebertragen kostet rund 12 ms - mehr als das Budget fuer 60 FPS.
        Gerechnet wird deshalb mit Vorrat links und rechts.
        """
        from virtual_cdj.deck.commands import CommandType, command

        scrolling = self.screen.scrolling
        calls = {"count": 0}
        original = scrolling._waveform.update

        def counted(*args, **kwargs):
            calls["count"] += 1
            return original(*args, **kwargs)

        scrolling._waveform.update = counted

        def seek(position: float) -> None:
            self.provider.send(
                command(CommandType.SEEK, 2, "TEST", position_s=position)
            )
            self.redraw()

        seek(60.0)
        calls["count"] = 0
        # 60 Bilder ueber drei Sekunden Trackzeit.
        for step in range(60):
            seek(60.0 + step * 0.05)
        self.assertLess(
            calls["count"], 12,
            f"{calls['count']} Neuberechnungen fuer 60 Bilder",
        )

        # Zoomwechsel muss neu rechnen - der Maßstab aendert sich.
        before = calls["count"]
        self.screen.send_command(
            command(CommandType.WAVEFORM_ZOOM, 2, "TEST", delta=+1)
        )
        seek(60.0)
        self.assertGreater(calls["count"], before)

    def test_waveform_shows_the_right_moment_at_the_playhead(self) -> None:
        """Der Versatz des Bildes darf die Zuordnung nicht verschieben."""
        from virtual_cdj.deck.commands import CommandType, command

        scrolling = self.screen.scrolling
        for position in (30.0, 30.1, 45.7, 90.0):
            self.provider.send(
                command(CommandType.SEEK, 2, "TEST", position_s=position)
            )
            self.redraw()
            images = [
                item for item in scrolling.find_all()
                if scrolling.type(item) == "image"
            ]
            self.assertTrue(images, "keine Wellenform gezeichnet")
            offset = scrolling.coords(images[0])[0]
            seconds_per_pixel = scrolling.window_s / scrolling.w
            at_playhead = (
                scrolling._cache_start
                + (scrolling.w / 2 - offset) * seconds_per_pixel
            )
            # Hoechstens ein halbes Pixel Rundung.
            self.assertLess(
                abs(at_playhead - position), seconds_per_pixel,
                f"Bild um {(at_playhead - position) * 1000:.1f} ms versetzt",
            )

    def test_regions_do_not_redraw_without_a_visible_change(self) -> None:
        """Die Oberflaeche zeichnet nicht die ganze Flaeche in jedem Bild."""
        self.redraw()
        state = self.deck.state
        for name in ("top_bar", "status_bar", "overview"):
            region = getattr(self.screen, name)
            region.update_state(state)
        redrawn = [
            name
            for name in ("top_bar", "status_bar", "overview")
            if getattr(self.screen, name).update_state(state)
        ]
        self.assertEqual(redrawn, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
