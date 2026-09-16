"""PLAYLIST, TAG LIST und die Sortierung der Tracklisten.

Geprueft wird die Kette, die die Aufgabe verlangt:

    Taste -> InputMapper -> DeckCommand -> CdjScreen -> MediaLibrary

und dass alle vier Einstiege (BROWSE, PLAYLIST, TAG LIST, Verlauf)
**dieselbe** Bibliothek, **dieselbe** Sortierfunktion und **dieselbe**
Ladefunktion benutzen.

Die Tracks sind hier von Hand gebaut - der Rekordbox-Leser hat seine
eigenen Tests (``test_usb_sources.py``). Keiner dieser Tests fasst ein
Laufwerk an.
"""

from __future__ import annotations

import unittest

from virtual_cdj.deck.commands import CommandType, Views, command
from virtual_cdj.deck.display_state import (
    BrowseContext,
    BrowseView,
    TagMenuAction,
)
from virtual_cdj.deck.library import (
    PLAYLIST_CATEGORY,
    TAG_LIST_CATEGORY,
    BrowseColumn,
    BrowseEntry,
    EntryKind,
    MediaLibrary,
    SourceInfo,
    SourceKind,
    TrackListLibrary,
    sort_entries,
)
from virtual_cdj.deck.state import TrackInfo
from virtual_cdj.media_library.taglist import (
    MAX_TAG_LIST_TRACKS,
    TagListService,
)

SOURCE_ID = "USB:TEST"


def track(
    track_id: str,
    title: str = "",
    *,
    artist: str = "",
    bpm: float = 0.0,
    key: str = "",
    rating: int = 0,
    colour: str = "",
    duration_s: float = 0.0,
) -> TrackInfo:
    return TrackInfo(
        track_id=track_id,
        title=title or track_id,
        artist=artist,
        original_bpm=bpm,
        key=key,
        rating=rating,
        color=colour,
        duration_s=duration_s,
        file_path=f"X:\\Contents\\{track_id}.mp3",
        source="USB",
    )


#: Eine kleine Bibliothek mit absichtlich luecken- und kontrastreichen
#: Werten: fehlende BPM, fehlende Bewertung, fehlende Tonart.
TRACKS: tuple[TrackInfo, ...] = (
    track("t1", "Charlie", artist="Zoe", bpm=154.0, key="8A", rating=3,
          colour="Red", duration_s=200.0),
    track("t2", "Alpha", artist="Yannick", bpm=99.0, key="2B", rating=5,
          duration_s=320.0),
    track("t3", "Bravo", artist="Xenia", bpm=0.0, rating=0,
          colour="Blue", duration_s=120.0),
    track("t4", "Delta", artist="Wanda", bpm=148.0, key="1A", rating=1,
          duration_s=0.0),
    track("t5", "Echo", artist="Viktor", bpm=160.0, key="5A", rating=0,
          duration_s=245.0),
)

#: Playlists in einer Reihenfolge, die **nicht** der Trackliste entspricht.
PLAYLISTS: dict[str, tuple[str, ...]] = {
    "Abends": ("t4", "t1", "t5"),
    "Ordner / Innen": ("t2", "t3"),
}


def build_model(
    tags: TagListService | None = None,
    *,
    playlists: dict[str, tuple[str, ...]] | None = None,
    tracks: tuple[TrackInfo, ...] = TRACKS,
) -> MediaLibrary:
    model = MediaLibrary()
    model.add(
        TrackListLibrary(
            SourceInfo(
                source_id=SOURCE_ID, name="TESTSTICK",
                kind=SourceKind.USB, has_library=True,
            ),
            lambda: tracks,
            playlists=lambda: (
                PLAYLISTS if playlists is None else playlists
            ),
            tag_list=(
                tags.ids_for(SOURCE_ID) if tags is not None else None
            ),
        )
    )
    return model


def titles(node) -> list[str]:
    return [entry.label for entry in node.entries]


# ----------------------------------------------------------------------
# Sortierung
# ----------------------------------------------------------------------


