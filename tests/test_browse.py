"""SOURCE, BROWSE und Drehregler - Verhalten nach Handbuch.

Belegstellen stehen an den einzelnen Tests: S. 18 (Quellenauswahl),
S. 19-20 (Durchsuchen-Bildschirm), S. 22 (Zoom-/Rastermodus), S. 24-25
(Drehregler und Touch), S. 38 (Sprungmodus), S. 42 (Verlauf), S. 72
(Beatgrid).
"""

from __future__ import annotations

import unittest

from virtual_cdj.core import ids
from virtual_cdj.core.model import (
    ControlType,
    EventType,
    InputEvent,
    Source,
)
from virtual_cdj.deck.commands import CommandType, Views, command
from virtual_cdj.deck.display_state import (
    BrowseView,
    CdjDisplayState,
    JumpMode,
    RotaryMode,
)
from virtual_cdj.deck.engine import Deck
from virtual_cdj.deck.library import (
    BrowseColumn,
    EntryKind,
    MediaLibrary,
    SourceInfo,
    SourceKind,
    TrackListLibrary,
)
from virtual_cdj.deck.mapping import InputMapper
from virtual_cdj.deck.mode_manager import OperatingMode
from virtual_cdj.deck.provider import LocalDeckStateProvider
from virtual_cdj.deck.state import BeatGrid, TrackInfo, empty_state

TRACKS = (
    TrackInfo(
        track_id="a", title="Alpha Drive", artist="Aiko", album="First",
        genre="Techno", original_bpm=150.0, key="8A", duration_s=300.0,
        file_path="/musik/techno/alpha.wav", source="USB",
    ),
    TrackInfo(
        track_id="b", title="Broken Window", artist="SHORDY", album="Second",
        genre="Industrial", original_bpm=154.0, key="4A", duration_s=330.0,
        file_path="/musik/schranz/broken.wav", source="USB",
    ),
    TrackInfo(
        track_id="c", title="Steel Pressure", artist="SHORDY", album="Second",
        genre="Industrial", original_bpm=156.0, key="5A", duration_s=300.0,
        file_path="/musik/schranz/steel.wav", source="USB",
    ),
)


def library(history=(), *, has_library: bool = True) -> MediaLibrary:
    model = MediaLibrary()
    model.add(
        TrackListLibrary(
            SourceInfo(
                source_id="USB1", name="Pioneer DJ 001",
                kind=SourceKind.USB, player_number=1,
                has_library=has_library,
            ),
            lambda: TRACKS,
            history=lambda: tuple(history),
        )
    )
    return model


# --------------------------------------------------------------------------
# Modell
# --------------------------------------------------------------------------


