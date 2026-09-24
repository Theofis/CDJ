"""Der vertikale Pfad aus Abschnitt 16 und 19.

    Datei -> Decoder -> Analyse -> Cache -> Deck -> Audio -> DeckState -> GUI

Die Kette wird mit echtem Audio geprueft: Position, BPM, Beat, Cue, Loop und
Hotcues kommen aus der Audioausgabe, nicht aus einer Wanduhr.
"""

from __future__ import annotations

import tempfile
import unittest

import numpy as np

from tests.audio_fixtures import (
    KNOWN_BPM,
    RAMP_DURATION_S,
    SAMPLE_RATE,
    fixtures,
    ramp_seconds,
    ramp_track,
)
from virtual_cdj.audio.cache import AnalysisCache
from virtual_cdj.audio.engine import AudioEngine
from virtual_cdj.audio.loader import TrackLoader
from virtual_cdj.core import ids
from virtual_cdj.core.input_layer import InputLayer
from virtual_cdj.deck.commands import CommandType, command
from virtual_cdj.deck.engine import Deck
from virtual_cdj.deck.mapping import InputMapper
from virtual_cdj.deck.mode_manager import OperatingMode
from virtual_cdj.deck.provider import LocalDeckStateProvider
from virtual_cdj.deck.state import (
    AudioStatus,
    BeatGrid,
    CueKind,
    HotCue,
    PlayState,
)

_LOADED: dict[str, object] = {}


def loaded_track():
    """Testtrack einmal je Testlauf laden und analysieren."""
    if "track" not in _LOADED:
        temp = tempfile.TemporaryDirectory()
        _LOADED["temp"] = temp
        loader = TrackLoader(cache=AnalysisCache(temp.name))
        _LOADED["track"] = loader.load(fixtures().wav)
    return _LOADED["track"]


class Rig:
    """Deck mit echter Audioausgabe, aber ohne Soundkarte."""

    def __init__(self, deck_id: int = 1, block: int = 512) -> None:
        self.block = block
        self.engine = AudioEngine(block_frames=block)
        self.voice = self.engine.voice(deck_id)
        self.deck = Deck(deck_id)
        self.deck.attach_playback(self.voice)
        self.provider = LocalDeckStateProvider(self.deck)
        self.out = np.zeros((block, 2), dtype=np.float32)

    def load(self, info, samples) -> None:
        self.deck.load_track(info, samples)

    def render(self, seconds: float) -> np.ndarray:
        """Audio rendern und danach den Deck-Zustand aktualisieren."""
        blocks = max(1, int(seconds * SAMPLE_RATE / self.block))
        parts = []
        for _ in range(blocks):
            parts.append(self.engine.render_block(self.block).copy())
        self.deck.tick()
        return np.concatenate(parts)

    def send(self, command_type: CommandType, **params) -> None:
        self.provider.send(
            command(command_type, self.deck.deck_id, "TEST", **params)
        )

    def close(self) -> None:
        self.engine.close()