class SortTests(unittest.TestCase):
    """Eine Sortierfunktion fuer alle Listen (Abschnitt 8-15)."""

    def setUp(self) -> None:
        self.library = build_model().library(SOURCE_ID)

    def sorted_titles(
        self, column: BrowseColumn, *, ascending: bool = True
    ) -> list[str]:
        node = self.library.node(
            ("TRACK",), column=column, ascending=ascending
        )
        return titles(node)

    def test_original_order_is_the_source_order(self) -> None:
        self.assertEqual(
            self.sorted_titles(BrowseColumn.NUMBER),
            ["Charlie", "Alpha", "Bravo", "Delta", "Echo"],
        )

    def test_title_sorts_alphabetically_both_ways(self) -> None:
        self.assertEqual(
            self.sorted_titles(BrowseColumn.TITLE),
            ["Alpha", "Bravo", "Charlie", "Delta", "Echo"],
        )
        self.assertEqual(
            self.sorted_titles(BrowseColumn.TITLE, ascending=False),
            ["Echo", "Delta", "Charlie", "Bravo", "Alpha"],
        )

    def test_bpm_sorts_numerically_not_as_text(self) -> None:
        """99 gehoert vor 148 - als Text waere es umgekehrt."""
        order = self.sorted_titles(BrowseColumn.BPM)
        self.assertEqual(order[:4], ["Alpha", "Delta", "Charlie", "Echo"])

    def test_missing_bpm_goes_last_in_both_directions(self) -> None:
        """Ein fehlender Wert ist kein Messwert und steht nie vorne."""
        self.assertEqual(self.sorted_titles(BrowseColumn.BPM)[-1], "Bravo")
        self.assertEqual(
            self.sorted_titles(BrowseColumn.BPM, ascending=False)[-1], "Bravo"
        )

    def test_rating_sorts_numerically(self) -> None:
        order = self.sorted_titles(BrowseColumn.RATING, ascending=False)
        self.assertEqual(order[:3], ["Alpha", "Charlie", "Delta"])
        # Ohne Bewertung ganz hinten.
        self.assertEqual(set(order[3:]), {"Bravo", "Echo"})

    def test_length_sorts_numerically(self) -> None:
        order = self.sorted_titles(BrowseColumn.TIME)
        self.assertEqual(order[:3], ["Bravo", "Charlie", "Echo"])
        self.assertEqual(order[-1], "Delta")  # ohne Laenge

    def test_key_uses_the_stored_value_without_conversion(self) -> None:
        order = self.sorted_titles(BrowseColumn.KEY)
        self.assertEqual(order[:4], ["Delta", "Alpha", "Echo", "Charlie"])
        self.assertEqual(order[-1], "Bravo")  # ohne Tonart

    def test_colour_sorts_by_the_stored_colour(self) -> None:
        order = self.sorted_titles(BrowseColumn.COLOR)
        self.assertEqual(order[:2], ["Bravo", "Charlie"])  # Blue, Red
        # Ohne Farbe ans Ende - es wird keine erfunden.
        self.assertEqual(set(order[2:]), {"Alpha", "Delta", "Echo"})

    def test_sorting_does_not_touch_the_source_list(self) -> None:
        """Abschnitt 10: sortiert wird eine Sicht, nicht die Quelle."""
        before = [t.track_id for t in self.library.tracks()]
        self.library.node(("TRACK",), column=BrowseColumn.BPM)
        self.library.node(("TRACK",), column=BrowseColumn.TITLE, ascending=False)
        self.assertEqual([t.track_id for t in self.library.tracks()], before)

    def test_sort_entries_returns_a_new_sequence(self) -> None:
        entries = tuple(
            BrowseEntry(kind=EntryKind.TRACK, label=t.title, track=t, number=i)
            for i, t in enumerate(TRACKS, start=1)
        )
        result = sort_entries(entries, BrowseColumn.TITLE)
        self.assertEqual([e.label for e in entries][0], "Charlie")
        self.assertEqual([e.label for e in result][0], "Alpha")


# ----------------------------------------------------------------------
# Playlists
# ----------------------------------------------------------------------


class PlaylistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.library = build_model().library(SOURCE_ID)

    def test_playlist_category_lists_the_playlists(self) -> None:
        node = self.library.node((PLAYLIST_CATEGORY,))
        self.assertEqual(titles(node), ["Abends", "Ordner / Innen"])
        self.assertEqual([e.count for e in node.entries], [3, 2])

    def test_playlist_opens_in_the_stored_order(self) -> None:
        """Abschnitt 11: zuerst die Reihenfolge des Sticks."""
        node = self.library.node((PLAYLIST_CATEGORY, "Abends"))
        self.assertEqual(titles(node), ["Delta", "Charlie", "Echo"])

    def test_playlist_can_be_sorted_without_losing_the_original(self) -> None:
        by_bpm = self.library.node(
            (PLAYLIST_CATEGORY, "Abends"), column=BrowseColumn.BPM
        )
        self.assertEqual(titles(by_bpm), ["Delta", "Charlie", "Echo"])
        by_bpm_desc = self.library.node(
            (PLAYLIST_CATEGORY, "Abends"),
            column=BrowseColumn.BPM, ascending=False,
        )
        self.assertEqual(titles(by_bpm_desc), ["Echo", "Charlie", "Delta"])
        # Zurueck zur gespeicherten Reihenfolge.
        again = self.library.node((PLAYLIST_CATEGORY, "Abends"))
        self.assertEqual(titles(again), ["Delta", "Charlie", "Echo"])

    def test_playlist_numbers_count_within_the_playlist(self) -> None:
        node = self.library.node((PLAYLIST_CATEGORY, "Abends"))
        self.assertEqual([e.number for e in node.entries], [1, 2, 3])

    def test_source_without_playlists_has_no_playlist_category(self) -> None:
        """Abschnitt 22: keine erfundenen Playlists."""
        library = build_model(playlists={}).library(SOURCE_ID)
        self.assertNotIn(PLAYLIST_CATEGORY, library.categories())
        node = library.node((PLAYLIST_CATEGORY,))
        self.assertTrue(node.is_empty)


# ----------------------------------------------------------------------
# Der Tag-List-Dienst
# ----------------------------------------------------------------------


class TagListServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tags = TagListService()

    def test_add_and_contains(self) -> None:
        result = self.tags.add(SOURCE_ID, "t1")
        self.assertTrue(result.changed)
        self.assertTrue(result.tagged)
        self.assertTrue(self.tags.contains(SOURCE_ID, "t1"))

    def test_no_duplicates_and_order_is_kept(self) -> None:
        for track_id in ("t3", "t1", "t2"):
            self.tags.add(SOURCE_ID, track_id)
        again = self.tags.add(SOURCE_ID, "t3")
        self.assertFalse(again.changed)
        self.assertTrue(again.tagged)
        # Der zweite Versuch schiebt den Track auch nicht ans Ende.
        self.assertEqual(self.tags.track_ids(SOURCE_ID), ("t3", "t1", "t2"))

    def test_remove_only_drops_the_entry(self) -> None:
        self.tags.add(SOURCE_ID, "t1")
        self.tags.add(SOURCE_ID, "t2")
        self.tags.remove(SOURCE_ID, "t1")
        self.assertEqual(self.tags.track_ids(SOURCE_ID), ("t2",))

    def test_toggle_switches_both_ways(self) -> None:
        self.assertTrue(self.tags.toggle(SOURCE_ID, "t1").tagged)
        self.assertFalse(self.tags.toggle(SOURCE_ID, "t1").tagged)

    def test_hundred_tracks_fit_the_hundred_and_first_does_not(self) -> None:
        for index in range(MAX_TAG_LIST_TRACKS):
            self.assertTrue(self.tags.add(SOURCE_ID, f"t{index}").changed)
        self.assertEqual(self.tags.count(SOURCE_ID), MAX_TAG_LIST_TRACKS)
        self.assertTrue(self.tags.is_full(SOURCE_ID))
        overflow = self.tags.add(SOURCE_ID, "zuviel")
        self.assertFalse(overflow.changed)
        self.assertIn("voll", overflow.message)
        self.assertFalse(self.tags.contains(SOURCE_ID, "zuviel"))

    def test_lists_are_kept_apart_per_device(self) -> None:
        self.tags.add("USB:A", "t1")
        self.tags.add("USB:B", "t2")
        self.assertEqual(self.tags.track_ids("USB:A"), ("t1",))
        self.assertEqual(self.tags.track_ids("USB:B"), ("t2",))

    def test_clear_empties_only_that_device(self) -> None:
        self.tags.add("USB:A", "t1")
        self.tags.add("USB:B", "t2")
        self.tags.clear("USB:A")
        self.assertEqual(self.tags.track_ids("USB:A"), ())
        self.assertEqual(self.tags.track_ids("USB:B"), ("t2",))

    def test_create_playlist_is_refused_without_a_write_layer(self) -> None:
        """Abschnitt 7: keine rekordbox-Datenbank beschaedigen."""
        self.tags.add(SOURCE_ID, "t1")
        self.assertFalse(self.tags.can_create_playlist)
        result = self.tags.create_playlist(SOURCE_ID)
        self.assertFalse(result.changed)
        self.assertIn("nicht verfuegbar", result.message)

    def test_playlist_names_follow_the_cdj_scheme(self) -> None:
        class Writer:
            def __init__(self) -> None:
                self.created: list[tuple[str, tuple[str, ...]]] = []
                self.names = ["TAG LIST 001", "Andere Liste"]

            def existing_playlist_names(self, device_id: str):
                return self.names

            def create_playlist(self, device_id, name, track_ids):
                self.names.append(name)
                self.created.append((name, tuple(track_ids)))

        writer = Writer()
        tags = TagListService(writer=writer)
        tags.add(SOURCE_ID, "t1")
        self.assertEqual(tags.next_playlist_name(SOURCE_ID), "TAG LIST 002")
        result = tags.create_playlist(SOURCE_ID)
        self.assertTrue(result.changed)
        self.assertEqual(writer.created, [("TAG LIST 002", ("t1",))])
        self.assertEqual(tags.next_playlist_name(SOURCE_ID), "TAG LIST 003")