class LibraryTests(unittest.TestCase):
    def test_source_counts_come_from_the_real_list(self) -> None:
        info = library().sources()[0]
        self.assertEqual(info.songs, 3)
        self.assertEqual(info.playlists, 0)
        self.assertEqual(info.player_number, 1)

    def test_root_shows_only_categories_that_have_content(self) -> None:
        """S. 19: Kategorien links. Leere Kategorien erscheinen nicht."""
        labels = [e.label for e in library().node("USB1").entries]
        self.assertIn("TRACK", labels)
        self.assertIn("ARTIST", labels)
        self.assertIn("GENRE", labels)
        # Ohne Verlauf und ohne Playlists fehlen diese Kategorien.
        self.assertNotIn("HISTORY", labels)
        self.assertNotIn("PLAYLIST", labels)

    def test_history_category_appears_with_history(self) -> None:
        labels = [e.label for e in library(("c",)).node("USB1").entries]
        self.assertIn("HISTORY", labels)

    def test_grouping_and_descending_into_a_group(self) -> None:
        model = library()
        artists = model.node("USB1", ("ARTIST",))
        self.assertEqual(artists.level_kind, EntryKind.FOLDER)
        self.assertEqual(
            [(e.label, e.count) for e in artists.entries],
            [("Aiko", 1), ("SHORDY", 2)],
        )
        tracks = model.node("USB1", ("ARTIST", "SHORDY"))
        self.assertTrue(tracks.is_track_list)
        self.assertEqual(
            [e.track.track_id for e in tracks.entries], ["b", "c"]
        )

    def test_sorting_by_column_and_direction(self) -> None:
        """S. 20: Titelzeile beruehren sortiert."""
        model = library()
        by_bpm = model.node("USB1", ("TRACK",), column=BrowseColumn.BPM)
        self.assertEqual(
            [e.track.original_bpm for e in by_bpm.entries],
            [150.0, 154.0, 156.0],
        )
        down = model.node(
            "USB1", ("TRACK",), column=BrowseColumn.BPM, ascending=False
        )
        self.assertEqual(
            [e.track.original_bpm for e in down.entries],
            [156.0, 154.0, 150.0],
        )

    def test_numbers_stay_the_position_in_the_source(self) -> None:
        """Die Spalte "#" bleibt beim Sortieren am Track haengen."""
        node = library().node(
            "USB1", ("TRACK",), column=BrowseColumn.BPM, ascending=False
        )
        self.assertEqual(
            [(e.number, e.track.track_id) for e in node.entries],
            [(3, "c"), (2, "b"), (1, "a")],
        )

    def test_played_tracks_are_marked(self) -> None:
        """S. 42: gespielte Tracks werden gruen dargestellt."""
        node = library(("c",)).node("USB1", ("TRACK",))
        played = {e.track.track_id: e.played for e in node.entries}
        self.assertTrue(played["c"])
        self.assertFalse(played["a"])

    def test_source_without_library_shows_folders(self) -> None:
        """S. 19: ohne rekordbox-Bibliothek Ordner- und Tracklisten."""
        model = library(has_library=False)
        root = model.node("USB1")
        self.assertEqual(root.level_kind, EntryKind.FOLDER)
        self.assertEqual(
            [e.label for e in root.entries], ["schranz", "techno"]
        )
        inside = model.node("USB1", ("schranz",))
        self.assertTrue(inside.is_track_list)
        self.assertEqual(
            [e.track.track_id for e in inside.entries], ["b", "c"]
        )

    def test_unknown_source_stays_empty_instead_of_guessing(self) -> None:
        node = MediaLibrary().node("NOPE")
        self.assertTrue(node.is_empty)
        self.assertEqual(MediaLibrary().sources(), ())


class BrowseViewTests(unittest.TestCase):
    def test_moving_clamps_and_clears_confirmation(self) -> None:
        view = BrowseView(selected=0, confirming=True)
        self.assertEqual(view.moved(+5, 3).selected, 2)
        self.assertEqual(view.moved(-5, 3).selected, 0)
        self.assertFalse(view.moved(+1, 3).confirming)

    def test_path_navigation(self) -> None:
        view = BrowseView().entered("ARTIST").entered("SHORDY")
        self.assertEqual(view.path, ("ARTIST", "SHORDY"))
        self.assertEqual(view.up().path, ("ARTIST",))
        self.assertEqual(view.to_top().path, ())
        self.assertTrue(view.to_top().at_top)

    def test_sorting_toggles_direction_on_the_same_column(self) -> None:
        view = BrowseView().with_sort(BrowseColumn.BPM)
        self.assertTrue(view.ascending)
        self.assertFalse(view.with_sort(BrowseColumn.BPM).ascending)
        self.assertTrue(view.with_sort(BrowseColumn.KEY).ascending)

    def test_display_state_keeps_navigation_separate_from_the_deck(self) -> None:
        display = CdjDisplayState(deck=empty_state(1))
        self.assertEqual(display.browse.path, ())
        self.assertIs(display.rotary, RotaryMode.ZOOM)
        self.assertIs(display.with_rotary_toggled().rotary, RotaryMode.GRID)
        # Kein zweiter Transportzustand.
        for forbidden in ("position_s", "current_bpm", "loop"):
            self.assertNotIn(forbidden, display.__dataclass_fields__)