class RealTrackTests(unittest.TestCase):
    """Echter Track, echtes Audio, echter DeckState."""

    def setUp(self) -> None:
        self.track = loaded_track()
        self.rig = Rig()
        self.rig.load(self.track.info, self.track.samples)

    def tearDown(self) -> None:
        self.rig.close()

    # -- Zustand ----------------------------------------------------------

    def test_deck_state_has_real_values_after_load(self) -> None:
        state = self.rig.deck.state
        self.assertTrue(state.has_track)
        self.assertTrue(state.has_waveform)
        self.assertTrue(state.has_beat_grid)
        self.assertIs(state.audio_status, AudioStatus.RUNNING)
        self.assertAlmostEqual(state.duration_s, 12.0, places=2)
        self.assertAlmostEqual(state.original_bpm, KNOWN_BPM, delta=0.5)
        self.assertAlmostEqual(state.current_bpm, state.original_bpm, places=2)
        self.assertEqual(state.tempo_percent, 0.0)
        self.assertIs(state.play_state, PlayState.STOPPED)

    def test_waveform_has_three_levels_with_real_peaks(self) -> None:
        waveform = self.rig.deck.state.track.waveform
        self.assertIsNotNone(waveform)
        self.assertEqual(
            set(waveform.levels), {"overview", "medium", "detailed"}
        )
        for level in waveform.levels.values():
            self.assertTrue(level.is_consistent())
            self.assertGreater(float(np.asarray(level.low).max()), 0.0)

    # -- Position ---------------------------------------------------------

    def test_position_comes_from_audio_not_from_a_clock(self) -> None:
        self.rig.send(CommandType.PLAY_PAUSE)
        # Ohne gerenderte Bloecke darf sich nichts bewegen, obwohl Zeit
        # vergeht - das beweist, dass die Audioausgabe die Quelle ist.
        import time

        time.sleep(0.15)
        self.rig.deck.tick()
        self.assertEqual(self.rig.deck.state.position_s, 0.0)

        self.rig.render(1.0)
        self.assertAlmostEqual(
            self.rig.deck.state.position_s, 1.0, delta=0.02
        )

    def test_position_advances_with_tempo(self) -> None:
        self.rig.send(CommandType.TEMPO_SET, value=1.0)  # +10 %
        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(1.0)
        self.assertAlmostEqual(
            self.rig.deck.state.position_s, 1.1, delta=0.03
        )

    def test_bpm_follows_tempo_but_original_stays(self) -> None:
        original = self.rig.deck.state.original_bpm
        self.rig.send(CommandType.TEMPO_SET, value=1.0)
        state = self.rig.deck.state
        self.assertAlmostEqual(state.tempo_percent, 10.0, places=3)
        self.assertAlmostEqual(state.current_bpm, original * 1.1, places=2)
        self.assertEqual(state.original_bpm, original)

    def test_beat_and_bar_come_from_the_analysed_grid(self) -> None:
        self.rig.send(CommandType.PLAY_PAUSE)
        seen_beats = set()
        for _ in range(8):
            self.rig.render(0.2)
            state = self.rig.deck.state
            self.assertGreaterEqual(state.bar, 1)
            self.assertIn(state.beat, (1, 2, 3, 4))
            seen_beats.add(state.beat)
        # Ueber 1.6 s bei 124 BPM muessen mehrere Beats vorkommen.
        self.assertGreater(len(seen_beats), 1)

    def test_playback_stops_at_end(self) -> None:
        self.rig.send(CommandType.SEEK, position_s=11.95)
        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(0.5)
        self.assertIs(self.rig.deck.state.play_state, PlayState.STOPPED)

    # -- Audio ------------------------------------------------------------

    def test_real_audio_is_produced(self) -> None:
        self.rig.send(CommandType.PLAY_PAUSE)
        data = self.rig.render(1.0)
        rms = float(np.sqrt(np.mean(data ** 2)))
        self.assertGreater(rms, 0.01, "kein Audio am Ausgang")
        self.assertLessEqual(float(np.abs(data).max()), 1.0)

    def test_pause_silences_the_output(self) -> None:
        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(0.2)
        self.rig.send(CommandType.PLAY_PAUSE)
        data = self.rig.render(0.2)
        self.assertEqual(float(np.abs(data).max()), 0.0)

    # -- Cue --------------------------------------------------------------

    def test_cue_returns_to_cue_point_and_pauses(self) -> None:
        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(1.0)
        self.rig.send(CommandType.CUE, pressed=True)
        state = self.rig.deck.state
        self.assertIs(state.play_state, PlayState.PAUSED)
        self.assertAlmostEqual(state.position_s, 0.0, places=5)
        data = self.rig.render(0.2)
        self.assertEqual(float(np.abs(data).max()), 0.0)

    def test_cue_sets_point_then_sampler_plays_while_held(self) -> None:
        self.rig.send(CommandType.SEEK, position_s=4.0)
        self.rig.send(CommandType.CUE, pressed=True)
        self.assertAlmostEqual(self.rig.deck.state.cue_point_s, 4.0, places=4)
        self.rig.send(CommandType.CUE, pressed=False)

        # Erneut druecken: spielt ab Cue, solange gehalten.
        self.rig.send(CommandType.CUE, pressed=True)
        self.assertIs(self.rig.deck.state.play_state, PlayState.CUEING)
        data = self.rig.render(0.3)
        self.assertGreater(float(np.abs(data).max()), 0.0)
        self.assertGreater(self.rig.deck.state.position_s, 4.0)

        self.rig.send(CommandType.CUE, pressed=False)
        self.assertIs(self.rig.deck.state.play_state, PlayState.PAUSED)
        self.assertAlmostEqual(self.rig.deck.state.position_s, 4.0, places=4)

    # -- Loop -------------------------------------------------------------

    def test_beat_loop_holds_the_audio_position(self) -> None:
        self.rig.send(CommandType.SEEK, position_s=2.0)
        self.rig.send(CommandType.BEAT_LOOP, beats=4)
        loop = self.rig.deck.state.loop
        self.assertTrue(loop.active)
        expected = 4 * 60.0 / self.rig.deck.state.original_bpm
        self.assertAlmostEqual(loop.length_s, expected, places=3)

        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(6.0)
        position = self.rig.deck.state.position_s
        self.assertGreaterEqual(position, loop.in_s - 1e-3)
        self.assertLess(position, loop.out_s + 1e-3)
        self.assertGreater(self.rig.voice.loop_wraps, 0)

    def test_loop_release_lets_playback_continue(self) -> None:
        self.rig.send(CommandType.SEEK, position_s=2.0)
        self.rig.send(CommandType.BEAT_LOOP, beats=1)
        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(2.0)
        loop_out = self.rig.deck.state.loop.out_s
        self.rig.send(CommandType.RELOOP_EXIT)
        self.assertFalse(self.rig.deck.state.loop.active)
        self.rig.render(2.0)
        self.assertGreater(self.rig.deck.state.position_s, loop_out)

    def test_manual_loop_in_out(self) -> None:
        # Ohne QUANTIZE liegen beide Punkte exakt auf der Position.
        self.rig.send(CommandType.SEEK, position_s=1.0)
        self.rig.send(CommandType.LOOP_IN)
        self.rig.send(CommandType.SEEK, position_s=2.0)
        self.rig.send(CommandType.LOOP_OUT)
        loop = self.rig.deck.state.loop
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.length_s, 1.0, places=3)

        self.rig.send(CommandType.PLAY_PAUSE)
        data = self.rig.render(4.0)
        self.assertGreater(float(np.abs(data).max()), 0.0)
        self.assertLess(self.rig.deck.state.position_s, 2.0 + 1e-3)

    def test_quantized_manual_loop_lands_on_the_grid(self) -> None:
        grid = self.rig.deck.state.track.beat_grid
        self.rig.send(CommandType.QUANTIZE_TOGGLE)
        self.rig.send(CommandType.SEEK, position_s=1.0)
        self.rig.send(CommandType.LOOP_IN)
        self.rig.send(CommandType.SEEK, position_s=2.0)
        self.rig.send(CommandType.LOOP_OUT)
        loop = self.rig.deck.state.loop
        self.assertAlmostEqual(loop.in_s, grid.snap(1.0), places=5)
        self.assertAlmostEqual(loop.out_s, grid.snap(2.0), places=5)

    # -- Hotcues ----------------------------------------------------------

    def test_hotcue_jumps_to_real_position_and_plays(self) -> None:
        self.rig.send(CommandType.SEEK, position_s=5.0)
        self.rig.send(CommandType.PAD, index=0, pressed=True)
        cue = self.rig.deck.state.track.hot_cue(0)
        self.assertIsNotNone(cue)
        self.assertAlmostEqual(cue.position_s, 5.0, places=4)

        self.rig.send(CommandType.SEEK, position_s=0.0)
        self.rig.send(CommandType.PAD, index=0, pressed=True)
        self.assertAlmostEqual(
            self.rig.deck.state.position_s, 5.0, places=4
        )
        self.assertIs(self.rig.deck.state.play_state, PlayState.PLAYING)
        data = self.rig.render(0.3)
        self.assertGreater(float(np.abs(data).max()), 0.0)

    def test_loop_hotcue_activates_the_loop_in_audio(self) -> None:
        import dataclasses

        info = dataclasses.replace(
            self.track.info,
            hot_cues=(
                HotCue(
                    index=2, position_s=3.0, color="#00ff00",
                    kind=CueKind.LOOP, loop_end_s=3.5,
                ),
            ),
        )
        self.rig.load(info, self.track.samples)
        self.rig.send(CommandType.PAD, index=2, pressed=True)
        loop = self.rig.deck.state.loop
        self.assertTrue(loop.active)
        self.rig.render(3.0)
        self.assertLess(self.rig.deck.state.position_s, 3.5 + 1e-3)

    # -- Jog --------------------------------------------------------------

    def test_jog_with_touch_moves_the_audio_position(self) -> None:
        self.rig.send(CommandType.SEEK, position_s=4.0)
        self.rig.send(CommandType.JOG_TOUCH, pressed=True)
        self.rig.send(
            CommandType.JOG_MOVE, delta=360, ticks_per_rev=360
        )
        # Eine Umdrehung entspricht der Plattenlaenge.
        self.assertGreater(self.rig.deck.state.position_s, 4.0)
        self.assertTrue(self.rig.deck.state.jog_touch)

    def test_jog_without_touch_bends_less(self) -> None:
        self.rig.send(CommandType.SEEK, position_s=4.0)
        self.rig.send(
            CommandType.JOG_MOVE, delta=360, ticks_per_rev=360
        )
        bend = self.rig.deck.state.position_s - 4.0

        self.rig.send(CommandType.SEEK, position_s=4.0)
        self.rig.send(CommandType.JOG_TOUCH, pressed=True)
        self.rig.send(
            CommandType.JOG_MOVE, delta=360, ticks_per_rev=360
        )
        scratch = self.rig.deck.state.position_s - 4.0
        self.assertGreater(scratch, bend)

    def test_jog_does_not_interrupt_playback(self) -> None:
        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(0.5)
        self.rig.send(CommandType.JOG_MOVE, delta=90, ticks_per_rev=360)
        data = self.rig.render(0.5)
        self.assertGreater(float(np.abs(data).max()), 0.0)
        self.assertIs(self.rig.deck.state.play_state, PlayState.PLAYING)

    # -- Seek -------------------------------------------------------------

    def test_seek_is_exact_and_audible(self) -> None:
        for target in (0.0, 1.5, 6.25, 11.0):
            self.rig.send(CommandType.SEEK, position_s=target)
            self.assertAlmostEqual(
                self.rig.deck.state.position_s, target, places=4
            )
        self.rig.send(CommandType.SEEK, position_s=99.0)
        self.assertAlmostEqual(
            self.rig.deck.state.position_s,
            self.rig.deck.state.duration_s,
            places=3,
        )

    def test_reverse_direction_plays_backwards_in_audio(self) -> None:
        from virtual_cdj.deck.state import Direction

        self.rig.send(CommandType.SEEK, position_s=6.0)
        self.rig.send(CommandType.DIRECTION, position=Direction.REV)
        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(1.0)
        self.assertLess(self.rig.deck.state.position_s, 6.0)