# ----------------------------------------------------------------------
# Tag List als Kategorie derselben Bibliothek
# ----------------------------------------------------------------------


class TagListInLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tags = TagListService()
        self.model = build_model(self.tags)
        self.library = self.model.library(SOURCE_ID)

    def test_category_exists_even_when_empty(self) -> None:
        """Die Arbeitsliste muss auffindbar sein, bevor etwas drinsteht."""
        self.assertIn(TAG_LIST_CATEGORY, self.library.categories())
        self.assertTrue(self.library.node((TAG_LIST_CATEGORY,)).is_empty)

    def test_tagged_tracks_appear_in_the_category(self) -> None:
        self.tags.add(SOURCE_ID, "t3")
        self.tags.add(SOURCE_ID, "t1")
        node = self.library.node((TAG_LIST_CATEGORY,))
        self.assertEqual(titles(node), ["Bravo", "Charlie"])

    def test_tag_list_is_sortable_like_every_other_list(self) -> None:
        for track_id in ("t3", "t1", "t2"):
            self.tags.add(SOURCE_ID, track_id)
        node = self.library.node(
            (TAG_LIST_CATEGORY,), column=BrowseColumn.TITLE
        )
        self.assertEqual(titles(node), ["Alpha", "Bravo", "Charlie"])
        # Und zurueck in die Reihenfolge des Hinzufuegens.
        original = self.library.node((TAG_LIST_CATEGORY,))
        self.assertEqual(titles(original), ["Bravo", "Charlie", "Alpha"])

    def test_tagged_flag_shows_up_in_every_list(self) -> None:
        self.tags.add(SOURCE_ID, "t2")
        node = self.library.node(("TRACK",))
        by_title = {e.label: e for e in node.entries}
        self.assertTrue(by_title["Alpha"].tagged)
        self.assertFalse(by_title["Bravo"].tagged)
        # Auch in einer Playlist.
        playlist = self.library.node((PLAYLIST_CATEGORY, "Ordner / Innen"))
        self.assertTrue(playlist.entries[0].tagged)

    def test_unknown_ids_fall_away_instead_of_breaking_the_list(self) -> None:
        """Nach einem Stickwechsel zeigt keine Zeile ins Leere."""
        self.tags.add(SOURCE_ID, "t1")
        self.tags.add(SOURCE_ID, "gibt-es-nicht")
        node = self.library.node((TAG_LIST_CATEGORY,))
        self.assertEqual(titles(node), ["Charlie"])

    def test_tag_list_uses_the_same_tracks_as_browse(self) -> None:
        """Abschnitt 26: eine Bibliothek, mehrere Ansichten."""
        self.tags.add(SOURCE_ID, "t1")
        from_browse = self.library.node(("TRACK",)).entries[0].track
        from_tags = self.library.node((TAG_LIST_CATEGORY,)).entries[0].track
        self.assertIs(from_browse, from_tags)


