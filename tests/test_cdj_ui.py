"""Tests der CDJ-Bildschirmoberflaeche (Phase 1).

Es wird ein echtes, aber unsichtbares Tk-Fenster aufgebaut. Geprueft wird,
dass die Oberflaeche den echten Deck-Zustand anzeigt, Bedienung als Kommando
weitergibt und ohne Trackdaten einen ehrlichen Leerzustand zeigt.
"""

from __future__ import annotations

import unittest

try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.withdraw()
    _probe.destroy()
    TK_AVAILABLE = True
except Exception:  # pragma: no cover - kopfloser Rechner
    TK_AVAILABLE = False

from virtual_cdj.deck.commands import CommandType, Views, command
from virtual_cdj.deck.engine import Deck
from virtual_cdj.deck.provider import LocalDeckStateProvider
from virtual_cdj.deck.state import BeatGrid, HotCue, PlayState, TrackInfo

TRACK = TrackInfo(
    track_id="t1",
    title="Above The Cloud",
    artist="Sansname",
    genre="Techno",
    duration_s=180.0,
    original_bpm=124.0,
    key="8A",
    source="USB",
    beat_grid=BeatGrid(first_beat_s=0.0, bpm=124.0),
    hot_cues=(
        HotCue(index=0, position_s=8.0, color="#ff3b30"),
        HotCue(index=3, position_s=40.0, color="#3aa7ff"),
    ),
)


def canvas_texts(widget) -> list[str]:
    """Alle Textelemente eines Canvas."""
    return [
        widget.itemcget(item, "text")
        for item in widget.find_all()
        if widget.type(item) == "text"
    ]


def joined(widget) -> str:
    return " | ".join(canvas_texts(widget))