class ExactPositionTests(unittest.TestCase):
    """Position am Rampensignal exakt nachrechnen."""

    def setUp(self) -> None:
        import dataclasses

        self.rig = Rig()
        samples = ramp_track(10.0)
        info = dataclasses.replace(
            loaded_track().info,
            duration_s=10.0,
            original_bpm=120.0,
            beat_grid=BeatGrid(first_beat_s=0.0, bpm=120.0),
        )
        self.rig.load(info, samples)

    def tearDown(self) -> None:
        self.rig.close()

    def test_output_sample_equals_position(self) -> None:
        self.rig.send(CommandType.SEEK, position_s=2.0)
        self.rig.send(CommandType.PLAY_PAUSE)
        data = self.rig.render(0.1)
        # Rampensignal: Samplewert ist die normierte Position.
        self.assertAlmostEqual(ramp_seconds(data[0, 0]), 2.0, places=3)
        self.assertAlmostEqual(
            ramp_seconds(data[-1, 0]),
            self.rig.deck.state.position_s,
            places=2,
        )

    def test_tempo_change_is_audible_in_the_signal(self) -> None:
        self.rig.send(CommandType.SEEK, position_s=1.0)
        self.rig.send(CommandType.TEMPO_SET, value=1.0)  # +10 %
        self.rig.send(CommandType.PLAY_PAUSE)
        data = self.rig.render(1.0)
        # Steigung des Rampensignals entspricht der Geschwindigkeit.
        slope = ramp_seconds(data[-1, 0]) - ramp_seconds(data[0, 0])
        self.assertAlmostEqual(slope, 1.1, delta=0.03)

    def test_beat_matches_grid_at_a_known_position(self) -> None:
        # 120 BPM: Beat 1 bei 0.0, 2.0, 4.0 ... Takt wechselt alle 2 s.
        self.rig.send(CommandType.SEEK, position_s=2.0)
        self.rig.deck.tick()
        state = self.rig.deck.state
        self.assertEqual((state.bar, state.beat), (2, 1))
        self.rig.send(CommandType.SEEK, position_s=2.5)
        self.rig.deck.tick()
        self.assertEqual(self.rig.deck.state.beat, 2)