class MappingTests(unittest.TestCase):
    """Die Taster oberhalb des Displays (S. 15)."""

    def setUp(self) -> None:
        self.commands: list = []
        self.mapper = InputMapper(1, self.commands.append)

    def press(self, control_id: str, event=EventType.PRESS) -> None:
        self.mapper.handle_event(
            InputEvent(
                control_id=control_id,
                control_type=ControlType.DIGITAL_BUTTON,
                event=event,
                source=Source.SCRIPT,
            )
        )

    def test_source_button_opens_the_source_screen(self) -> None:
        self.press(ids.SOURCE)
        self.assertIs(self.commands[-1].type, CommandType.VIEW)
        self.assertIs(self.commands[-1].get("view"), Views.SOURCE)

    def test_menu_button_sends_one_menu_command(self) -> None:
        """MENU ist kontextabhaengig (S. 37 Verlauf, S. 41 Tag-List-Menue).

        Die Zuordnungsschicht kennt die Ansicht nicht und entscheidet
        deshalb nichts - sie meldet nur den Tastendruck. Was daraus wird,
        steht in ``CdjScreen._menu_pressed``.
        """
        self.press(ids.MENU)
        self.assertIs(self.commands[-1].type, CommandType.MENU)

    def test_playlist_button_opens_the_playlist_category(self) -> None:
        self.press(ids.PLAYLIST)
        self.assertIs(self.commands[-1].type, CommandType.VIEW)
        self.assertIs(self.commands[-1].get("view"), Views.BROWSE)
        self.assertEqual(self.commands[-1].get("path"), ("PLAYLIST",))

    def test_tag_list_button_opens_the_tag_list_category(self) -> None:
        self.press(ids.TAG_LIST)
        self.assertIs(self.commands[-1].get("view"), Views.BROWSE)
        self.assertEqual(self.commands[-1].get("path"), ("TAG LIST",))

    def test_tag_track_button_sends_a_toggle(self) -> None:
        self.press(ids.TAG_TRACK_REMOVE)
        self.assertIs(self.commands[-1].type, CommandType.TAG_TRACK_TOGGLE)

    def test_shift_menu_requests_the_personal_settings(self) -> None:
        self.press(ids.SHIFT)
        self.press(ids.MENU)
        self.assertIs(self.commands[-1].type, CommandType.OPEN_SETTINGS)
        self.assertTrue(self.commands[-1].get("shift"))

    def test_back_reports_press_and_release(self) -> None:
        """Nur so ist langes Druecken unterscheidbar (S. 25)."""
        self.press(ids.BACK)
        self.press(ids.BACK, EventType.RELEASE)
        self.assertEqual(
            [c.type for c in self.commands],
            [CommandType.BACK, CommandType.BACK],
        )
        self.assertTrue(self.commands[0].pressed)
        self.assertFalse(self.commands[1].pressed)


class BeatgridCommandTests(unittest.TestCase):
    """Rastereinstellungsmodus (S. 72) - echte Aenderung am Beatgrid."""

    def setUp(self) -> None:
        self.deck = Deck(1)
        self.deck.load_track(
            TrackInfo(
                track_id="a", title="A", original_bpm=120.0, duration_s=60.0,
                beat_grid=BeatGrid(first_beat_s=0.5, bpm=120.0),
            )
        )

    def test_shift_moves_the_grid(self) -> None:
        self.deck.execute(
            command(CommandType.BEATGRID_SHIFT, 1, "TEST", delta_s=0.02)
        )
        grid = self.deck.state.track.beat_grid
        self.assertAlmostEqual(grid.first_beat_s, 0.52, places=6)

    def test_shift_in_beats(self) -> None:
        self.deck.execute(
            command(CommandType.BEATGRID_SHIFT, 1, "TEST", beats=0.5)
        )
        grid = self.deck.state.track.beat_grid
        self.assertAlmostEqual(grid.first_beat_s, 0.75, places=6)

    def test_reset_restores_the_original_grid(self) -> None:
        for _ in range(3):
            self.deck.execute(
                command(CommandType.BEATGRID_SHIFT, 1, "TEST", delta_s=0.02)
            )
        self.deck.execute(command(CommandType.BEATGRID_RESET, 1, "TEST"))
        self.assertAlmostEqual(
            self.deck.state.track.beat_grid.first_beat_s, 0.5, places=6
        )

    def test_bpm_is_unchanged_by_shifting(self) -> None:
        before = self.deck.state.current_bpm
        self.deck.execute(
            command(CommandType.BEATGRID_SHIFT, 1, "TEST", delta_s=0.05)
        )
        self.assertAlmostEqual(self.deck.state.current_bpm, before, places=6)