@unittest.skipUnless(TK_AVAILABLE, "keine Tk-Anzeige verfuegbar")
class CdjScreenTests(unittest.TestCase):
    def setUp(self) -> None:
        from virtual_cdj.cdj_ui.screen import CdjScreen

        # Das Fenster muss gemappt sein, sonst hat es in Tk keine echte
        # Geometrie (1x1) und Klickkoordinaten waeren sinnlos. Es wird
        # deshalb unsichtbar und ausserhalb des Bildschirms gemappt.
        self.root = tk.Tk()
        self.root.geometry("1024x600+3000+3000")
        self.root.attributes("-alpha", 0.0)

        self.deck = Deck(2)
        self.provider = LocalDeckStateProvider(self.deck)
        self.screen = CdjScreen(self.root, self.provider)
        self.screen.pack(fill="both", expand=True)
        self.root.update()
        self.screen.refresh(force=True)

    def tearDown(self) -> None:
        self.screen.stop()
        self.root.destroy()

    def redraw(self) -> None:
        self.root.update()
        self.screen.refresh(force=True)

    # -- Aufbau -----------------------------------------------------------

    def test_all_regions_exist(self) -> None:
        for name in (
            "top_bar", "waveform_header", "master_wave",
            "scrolling", "overview", "status_bar",
            "browser", "source_screen", "panels", "track_info_popup",
            "debug",
        ):
            self.assertTrue(hasattr(self.screen, name), name)

    def test_screen_uses_provider_deck_id_not_a_fixed_one(self) -> None:
        self.assertEqual(self.screen.deck_id, 2)
        self.assertIn("2", joined(self.screen.top_bar))

    def test_layout_fits_target_resolution(self) -> None:
        self.assertEqual(
            (self.screen.winfo_width(), self.screen.winfo_height()),
            (1024, 600),
        )
        total = sum(
            region.winfo_height()
            for region in (
                self.screen.top_bar,
                self.screen.status_bar, self.screen.overview,
            )
        )
        self.assertLess(total, 600)
        self.assertGreater(self.screen.scrolling.winfo_height(), 100)

    def test_metrics_scale_with_window(self) -> None:
        base = self.screen.metrics
        self.assertEqual((base.width, base.height), (1024, 600))
        self.assertAlmostEqual(base.scale, 1.0)

        self.root.geometry("1280x800+3000+3000")
        self.root.update()
        scaled = self.screen.metrics
        self.assertEqual((scaled.width, scaled.height), (1280, 800))
        self.assertGreater(scaled.scale, 1.0)
        # Negative Groessen sind Pixel: groesser heisst weiter im Minus.
        self.assertGreater(abs(scaled.font(10)[1]), abs(base.font(10)[1]))
        self.assertLess(base.font(10)[1], 0, "Schriftgroesse muss Pixel sein")
        self.assertGreater(scaled.px(40), base.px(40))

    # -- Leerzustand ------------------------------------------------------

    def test_empty_deck_shows_honest_placeholders(self) -> None:
        self.assertIn("KEIN TRACK GELADEN", joined(self.screen.top_bar))
        self.assertIn("kein Track geladen", joined(self.screen.overview))
        self.assertIn("kein Track geladen", joined(self.screen.scrolling))

    def test_no_invented_bpm_without_track(self) -> None:
        text = joined(self.screen.status_bar)
        self.assertIn("--.--", text)
        self.assertNotIn("124", text)

    def test_browser_states_that_no_source_is_connected(self) -> None:
        self.screen.set_view(Views.BROWSE)
        self.redraw()
        self.assertIn(
            "KEINE TRACKQUELLE VERBUNDEN", joined(self.screen.browser)
        )

    # -- Mit Track --------------------------------------------------------

    def test_track_information_comes_from_deck_state(self) -> None:
        self.deck.load_track(TRACK)
        self.redraw()

        top = joined(self.screen.top_bar)
        self.assertIn("Above The Cloud", top)
        self.assertIn("Sansname", top)
        self.assertIn("Techno", top)
        self.assertIn("USB", top)

        status = joined(self.screen.status_bar)
        self.assertIn("124.00", status)
        self.assertIn("+0.00 %", status)
        # Tonart und MT stehen in der unteren Zeile (Elemente 24, 25).
        self.assertIn("8A", joined(self.screen.overview))

    def test_bpm_display_follows_tempo_not_gui(self) -> None:
        self.deck.load_track(TRACK)
        self.provider.send(command(CommandType.TEMPO_SET, 2, value=1.0))
        self.redraw()
        status = joined(self.screen.status_bar)
        self.assertIn("136.40", status)  # 124 + 10 %
        self.assertIn("+10.00 %", status)
        self.assertIn("ORIG 124.00", status)

    def test_beat_display_uses_beat_grid(self) -> None:
        self.deck.load_track(TRACK)
        self.provider.send(command(CommandType.SEEK, 2, position_s=0.0))
        self.redraw()
        self.assertIn("BEAT", joined(self.screen.waveform_header))
        self.assertEqual(self.deck.state.beat, 1)

    def test_missing_beat_grid_is_stated(self) -> None:
        from dataclasses import replace

        self.deck.load_track(replace(TRACK, beat_grid=None))
        self.redraw()
        # Ohne Beatgrid kann es keinen Beat-Countdown geben.
        self.assertFalse(self.screen.display.beat_countdown().is_valid)
        self.assertIn("--.-", joined(self.screen.waveform_header))

    def test_waveform_absence_is_stated_not_faked(self) -> None:
        self.deck.load_track(TRACK)
        self.redraw()
        self.assertIn(
            "keine Waveform-Analyse verfuegbar", joined(self.screen.scrolling)
        )
        self.assertIn(
            "keine Waveform-Analyse verfuegbar", joined(self.screen.overview)
        )

    def test_hot_cue_labels_come_from_track_data(self) -> None:
        self.deck.load_track(TRACK)
        self.redraw()
        overview = canvas_texts(self.screen.overview)
        self.assertIn("A", overview)
        self.assertIn("D", overview)
        self.assertNotIn("B", overview)

    def test_remaining_time_counts_down(self) -> None:
        self.deck.load_track(TRACK)
        self.provider.send(command(CommandType.SEEK, 2, position_s=60.0))
        self.redraw()
        self.assertIn("-2:00.000", joined(self.screen.status_bar))

    # -- Bedienung --------------------------------------------------------

    def test_display_has_no_pad_row(self) -> None:
        """Am Geraet sind die Hot Cues Tasten unter dem Display.

        Auf dem Schirm erscheinen sie als Marken auf beiden Wellenformen -
        eine Pad-Reihe gibt es dort nicht (Handbuch S. 21-23).
        """
        self.assertFalse(hasattr(self.screen, "performance"))

    def test_sync_state_comes_from_the_deck(self) -> None:
        """SYNC ist eine Anzeige, kein Schalter auf dem Schirm."""
        self.deck.load_track(TRACK)
        self.redraw()
        self.assertNotIn("SYNC", joined(self.screen.status_bar))
        self.provider.send(command(CommandType.SYNC_TOGGLE, 2))
        self.redraw()
        self.assertIn("SYNC", joined(self.screen.status_bar))
        self.assertTrue(self.deck.state.sync)

    def test_overview_touch_seeks(self) -> None:
        self.deck.load_track(TRACK)
        self.redraw()
        overview = self.screen.overview
        overview.event_generate(
            "<Button-1>", x=int(overview.winfo_width() * 0.5), y=10
        )
        self.root.update()
        self.assertAlmostEqual(self.deck.state.position_s, 90.0, delta=2.0)

    def test_screen_has_no_tab_bar(self) -> None:
        """Am Geraet gibt es keine Reiterleiste - die Taster schalten um."""
        self.assertFalse(hasattr(self.screen, "nav"))

    def test_top_bar_only_on_the_playback_screen(self) -> None:
        """Handbuch S. 18/19: SOURCE und BROWSE haben eigene Kopfzeilen."""
        self.assertTrue(self.screen.top_bar.winfo_ismapped())
        self.screen.set_view(Views.BROWSE)
        self.root.update()
        self.assertFalse(self.screen.top_bar.winfo_ismapped())
        self.screen.set_view(Views.WAVEFORM)
        self.root.update()
        self.assertTrue(self.screen.top_bar.winfo_ismapped())

    def test_view_command_does_not_reach_the_deck(self) -> None:
        before = self.deck.state.generation
        self.screen.send_command(
            command(CommandType.VIEW, 2, view=Views.SOURCE)
        )
        self.assertIs(self.screen.view, Views.SOURCE)
        self.assertEqual(self.deck.state.generation, before)

    # -- Master-Waveform --------------------------------------------------

    def test_master_row_hidden_without_master_provider(self) -> None:
        self.assertFalse(
            self.screen.master_wave.should_show(self.deck.state)
        )
        self.assertFalse(self.screen.master_wave.winfo_ismapped())

    def test_master_row_appears_when_another_deck_is_master(self) -> None:
        from virtual_cdj.deck.display_state import MasterDeckView

        other = Deck(1)
        other.load_track(TRACK)
        other.execute(command(CommandType.MASTER_SET, 1))
        self.screen.master_view_source = (
            lambda: MasterDeckView.from_deck_state(other.state)
        )
        self.redraw()
        self.assertTrue(self.screen.display.master_is_other_deck)
        self.assertTrue(self.screen.master_wave.should_show(self.deck.state))
        self.assertIn("MASTER", joined(self.screen.master_wave))

    def test_own_master_is_not_shown_twice(self) -> None:
        from virtual_cdj.deck.display_state import MasterDeckView

        self.deck.execute(command(CommandType.MASTER_SET, 2))
        self.screen.master_view_source = (
            lambda: MasterDeckView.from_deck_state(self.deck.state)
        )
        self.redraw()
        self.assertFalse(self.screen.display.master_is_other_deck)
        self.assertFalse(
            self.screen.master_wave.should_show(self.deck.state)
        )
        self.assertFalse(self.screen.master_wave.winfo_ismapped())

    # -- Rendering --------------------------------------------------------

    def test_unchanged_state_is_not_redrawn(self) -> None:
        self.deck.load_track(TRACK)
        self.redraw()
        self.assertFalse(self.screen.top_bar.update_state(self.deck.state))

    def test_regions_redraw_only_for_what_they_show(self) -> None:
        """Jeder Bereich haengt an seinen eigenen Werten.

        Quantize steht in der Statuszeile, nicht in der Kopfzeile. Also
        darf sich beim Umschalten nur die Statuszeile neu zeichnen.
        """
        from dataclasses import replace as dc_replace

        self.deck.load_track(TRACK)
        self.redraw()
        self.provider.send(command(CommandType.QUANTIZE_TOGGLE, 2))
        state = self.deck.state
        # QUANTIZE steht in der linken Spalte der unteren Zeile
        # (Handbuch Element 13), nicht in der Kopfzeile.
        self.assertTrue(self.screen.overview.update_state(state))
        self.assertFalse(self.screen.top_bar.update_state(state))

        # Ein Trackwechsel betrifft dagegen auch die Kopfzeile.
        self.deck.load_track(dc_replace(TRACK, track_id="t2", title="Zwei"))
        self.assertTrue(self.screen.top_bar.update_state(self.deck.state))

    def test_playing_does_not_redraw_the_whole_screen(self) -> None:
        """Waehrend der Wiedergabe laeuft nur mit, was sich auch bewegt."""
        self.deck.load_track(TRACK)
        self.redraw()
        drawn: list[str] = []
        for name in (
            "top_bar", "status_bar", "overview", "waveform_header",
        ):
            region = getattr(self.screen, name)
            region.update_state(self.deck.state)

        # Ein Positionsschritt von 5 ms: fuer die Statuszeile (20 Hz), die
        # Overview (10 Hz) und die Kopfzeile ist das nichts Sichtbares.
        self.provider.send(
            command(CommandType.SEEK, 2, position_s=30.0)
        )
        self.redraw()
        for name in (
            "top_bar", "status_bar", "overview", "waveform_header",
        ):
            region = getattr(self.screen, name)
            region.update_state(self.deck.state)
        state = self.deck.state.with_changes(position_s=30.005)
        for name in ("top_bar", "overview"):
            region = getattr(self.screen, name)
            if region.update_state(state):
                drawn.append(name)
        self.assertEqual(drawn, [], f"unnoetig neu gezeichnet: {drawn}")

    def test_debug_overlay_can_be_toggled(self) -> None:
        self.assertFalse(self.screen.debug.visible)
        self.assertTrue(self.screen.toggle_debug())
        self.redraw()
        self.assertIn("DEBUG", joined(self.screen.debug))
        self.assertIn("NO_BACKEND", joined(self.screen.debug))
        self.assertFalse(self.screen.toggle_debug())

    def test_frame_loop_runs_and_advances_playback(self) -> None:
        self.deck.load_track(TRACK)
        self.provider.send(command(CommandType.PLAY_PAUSE, 2))
        self.screen.start()
        for _ in range(12):
            self.root.update()
        self.screen.stop()
        self.assertGreater(self.deck.state.position_s, 0.0)