class FourDeckTests(unittest.TestCase):
    """Vier Decks gleichzeitig - Abschnitt 17."""

    def setUp(self) -> None:
        self.track = loaded_track()
        self.engine = AudioEngine(block_frames=512)
        self.decks: dict[int, Deck] = {}
        self.providers: dict[int, LocalDeckStateProvider] = {}
        for deck_id in (1, 2, 3, 4):
            deck = Deck(deck_id)
            deck.attach_playback(self.engine.voice(deck_id))
            deck.load_track(self.track.info, self.track.samples)
            self.decks[deck_id] = deck
            self.providers[deck_id] = LocalDeckStateProvider(deck)

    def tearDown(self) -> None:
        self.engine.close()

    def render(self, seconds: float) -> np.ndarray:
        blocks = max(1, int(seconds * SAMPLE_RATE / 512))
        parts = [self.engine.render_block(512).copy() for _ in range(blocks)]
        for deck in self.decks.values():
            deck.tick()
        return np.concatenate(parts)

    def test_four_independent_deck_states(self) -> None:
        for deck_id, deck in self.decks.items():
            self.providers[deck_id].send(
                command(
                    CommandType.SEEK, deck_id, "TEST",
                    position_s=deck_id * 1.0,
                )
            )
            self.providers[deck_id].send(
                command(CommandType.PLAY_PAUSE, deck_id, "TEST")
            )
        self.render(0.5)

        positions = [deck.state.position_s for deck in self.decks.values()]
        self.assertEqual(len(set(round(p, 3) for p in positions)), 4)
        for deck_id, deck in self.decks.items():
            self.assertEqual(deck.state.deck_id, deck_id)
            self.assertIs(deck.state.play_state, PlayState.PLAYING)
            self.assertIs(deck.state.audio_status, AudioStatus.RUNNING)

    def test_four_decks_mix_without_clipping_or_dropouts(self) -> None:
        for deck_id in (1, 2, 3, 4):
            self.providers[deck_id].send(
                command(CommandType.PLAY_PAUSE, deck_id, "TEST")
            )
        data = self.render(2.0)
        self.assertEqual(self.engine.metrics.active_voices, 4)
        self.assertLessEqual(float(np.abs(data).max()), 1.0)
        self.assertEqual(self.engine.metrics.underruns, 0)
        # Bei vier Decks muss noch reichlich Zeit uebrig sein.
        self.assertLess(
            self.engine.metrics.load, 0.5,
            f"Callback-Last {self.engine.metrics.load * 100:.0f} %",
        )

    def test_commands_reach_only_the_addressed_deck(self) -> None:
        self.providers[2].send(command(CommandType.SYNC_TOGGLE, 2, "TEST"))
        self.assertTrue(self.decks[2].state.sync)
        for deck_id in (1, 3, 4):
            self.assertFalse(self.decks[deck_id].state.sync)

    def test_separate_loops_per_deck(self) -> None:
        self.providers[1].send(
            command(CommandType.BEAT_LOOP, 1, "TEST", beats=4)
        )
        self.assertTrue(self.decks[1].state.loop.active)
        for deck_id in (2, 3, 4):
            self.assertFalse(self.decks[deck_id].state.loop.active)

    def test_memory_scales_with_deck_count(self) -> None:
        megabytes = self.engine.memory_mb()
        one_track = 12.0 * SAMPLE_RATE * 2 * 4 / (1024 * 1024)
        self.assertAlmostEqual(megabytes, 4 * one_track, delta=1.0)