# --------------------------------------------------------------------------
# Bildschirm
# --------------------------------------------------------------------------


class Clock:
    """Steuerbare Uhr fuer die Halte-Erkennung."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ScreenNavigationTests(unittest.TestCase):
    """Bedienung ueber Drehregler und Taster."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            import tkinter as tk

            probe = tk.Tk()
            probe.destroy()
            cls.tk_available = True
        except Exception:  # pragma: no cover
            cls.tk_available = False

    def setUp(self) -> None:
        if not self.tk_available:
            self.skipTest("keine Tk-Anzeige verfuegbar")
        import tkinter as tk

        from virtual_cdj.cdj_ui.screen import CdjScreen

        self.deck = Deck(2)
        self.provider = LocalDeckStateProvider(self.deck)
        self.clock = Clock()
        self.root = tk.Tk()
        self.root.geometry("1024x600+3000+3000")
        self.root.attributes("-alpha", 0.0)
        self.screen = CdjScreen(
            self.root, self.provider, time_source=self.clock
        )
        self.screen.set_model(library())
        self.screen.pack(fill="both", expand=True)
        self.root.update()
        self.screen.refresh(force=True)

    def tearDown(self) -> None:
        self.screen.stop()
        self.root.destroy()

    # -- Hilfen ---------------------------------------------------------

    def send(self, command_type: CommandType, **params) -> None:
        self.screen.send_command(
            command(command_type, 2, "TEST", **params)
        )

    def rotate(self, delta: int) -> None:
        self.send(CommandType.BROWSE_ROTATE, delta=delta)

    def click(self, hold: float = 0.0) -> None:
        self.send(CommandType.BROWSE_PRESS, pressed=True)
        if hold:
            self.clock.advance(hold)
            self.screen.poll_holds()
        self.send(CommandType.BROWSE_PRESS, pressed=False)

    def back(self, hold: float = 0.0) -> None:
        self.send(CommandType.BACK, pressed=True)
        if hold:
            self.clock.advance(hold)
            self.screen.poll_holds()
        self.send(CommandType.BACK, pressed=False)

    def texts(self, widget) -> str:
        return " | ".join(
            widget.itemcget(item, "text")
            for item in widget.find_all()
            if widget.type(item) == "text"
        )

    def redraw(self) -> None:
        self.root.update()
        self.screen.refresh(force=True)

    # -- SOURCE ---------------------------------------------------------

    def test_source_button_shows_the_real_sources(self) -> None:
        """S. 18: Geraetename und Geraeteinformation."""
        self.send(CommandType.VIEW, view=Views.SOURCE)
        self.redraw()
        text = self.texts(self.screen.source_screen)
        self.assertIn("Pioneer DJ 001", text)
        self.assertIn("Songs", text)
        self.assertIn("3", text)
        self.assertIn("USB", text)

    def test_source_screen_is_empty_without_sources(self) -> None:
        self.screen.set_model(None)
        self.send(CommandType.VIEW, view=Views.SOURCE)
        self.redraw()
        self.assertIn(
            "KEINE QUELLE VERBUNDEN", self.texts(self.screen.source_screen)
        )

    def test_rotary_selects_and_confirms_a_source(self) -> None:
        """S. 24: drehen markiert, druecken bestaetigt."""
        self.send(CommandType.VIEW, view=Views.SOURCE)
        self.redraw()
        self.assertEqual(self.screen.display.source_index, 0)
        self.click()
        self.assertEqual(self.screen.display.source_id, "USB1")
        # Die bestaetigte Quelle fuehrt in die Trackliste.
        self.assertIs(self.screen.view, Views.BROWSE)

    def test_source_touch_needs_two_taps(self) -> None:
        """S. 25: erste Beruehrung markiert, zweite bestaetigt."""
        self.send(CommandType.VIEW, view=Views.SOURCE)
        self.redraw()
        self.send(CommandType.NAV_SELECT, index=0, confirm=False)
        self.assertIs(self.screen.view, Views.SOURCE)
        self.assertTrue(self.screen.display.source_confirming)
        self.send(CommandType.NAV_SELECT, index=0, confirm=True)
        self.assertIs(self.screen.view, Views.BROWSE)

    # -- BROWSE ---------------------------------------------------------

    def test_browse_starts_at_the_categories(self) -> None:
        """S. 19: mit Bibliothek zuerst die Kategorien."""
        self.send(CommandType.VIEW, view=Views.BROWSE)
        node = self.screen.node()
        self.assertEqual(node.level_kind, EntryKind.CATEGORY)
        self.assertEqual(node.entries[0].label, "TRACK")

    def test_rotary_moves_and_press_descends(self) -> None:
        self.send(CommandType.VIEW, view=Views.BROWSE)
        self.rotate(+1)  # ARTIST
        self.assertEqual(self.screen.display.browse.selected, 1)
        self.click()
        self.assertEqual(self.screen.display.browse.path, ("ARTIST",))
        self.assertEqual(
            [e.label for e in self.screen.node().entries],
            ["Aiko", "SHORDY"],
        )

    def test_press_on_a_track_loads_it(self) -> None:
        """S. 24: Druecken auf einem Track laedt und zeigt die Wellenform."""
        loaded: list[str] = []
        self.screen.add_load_hook(loaded.append)
        self.send(CommandType.VIEW, view=Views.BROWSE)
        self.click()  # TRACK-Kategorie betreten
        self.rotate(+1)
        self.click()
        self.assertEqual(loaded, ["b"])
        self.assertIs(self.screen.view, Views.WAVEFORM)

    def test_back_goes_one_level_up_then_to_the_waveform(self) -> None:
        """S. 25: BACK eine Ebene hoeher."""
        self.send(CommandType.VIEW, view=Views.BROWSE)
        self.rotate(+1)
        self.click()
        self.assertEqual(self.screen.display.browse.path, ("ARTIST",))
        self.back()
        self.assertEqual(self.screen.display.browse.path, ())
        self.assertIs(self.screen.view, Views.BROWSE)
        self.back()
        self.assertIs(self.screen.view, Views.WAVEFORM)

    def test_holding_back_jumps_to_the_top_level(self) -> None:
        """S. 25: BACK halten markiert die oberste Ebene."""
        self.send(CommandType.VIEW, view=Views.BROWSE)
        self.rotate(+1)
        self.click()
        self.click()  # in eine Gruppe hinein
        self.assertEqual(len(self.screen.display.browse.path), 2)
        self.back(hold=0.8)
        self.assertEqual(self.screen.display.browse.path, ())
        self.assertIs(self.screen.view, Views.BROWSE)

    def test_touch_on_a_category_column_jumps_there(self) -> None:
        self.send(CommandType.VIEW, view=Views.BROWSE)
        self.send(CommandType.NAV_SELECT, path=("GENRE",))
        self.assertEqual(self.screen.display.browse.path, ("GENRE",))
        self.assertEqual(
            [e.label for e in self.screen.node().entries],
            ["Industrial", "Techno"],
        )

    def test_sorting_and_header_toggles(self) -> None:
        """S. 19-20: Sortierung sowie PREVIEW, Schrift und INFO."""
        self.send(CommandType.VIEW, view=Views.BROWSE)
        self.click()  # TRACK
        self.send(CommandType.BROWSE_SORT, column=BrowseColumn.BPM)
        self.assertIs(
            self.screen.display.browse.sort_column, BrowseColumn.BPM
        )
        self.assertEqual(
            [e.track.track_id for e in self.screen.node().entries],
            ["a", "b", "c"],
        )
        self.send(CommandType.BROWSE_SORT, column=BrowseColumn.BPM)
        self.assertEqual(
            [e.track.track_id for e in self.screen.node().entries],
            ["c", "b", "a"],
        )

        browse = self.screen.display.browse
        self.assertTrue(browse.preview)
        self.send(CommandType.BROWSE_TOGGLE, option="PREVIEW")
        self.assertFalse(self.screen.display.browse.preview)
        self.send(CommandType.BROWSE_TOGGLE, option="INFO")
        self.assertTrue(self.screen.display.browse.info)
        self.send(CommandType.BROWSE_TOGGLE, option="FONT")
        self.assertTrue(self.screen.display.browse.large_font)
        self.redraw()  # zeichnet mit Infospalte und grosser Schrift

    def test_hold_rotary_enables_alphabet_jump(self) -> None:
        """S. 38: Drehregler halten schaltet den Sprungmodus ein."""
        self.send(CommandType.VIEW, view=Views.BROWSE)
        self.click()  # TRACK-Liste
        self.send(CommandType.BROWSE_SORT, column=BrowseColumn.TITLE)
        self.click(hold=0.8)
        self.assertIs(self.screen.display.browse.jump, JumpMode.ALPHABET)
        # Alpha Drive -> Broken Window: naechster anderer Anfangsbuchstabe
        self.rotate(+1)
        self.assertEqual(self.screen.display.browse.selected, 1)
        self.redraw()
        self.assertIn("ALPHABET", self.texts(self.screen.browser))
        self.click(hold=0.8)
        self.assertIs(self.screen.display.browse.jump, JumpMode.OFF)

    def test_status_bar_stays_visible_while_browsing(self) -> None:
        """S. 19, Element 9: Wiedergabestatusanzeige bleibt unten."""
        self.send(CommandType.VIEW, view=Views.BROWSE)
        self.redraw()
        self.assertTrue(self.screen.status_bar.winfo_ismapped())
        self.assertTrue(self.screen.overview.winfo_ismapped())

    # -- Drehregler auf dem Wiedergabebildschirm --------------------------

    def test_rotary_zooms_the_waveform(self) -> None:
        before = self.screen.display.window_s
        self.rotate(+1)
        self.assertGreater(self.screen.display.window_s, before)

    def test_holding_rotary_switches_to_grid_mode(self) -> None:
        """S. 22, Element 11: halten wechselt Zoom <-> Rastereinstellung."""
        self.deck.load_track(
            TrackInfo(
                track_id="x", title="X", original_bpm=120.0, duration_s=60.0,
                beat_grid=BeatGrid(first_beat_s=0.5, bpm=120.0),
            )
        )
        self.click(hold=0.8)
        self.assertIs(self.screen.display.rotary, RotaryMode.GRID)

        window_before = self.screen.display.window_s
        self.rotate(+2)
        # Im Rastermodus zoomt der Regler nicht, er verschiebt das Raster.
        self.assertEqual(self.screen.display.window_s, window_before)
        self.assertGreater(self.deck.state.track.beat_grid.first_beat_s, 0.5)

        self.click(hold=0.8)
        self.assertIs(self.screen.display.rotary, RotaryMode.ZOOM)

    def test_view_switch_does_not_touch_the_deck(self) -> None:
        before = self.deck.state.generation
        self.send(CommandType.VIEW, view=Views.SOURCE)
        self.send(CommandType.VIEW, view=Views.BROWSE)
        self.rotate(+1)
        self.assertEqual(self.deck.state.generation, before)