@unittest.skipUnless(TK_AVAILABLE, "keine Tk-Anzeige verfuegbar")
class CdjWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        from virtual_cdj.cdj_ui.window import CdjDisplayApp

        self.app = CdjDisplayApp()

    def tearDown(self) -> None:
        for window in list(self.app.windows):
            window.destroy()
        self.app.destroy()

    def test_multiple_displays_have_own_deck_ids(self) -> None:
        providers = [LocalDeckStateProvider(Deck(i)) for i in (1, 2, 3)]
        windows = [self.app.add_display(p) for p in providers]
        self.app.update()
        self.assertEqual([w.deck_id for w in windows], [1, 2, 3])
        self.assertEqual(
            [w.screen.deck_id for w in windows], [1, 2, 3]
        )
        self.assertEqual(len({id(w.screen) for w in windows}), 3)

    def test_windows_are_independent(self) -> None:
        a = self.app.add_display(LocalDeckStateProvider(Deck(1)))
        b = self.app.add_display(LocalDeckStateProvider(Deck(2)))
        self.app.update()
        a.screen.set_view(Views.BROWSE)
        self.assertIs(a.screen.view, Views.BROWSE)
        self.assertIs(b.screen.view, Views.WAVEFORM)

    def test_fullscreen_can_be_toggled(self) -> None:
        window = self.app.add_display(LocalDeckStateProvider(Deck(1)))
        self.app.update()
        self.assertFalse(window.fullscreen)
        window.set_fullscreen(True)
        self.app.update()
        self.assertTrue(window.fullscreen)
        self.assertTrue(bool(window.overrideredirect()))
        window.set_fullscreen(False)
        self.app.update()
        self.assertFalse(window.fullscreen)

    def test_default_geometry_is_target_resolution(self) -> None:
        window = self.app.add_display(LocalDeckStateProvider(Deck(1)))
        self.app.update()
        self.assertEqual(window.winfo_width(), 1024)
        self.assertEqual(window.winfo_height(), 600)