class InputToAudioTests(unittest.TestCase):
    """Bedienelement -> InputLayer -> Kommando -> Deck -> Audio -> Zustand."""

    def setUp(self) -> None:
        self.track = loaded_track()
        self.rig = Rig()
        self.rig.load(self.track.info, self.track.samples)
        self.layer = InputLayer()
        self.mapper = InputMapper(1, self.rig.provider.send)
        self.layer.subscribe(self.mapper.handle_event)

    def tearDown(self) -> None:
        self.rig.close()

    def test_play_button_produces_audio(self) -> None:
        self.layer.press(ids.PLAY)
        self.layer.release(ids.PLAY)
        data = self.rig.render(0.5)
        self.assertIs(self.rig.deck.state.play_state, PlayState.PLAYING)
        self.assertGreater(float(np.abs(data).max()), 0.0)

    def test_tempo_fader_changes_bpm_and_playback_rate(self) -> None:
        original = self.rig.deck.state.original_bpm
        self.layer.set_analog(ids.TEMPO_FADER, 1.0)
        self.assertAlmostEqual(
            self.rig.deck.state.current_bpm, original * 1.1, places=2
        )
        self.layer.press(ids.PLAY)
        self.rig.render(1.0)
        self.assertAlmostEqual(
            self.rig.deck.state.position_s, 1.1, delta=0.03
        )

    def test_pad_button_sets_and_recalls_hotcue(self) -> None:
        self.rig.send(CommandType.SEEK, position_s=3.0)
        self.layer.press(ids.PAD_C)
        self.layer.release(ids.PAD_C)
        cue = self.rig.deck.state.track.hot_cue(2)
        self.assertIsNotNone(cue)
        self.assertAlmostEqual(cue.position_s, 3.0, places=4)

    def test_cue_button_press_and_release(self) -> None:
        self.layer.press(ids.PLAY)
        self.rig.render(0.5)
        self.layer.press(ids.CUE)
        self.assertIs(self.rig.deck.state.play_state, PlayState.PAUSED)
        self.layer.release(ids.CUE)
        self.assertAlmostEqual(self.rig.deck.state.position_s, 0.0, places=5)

    def test_jog_from_hardware_protocol(self) -> None:
        from virtual_cdj.sources.hardware import HardwareSource

        source = HardwareSource(self.layer)
        self.rig.send(CommandType.SEEK, position_s=2.0)
        source.feed("T JOG_TOUCH 1\nJ JOG_MOVE 180\n")
        self.assertTrue(self.rig.deck.state.jog_touch)
        self.assertGreater(self.rig.deck.state.position_s, 2.0)

    def test_sync_and_quantize_from_buttons(self) -> None:
        self.layer.press(ids.BEAT_SYNC)
        self.assertTrue(self.rig.deck.state.sync)
        self.layer.press(ids.QUANTIZE)
        self.assertTrue(self.rig.deck.state.quantize)