class PanelChainTests(unittest.TestCase):
    """Die Taster und der Drehgeber des Bedienfelds am echten Bildschirm.

    Gedrueckt wird ueber die ``VirtualSource`` - denselben Weg, den spaeter
    der ESP32 nimmt:

        VirtualSource -> InputLayer -> InputMapper -> DeckCommand -> Screen
    """

    @classmethod
    def setUpClass(cls) -> None:
        try:
            import tkinter as tk

            probe = tk.Tk()
            probe.destroy()
            cls.tk_available = True
        except Exception:  # pragma: no cover
            cls.tk_available = False

    def setUp(self) -> None:
        if not self.tk_available:
            self.skipTest("keine Tk-Anzeige verfuegbar")
        import tkinter as tk

        from virtual_cdj.app import CdjApplication
        from virtual_cdj.cdj_ui.screen import CdjScreen

        self.app = CdjApplication(
            [1], start_audio=False, operating_mode=OperatingMode.CDJ
        )
        self.app.library.add(
            TrackListLibrary(
                SourceInfo(
                    source_id="USB1", name="Pioneer DJ 001",
                    kind=SourceKind.USB, player_number=1, has_library=True,
                ),
                lambda: TRACKS,
                history=lambda: ("c",),
            )
        )
        self.clock = Clock()
        self.root = tk.Tk()
        self.root.geometry("1024x600+3000+3000")
        self.root.attributes("-alpha", 0.0)
        self.screen = CdjScreen(
            self.root, self.app.providers[1], time_source=self.clock
        )
        self.screen.pack(fill="both", expand=True)
        # Die echte Verdrahtung aus run_cdj.py - keine Nachbildung.
        import run_cdj

        run_cdj.wire_display(self.app, 1, self.screen)
        self.root.update()
        self.screen.refresh(force=True)

    def tearDown(self) -> None:
        self.screen.stop()
        self.root.destroy()
        self.app.close()

    # -- Bedienung ueber das Bedienfeld ---------------------------------

    def tap(self, control_id: str, hold: float = 0.0) -> None:
        source = self.app.virtual_source
        source.press(control_id)
        if hold:
            self.clock.advance(hold)
            self.screen.poll_holds()
        source.release(control_id)

    def turn(self, delta: int) -> None:
        self.app.virtual_source.rotate(ids.BROWSE_ROTATE, delta)

    # -- Tests ----------------------------------------------------------

    def test_source_button_opens_the_source_screen(self) -> None:
        self.tap(ids.SOURCE)
        self.assertIs(self.screen.view, Views.SOURCE)

    def test_browse_button_opens_the_browse_screen(self) -> None:
        self.tap(ids.BROWSE)
        self.assertIs(self.screen.view, Views.BROWSE)

    def test_search_button_opens_the_browse_screen(self) -> None:
        """Ersatz, solange der Suchbildschirm fehlt - dokumentiert."""
        self.tap(ids.SEARCH)
        self.assertIs(self.screen.view, Views.BROWSE)

    def test_menu_button_shows_the_history(self) -> None:
        self.tap(ids.MENU)
        self.assertIs(self.screen.view, Views.BROWSE)
        self.assertEqual(self.screen.display.browse.path, ("HISTORY",))

    def test_rotary_turns_press_and_loads_through_the_panel(self) -> None:
        loaded: list[str] = []
        self.screen.add_load_hook(loaded.append)

        self.tap(ids.SOURCE)
        self.tap(ids.BROWSE_PRESS)          # Quelle bestaetigen
        self.assertEqual(self.screen.display.source_id, "USB1")
        self.assertIs(self.screen.view, Views.BROWSE)

        self.turn(+1)                        # ARTIST markieren
        self.assertEqual(self.screen.display.browse.selected, 1)
        self.tap(ids.BROWSE_PRESS)           # ARTIST betreten
        self.assertEqual(self.screen.display.browse.path, ("ARTIST",))
        self.tap(ids.BROWSE_PRESS)           # erste Gruppe betreten
        self.assertEqual(len(self.screen.display.browse.path), 2)
        self.tap(ids.BROWSE_PRESS)           # Track laden
        self.assertEqual(len(loaded), 1)
        self.assertIs(self.screen.view, Views.WAVEFORM)

    def test_back_button_walks_up_and_leaves(self) -> None:
        self.tap(ids.BROWSE)
        self.turn(+1)
        self.tap(ids.BROWSE_PRESS)
        self.assertEqual(self.screen.display.browse.path, ("ARTIST",))
        self.tap(ids.BACK)
        self.assertEqual(self.screen.display.browse.path, ())
        self.tap(ids.BACK)
        self.assertIs(self.screen.view, Views.WAVEFORM)

    def test_holding_back_returns_to_the_top_level(self) -> None:
        self.tap(ids.BROWSE)
        self.turn(+1)
        self.tap(ids.BROWSE_PRESS)
        self.tap(ids.BROWSE_PRESS)
        self.assertEqual(len(self.screen.display.browse.path), 2)
        self.tap(ids.BACK, hold=0.8)
        self.assertEqual(self.screen.display.browse.path, ())

    def test_holding_the_rotary_switches_the_grid_mode(self) -> None:
        self.tap(ids.BROWSE_PRESS, hold=0.8)
        self.assertIs(self.screen.display.rotary, RotaryMode.GRID)

    def test_holding_the_rotary_enables_the_jump_mode(self) -> None:
        self.tap(ids.BROWSE)
        self.tap(ids.BROWSE_PRESS)           # TRACK-Liste
        self.tap(ids.BROWSE_PRESS, hold=0.8)
        self.assertIsNot(self.screen.display.browse.jump, JumpMode.OFF)

    def test_rotary_zooms_on_the_playback_screen(self) -> None:
        before = self.screen.display.zoom_beats
        self.turn(+1)
        self.assertGreater(self.screen.display.zoom_beats, before)

    def test_transport_still_reaches_the_deck(self) -> None:
        """Der Bildschirm faengt nur Anzeigebefehle ab, nichts anderes."""
        self.app.decks[1].load_track(
            TrackInfo(track_id="a", title="A", duration_s=60.0)
        )
        self.tap(ids.PLAY)
        self.assertTrue(self.app.decks[1].state.is_playing)
        self.tap(ids.QUANTIZE)
        self.assertTrue(self.app.decks[1].state.quantize)

    def test_without_a_screen_the_commands_still_reach_the_deck(self) -> None:
        """Ohne Oberflaeche bleibt die alte Verdrahtung gueltig."""
        self.app.set_command_sink(1, None)
        self.app.decks[1].load_track(
            TrackInfo(track_id="a", title="A", duration_s=60.0)
        )
        self.tap(ids.PLAY)
        self.assertTrue(self.app.decks[1].state.is_playing)