@unittest.skipUnless(TK_AVAILABLE, "keine Tk-Anzeige verfuegbar")
class WiringTests(unittest.TestCase):
    """Bedienfeld -> InputLayer -> Mapper -> Deck -> CDJ-Anzeige."""

    def test_virtual_panel_drives_the_cdj_screen(self) -> None:
        import run_cdj

        # ``--no-usb``: der Test prueft die Verdrahtung, nicht die
        # Datentraegererkennung. Ohne das laese er die echten Laufwerke des
        # Rechners ein und haenge vom dort steckenden Stick ab.
        args = run_cdj.parse_args(["--decks", "1", "--no-panel", "--no-usb"])
        app = run_cdj.build(args)
        try:
            app.update()
            window = app.windows[0]
            provider = window.provider
            provider.deck.load_track(TRACK)

            from virtual_cdj.core import ids
            from virtual_cdj.core.input_layer import InputLayer
            from virtual_cdj.deck.mapping import InputMapper

            layer = InputLayer()
            layer.subscribe(InputMapper(1, provider.send).handle_event)

            layer.press(ids.PLAY)
            window.screen.refresh(force=True)
            app.update()
            self.assertIs(
                provider.get_state().play_state, PlayState.PLAYING
            )
            self.assertIn("PLAY", joined(window.screen.status_bar))
        finally:
            for window in list(app.windows):
                window.destroy()
            app.destroy()


if __name__ == "__main__":
    unittest.main()