class GuiEndToEndTests(unittest.TestCase):
    """Bis in die Oberflaeche: echte Waveform, echte Werte."""

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

        self.track = loaded_track()
        self.rig = Rig()
        self.rig.load(self.track.info, self.track.samples)

        self.root = tk.Tk()
        self.root.geometry("1024x600+3000+3000")
        self.root.attributes("-alpha", 0.0)
        self.screen = CdjScreen(self.root, self.rig.provider)
        self.screen.pack(fill="both", expand=True)
        self.root.update()
        self.screen.refresh(force=True)

    def tearDown(self) -> None:
        self.screen.stop()
        self.root.destroy()
        self.rig.close()

    def texts(self, widget) -> str:
        return " | ".join(
            widget.itemcget(item, "text")
            for item in widget.find_all()
            if widget.type(item) == "text"
        )

    def images(self, widget) -> int:
        return sum(
            1 for item in widget.find_all() if widget.type(item) == "image"
        )

    def redraw(self) -> None:
        self.root.update()
        self.screen.refresh(force=True)

    def test_real_bpm_is_displayed(self) -> None:
        expected = f"{self.rig.deck.state.current_bpm:.2f}"
        self.assertIn(expected, self.texts(self.screen.status_bar))

    def test_waveform_is_drawn_as_image_not_placeholder(self) -> None:
        self.assertGreaterEqual(self.images(self.screen.overview), 1)
        self.assertGreaterEqual(self.images(self.screen.scrolling), 1)
        self.assertNotIn(
            "keine Waveform-Analyse", self.texts(self.screen.scrolling)
        )
        self.assertNotIn(
            "keine Waveform-Analyse", self.texts(self.screen.overview)
        )

    def test_waveform_follows_the_audio_position(self) -> None:
        """Die laufende Waveform muss sich mit der Wiedergabe bewegen."""
        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(0.5)
        self.redraw()
        first = np.asarray(
            self.screen.scrolling._waveform.renderer.render_array(
                self.rig.deck.state.track.waveform.get("detailed"),
                *self.screen.scrolling.visible_range(
                    self.rig.deck.state.position_s
                ),
                200, 40,
            )
        ).copy()

        self.rig.render(1.0)
        self.redraw()
        second = np.asarray(
            self.screen.scrolling._waveform.renderer.render_array(
                self.rig.deck.state.track.waveform.get("detailed"),
                *self.screen.scrolling.visible_range(
                    self.rig.deck.state.position_s
                ),
                200, 40,
            )
        )
        self.assertFalse(
            np.array_equal(first, second),
            "Waveform-Bild aendert sich nicht mit der Position",
        )

    def test_time_and_beat_update_while_playing(self) -> None:
        before = self.texts(self.screen.status_bar)
        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(1.0)
        self.redraw()
        self.assertNotEqual(before, self.texts(self.screen.status_bar))
        self.assertIn("PLAY", self.texts(self.screen.status_bar))

    def test_loop_is_shown_with_real_length(self) -> None:
        """Handbuch Element 1: die Beatzahl steht an der Wellenform."""
        self.rig.send(CommandType.SEEK, position_s=2.0)
        self.rig.send(CommandType.BEAT_LOOP, beats=4)
        self.redraw()
        self.assertIn("4", self.texts(self.screen.scrolling))
        self.assertTrue(self.rig.deck.state.loop.active)

    def test_debug_overlay_shows_audio_metrics(self) -> None:
        self.screen.debug.audio_metrics = self.rig.engine.metrics
        self.screen.debug.analysis_metrics = None
        self.screen.toggle_debug()
        self.rig.send(CommandType.PLAY_PAUSE)
        self.rig.render(0.3)
        self.redraw()
        text = self.texts(self.screen.debug)
        for label in ("AUDIO", "Buffer", "Underruns", "Callback", "FPS"):
            self.assertIn(label, text)
        self.assertIn("RUNNING", text)