class ApplicationSourceTests(unittest.TestCase):
    """Verdrahtung: Quellen und Verlauf in der Anwendung."""

    def test_history_is_recorded_after_a_minute_of_play(self) -> None:
        """S. 42: Tracks nach etwa einer Minute Wiedergabe im Verlauf."""
        from virtual_cdj.app import CdjApplication

        app = CdjApplication(
            [1], start_audio=False, operating_mode=OperatingMode.CDJ
        )
        try:
            app.decks[1].load_track(
                TrackInfo(
                    track_id="a", title="A", duration_s=300.0,
                    original_bpm=120.0,
                )
            )
            app.decks[1].execute(command(CommandType.PLAY_PAUSE, 1, "TEST"))
            app.update_history(30.0)
            self.assertEqual(app.history, [])
            app.update_history(40.0)
            self.assertEqual(app.history, ["a"])
            # Kein zweiter Eintrag fuer denselben Track.
            app.update_history(60.0)
            self.assertEqual(app.history, ["a"])
        finally:
            app.close()

    def test_paused_playback_does_not_fill_the_history(self) -> None:
        from virtual_cdj.app import CdjApplication

        app = CdjApplication(
            [1], start_audio=False, operating_mode=OperatingMode.CDJ
        )
        try:
            app.decks[1].load_track(
                TrackInfo(track_id="a", title="A", duration_s=300.0)
            )
            app.update_history(120.0)
            self.assertEqual(app.history, [])
        finally:
            app.close()

    def test_without_demo_there_is_no_source(self) -> None:
        """Es werden keine Geraete erfunden."""
        from virtual_cdj.app import CdjApplication

        app = CdjApplication(
            [1], start_audio=False, operating_mode=OperatingMode.CDJ
        )
        try:
            self.assertEqual(app.library.sources(), ())
        finally:
            app.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