# ----------------------------------------------------------------------
# Bildschirm: Tasten, Kontexte, Laden
# ----------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.withdraw()
    _probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - kopfloser Rechner
    TK_AVAILABLE = False


@unittest.skipUnless(TK_AVAILABLE, "keine Tk-Anzeige verfuegbar")
class ScreenTagListTests(unittest.TestCase):
    """Die Kette Taste -> Kommando -> Bildschirm -> Bibliothek."""

    def setUp(self) -> None:
        import tkinter as tk

        from virtual_cdj.cdj_ui.screen import CdjScreen
        from virtual_cdj.deck.engine import Deck
        from virtual_cdj.deck.provider import LocalDeckStateProvider

        self.deck = Deck(1)
        self.provider = LocalDeckStateProvider(self.deck)
        self.clock = Clock()
        self.root = tk.Tk()
        self.root.geometry("1024x600+3000+3000")
        self.root.attributes("-alpha", 0.0)
        self.screen = CdjScreen(
            self.root, self.provider, time_source=self.clock
        )
        self.screen.pack(fill="both", expand=True)
        self.root.update()

        self.tags = TagListService()
        self.screen.tag_list = self.tags
        self.screen.set_model(build_model(self.tags))
        self.loaded: list[str] = []
        self.screen.add_load_hook(self.loaded.append)
        self.root.update()

    def tearDown(self) -> None:
        self.screen.stop()
        self.root.destroy()

    # -- Hilfen --------------------------------------------------------

    def send(self, command_type: CommandType, **params) -> None:
        self.screen.send_command(command(command_type, 1, "TEST", **params))
        self.root.update()

    def open(self, *path: str) -> None:
        self.send(
            CommandType.VIEW, view=Views.BROWSE, path=tuple(path)
        )

    def rotate(self, delta: int) -> None:
        self.send(CommandType.BROWSE_ROTATE, delta=delta)

    def press_rotary(self) -> None:
        """Kurzer Druck auf den Load-Regler."""
        self.send(CommandType.BROWSE_PRESS, pressed=True)
        self.clock.now += 0.05
        self.send(CommandType.BROWSE_PRESS, pressed=False)

    def listing(self) -> list[str]:
        return titles(self.screen.browser.list_node())

    # -- TEST 1: USB -> PLAYLIST -> Playlist -> Track laden -------------

    def test_playlist_button_opens_playlists_and_loads_a_track(self) -> None:
        self.open(PLAYLIST_CATEGORY)
        self.assertIs(self.screen.view, Views.BROWSE)
        self.assertEqual(self.listing(), ["Abends", "Ordner / Innen"])

        self.press_rotary()                      # Playlist "Abends" oeffnen
        self.assertEqual(self.listing(), ["Delta", "Charlie", "Echo"])

        self.rotate(1)                           # auf "Charlie"
        self.press_rotary()                      # laden
        self.assertEqual(self.loaded, ["t1"])
        # Laden fuehrt zurueck auf den Wiedergabebildschirm.
        self.assertIs(self.screen.view, Views.WAVEFORM)

    def test_back_leaves_the_playlist_before_the_category(self) -> None:
        self.open(PLAYLIST_CATEGORY)
        self.press_rotary()
        self.assertEqual(len(self.screen.display.browse.path), 2)
        self.send(CommandType.BACK, pressed=True)
        self.clock.now += 0.05
        self.send(CommandType.BACK, pressed=False)
        self.assertEqual(
            self.screen.display.browse.path, (PLAYLIST_CATEGORY,)
        )

    # -- TEST 2-5: Tag List --------------------------------------------

    def test_tagging_a_marked_track_puts_it_in_the_tag_list(self) -> None:
        self.open("TRACK")
        self.send(CommandType.TAG_TRACK_TOGGLE)
        self.assertEqual(self.tags.track_ids(SOURCE_ID), ("t1",))

        self.open(TAG_LIST_CATEGORY)
        self.assertEqual(self.listing(), ["Charlie"])

    def test_tagging_twice_removes_instead_of_duplicating(self) -> None:
        self.open("TRACK")
        self.send(CommandType.TAG_TRACK_TOGGLE)
        self.send(CommandType.TAG_TRACK_TOGGLE)
        self.assertEqual(self.tags.track_ids(SOURCE_ID), ())

    def test_tagging_from_a_playlist_uses_the_same_list(self) -> None:
        self.open(PLAYLIST_CATEGORY)
        self.press_rotary()                       # "Abends"
        self.send(CommandType.TAG_TRACK_TOGGLE)   # "Delta"
        self.assertEqual(self.tags.track_ids(SOURCE_ID), ("t4",))

    def test_tagging_on_the_waveform_screen_uses_the_loaded_track(self) -> None:
        """S. 40: dort meint die Taste den geladenen, nicht einen markierten."""
        self.deck.load_track(TRACKS[1])           # t2
        self.screen.set_view(Views.WAVEFORM)
        # Im Betrieb holt die Bildschleife den Deck-Zustand ab; im Test
        # gibt es keine Schleife, also einmal von Hand.
        self.screen.refresh(force=True)
        self.root.update()
        self.send(CommandType.TAG_TRACK_TOGGLE)
        self.assertEqual(self.tags.track_ids(SOURCE_ID), ("t2",))

    def test_loading_from_the_tag_list_uses_the_normal_load_path(self) -> None:
        self.tags.add(SOURCE_ID, "t5")
        self.open(TAG_LIST_CATEGORY)
        self.press_rotary()
        self.assertEqual(self.loaded, ["t5"])

    # -- TEST 6-8: Sortieren ueber den Spaltenkopf ----------------------

    def test_column_header_sorts_and_reverses(self) -> None:
        self.open(PLAYLIST_CATEGORY)
        self.press_rotary()
        self.assertEqual(self.listing(), ["Delta", "Charlie", "Echo"])

        self.send(CommandType.BROWSE_SORT, column=BrowseColumn.BPM)
        self.assertEqual(self.listing(), ["Delta", "Charlie", "Echo"])
        self.assertTrue(self.screen.display.browse.ascending)

        self.send(CommandType.BROWSE_SORT, column=BrowseColumn.BPM)
        self.assertFalse(self.screen.display.browse.ascending)
        self.assertEqual(self.listing(), ["Echo", "Charlie", "Delta"])

    def test_number_column_restores_the_original_order(self) -> None:
        self.open(PLAYLIST_CATEGORY)
        self.press_rotary()
        self.send(CommandType.BROWSE_SORT, column=BrowseColumn.TITLE)
        self.assertEqual(self.listing(), ["Charlie", "Delta", "Echo"])
        self.send(CommandType.BROWSE_SORT, column=BrowseColumn.NUMBER)
        self.assertTrue(self.screen.display.browse.is_original_order)
        self.assertEqual(self.listing(), ["Delta", "Charlie", "Echo"])

    def test_a_freshly_opened_playlist_starts_in_stored_order(self) -> None:
        """Abschnitt 11: eine andere Liste erbt keine fremde Sortierung."""
        self.open("TRACK")
        self.send(CommandType.BROWSE_SORT, column=BrowseColumn.TITLE)
        self.open(PLAYLIST_CATEGORY)
        self.press_rotary()
        self.assertTrue(self.screen.display.browse.is_original_order)
        self.assertEqual(self.listing(), ["Delta", "Charlie", "Echo"])

    # -- TEST 13: Zustand beim Wechseln ---------------------------------

    def test_each_entry_point_keeps_its_own_position(self) -> None:
        self.open("TRACK")
        self.rotate(3)                             # vierter Track
        selected = self.screen.display.browse.selected
        self.assertEqual(selected, 3)

        self.open(PLAYLIST_CATEGORY)
        self.assertIs(
            self.screen.display.browse.context, BrowseContext.PLAYLIST
        )
        self.rotate(1)

        self.open(TAG_LIST_CATEGORY)
        self.assertIs(
            self.screen.display.browse.context, BrowseContext.TAG_LIST
        )

        # Zurueck zu BROWSE: dieselbe Stelle wie vorher.
        self.send(CommandType.VIEW, view=Views.BROWSE, path=())
        self.assertIs(
            self.screen.display.browse.context, BrowseContext.LIBRARY
        )
        self.assertEqual(self.screen.display.browse.selected, selected)

        # Und zurueck in die Playlist-Kategorie: ebenfalls wie verlassen.
        self.open(PLAYLIST_CATEGORY)
        self.assertEqual(self.screen.display.browse.selected, 1)

    # -- Abschnitt 6/7: Tag-List-Menue ----------------------------------

    def test_menu_opens_the_tag_menu_only_inside_the_tag_list(self) -> None:
        self.open("TRACK")
        self.send(CommandType.MENU)
        self.assertIsNone(self.screen.display.tag_menu)
        self.assertEqual(self.screen.display.browse.path, ("HISTORY",))

        self.open(TAG_LIST_CATEGORY)
        self.send(CommandType.MENU)
        self.assertIs(
            self.screen.display.tag_menu, TagMenuAction.REMOVE_ALL
        )

    def test_remove_all_empties_the_tag_list(self) -> None:
        for track_id in ("t1", "t2"):
            self.tags.add(SOURCE_ID, track_id)
        self.open(TAG_LIST_CATEGORY)
        self.send(CommandType.MENU)
        self.press_rotary()                       # REMOVE ALL TRACKS
        self.assertEqual(self.tags.track_ids(SOURCE_ID), ())
        self.assertIsNone(self.screen.display.tag_menu)

    def test_create_playlist_reports_that_it_is_read_only(self) -> None:
        self.tags.add(SOURCE_ID, "t1")
        self.open(TAG_LIST_CATEGORY)
        self.send(CommandType.MENU)
        self.rotate(1)                            # CREATE PLAYLIST
        self.assertIs(
            self.screen.display.tag_menu, TagMenuAction.CREATE_PLAYLIST
        )
        self.press_rotary()
        self.assertIn("nicht verfuegbar", self.screen.display.notice)
        # Und die Tag List ist unveraendert geblieben.
        self.assertEqual(self.tags.track_ids(SOURCE_ID), ("t1",))

    def test_long_press_removes_a_track_from_the_tag_list(self) -> None:
        """Abschnitt 5: entfernt nur den Eintrag, sonst nichts."""
        for track_id in ("t1", "t2"):
            self.tags.add(SOURCE_ID, track_id)
        self.open(TAG_LIST_CATEGORY)
        self.send(CommandType.BROWSE_PRESS, pressed=True)
        self.clock.now += 1.0
        self.screen.poll_holds()
        self.send(CommandType.BROWSE_PRESS, pressed=False)

        self.assertEqual(self.tags.track_ids(SOURCE_ID), ("t2",))
        # Der Track selbst ist in der Bibliothek unveraendert vorhanden.
        library = self.screen.model.library(SOURCE_ID)
        self.assertIsNotNone(library.track("t1"))
        self.assertEqual(len(library.tracks()), len(TRACKS))
        # Und der Ladevorgang wurde dabei nicht ausgeloest.
        self.assertEqual(self.loaded, [])

    def test_long_press_outside_the_tag_list_still_toggles_jump_mode(self) -> None:
        from virtual_cdj.deck.display_state import JumpMode

        self.open("TRACK")
        self.send(CommandType.BROWSE_PRESS, pressed=True)
        self.clock.now += 1.0
        self.screen.poll_holds()
        self.send(CommandType.BROWSE_PRESS, pressed=False)
        self.assertIsNot(self.screen.display.browse.jump, JumpMode.OFF)

    def test_back_closes_the_menu_before_leaving_the_list(self) -> None:
        self.open(TAG_LIST_CATEGORY)
        self.send(CommandType.MENU)
        self.send(CommandType.BACK, pressed=True)
        self.clock.now += 0.05
        self.send(CommandType.BACK, pressed=False)
        self.assertIsNone(self.screen.display.tag_menu)
        self.assertEqual(
            self.screen.display.browse.path, (TAG_LIST_CATEGORY,)
        )

    # -- TEST 12: Datentraeger verschwindet ------------------------------

    def test_losing_the_source_does_not_crash_the_browser(self) -> None:
        self.tags.add(SOURCE_ID, "t1")
        self.open(TAG_LIST_CATEGORY)
        self.assertEqual(self.listing(), ["Charlie"])

        # Stick abgezogen: die Quelle verschwindet aus dem Modell.
        model = self.screen.model
        model.remove(SOURCE_ID)
        self.screen.set_model(model)
        self.root.update()
        self.screen.refresh(force=True)

        self.assertEqual(self.listing(), [])
        self.assertEqual(self.screen.target_track_id(), "")
        # Und die Taste laeuft ins Leere, statt zu stuerzen.
        self.send(CommandType.TAG_TRACK_TOGGLE)
        self.assertEqual(self.screen.display.notice, "kein Track markiert")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