if __name__ == "__main__":
    unittest.main()


class ApplicationWiringTests(unittest.TestCase):
    """Verdrahtung in ``virtual_cdj/app.py``."""

    def test_without_audio_device_the_clock_takes_over(self) -> None:
        """Ohne Ausgabegeraet muss die Oberflaeche bedienbar bleiben."""
        import time

        from virtual_cdj.app import CdjApplication

        app = CdjApplication(
            [1], start_audio=False, operating_mode=OperatingMode.CDJ
        )
        try:
            result = app.load_track_now(1, fixtures().wav)
            self.assertTrue(result.ok)
            state = app.decks[1].state
            self.assertTrue(state.has_track)
            self.assertTrue(state.has_waveform)
            # Ehrliche Meldung statt vorgetaeuschter Ausgabe.
            self.assertIs(state.audio_status, AudioStatus.NO_BACKEND)
            self.assertFalse(app.audio_started)

            app.providers[1].send(command(CommandType.PLAY_PAUSE, 1, "TEST"))
            time.sleep(0.25)
            app.tick()
            self.assertGreater(app.decks[1].state.position_s, 0.0)
        finally:
            app.close()

    def test_track_loading_runs_in_the_worker(self) -> None:
        import time

        from virtual_cdj.app import CdjApplication

        app = CdjApplication(
            [1, 2], start_audio=False, operating_mode=OperatingMode.CDJ
        )
        try:
            app.load_track(2, fixtures().wav)
            deadline = time.monotonic() + 60.0
            while (
                not app.decks[2].state.has_track
                and time.monotonic() < deadline
            ):
                app.tick()
                time.sleep(0.02)
            self.assertTrue(app.decks[2].state.has_track)
            # Deck 1 bleibt unberuehrt.
            self.assertFalse(app.decks[1].state.has_track)
        finally:
            app.close()

    def test_panel_input_reaches_the_bound_deck_only(self) -> None:
        from virtual_cdj.app import CdjApplication

        app = CdjApplication(
            [1, 2], start_audio=False, operating_mode=OperatingMode.CDJ
        )
        try:
            app.load_track_now(1, fixtures().wav)
            app.input_layer.press(ids.PLAY)
            self.assertIs(
                app.decks[1].state.play_state, PlayState.PLAYING
            )
            self.assertIs(app.decks[2].state.play_state, PlayState.EMPTY)
        finally:
            app.close()

    def test_metrics_are_available_for_the_debug_overlay(self) -> None:
        from virtual_cdj.app import CdjApplication

        app = CdjApplication(
            [1], start_audio=False, operating_mode=OperatingMode.CDJ
        )
        try:
            app.load_track_now(1, fixtures().wav)
            self.assertIsNotNone(app.audio.metrics)
            self.assertIsNotNone(app.loader.metrics)
            self.assertGreaterEqual(
                app.loader.metrics.analysed + app.loader.metrics.cached, 1
            )
            self.assertTrue(app.audio_status_text)
        finally:
            app.close()
