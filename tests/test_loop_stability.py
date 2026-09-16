"""Bleibt ein Loop stabil? Die Testfaelle aus den Abschnitten 25-31.

Die Frage hinter allen Tests ist immer dieselbe: kann ``loop.active`` von
``True`` auf ``False`` fallen, **ohne** dass jemand es ausdruecklich
verlangt hat. Es gibt genau drei erlaubte Gruende
(``LoopExitReason``); alles andere ist ein Fehler.

Geprueft werden beide Transportwege - die Wanduhr (ohne Audioausgabe) und
die sample-genaue Ausgabe (``DeckVoice``). Die Loop-Ausfuehrung liegt in
der Playback-Schicht, nicht in der Oberflaeche; deshalb muss ein Loop auch
unter GUI-Last unveraendert weiterlaufen.
"""

from __future__ import annotations

import unittest

import numpy as np

from virtual_cdj.audio.engine import (
    MAX_LOOP_WRAPS_PER_BLOCK,
    MIN_LOOP_FRAMES,
    DeckVoice,
)
from virtual_cdj.core import ids
from virtual_cdj.core.input_layer import InputLayer
from virtual_cdj.deck.commands import CommandType, command
from virtual_cdj.deck.engine import Deck
from virtual_cdj.deck.mapping import InputMapper
from virtual_cdj.deck.state import (
    BEAT_LOOP_LENGTHS,
    BeatGrid,
    LoopExitReason,
    PadMode,
    PlayState,
    TrackInfo,
)

SR = 48000
BLOCK = 512
FRAME_S = 0.016


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_track(bpm: float = 128.0, duration_s: float = 600.0) -> TrackInfo:
    return TrackInfo(
        track_id="t", title="T", duration_s=duration_s, original_bpm=bpm,
        beat_grid=BeatGrid(first_beat_s=0.0, bpm=bpm),
    )


class LoopFixture:
    """Deck mit Beatgrid, Eingabekette und laufendem Loop."""

    def __init__(self, bpm: float = 128.0) -> None:
        self.clock = Clock()
        self.deck = Deck(1, time_source=self.clock)
        self.deck.load_track(make_track(bpm))
        self.layer = InputLayer()
        self.layer.subscribe(InputMapper(1, self.deck.execute).handle_event)
        self.deck.execute(
            command(CommandType.PAD_MODE, 1, mode=PadMode.BEAT_LOOP)
        )

    def seek(self, position_s: float) -> None:
        self.deck.execute(
            command(CommandType.SEEK, 1, position_s=position_s)
        )

    def beat_loop(self, beats: float) -> None:
        self.deck.execute(command(CommandType.BEAT_LOOP, 1, beats=beats))

    def play(self) -> None:
        self.deck.execute(command(CommandType.PLAY_PAUSE, 1))

    def tap(self, control_id: str) -> None:
        self.layer.press(control_id)
        self.layer.release(control_id)

    def run(self, seconds: float, step: float = FRAME_S) -> None:
        for _ in range(int(seconds / step)):
            self.clock.advance(step)
            self.deck.tick()

    @property
    def loop(self):
        return self.deck.state.loop


# ----------------------------------------------------------------------
# TEST 25: 100 Wiederholungen
# ----------------------------------------------------------------------


class HundredRepetitionsTests(unittest.TestCase):
    def test_25_a_four_beat_loop_survives_a_hundred_repetitions(self) -> None:
        f = LoopFixture(bpm=128.0)
        f.seek(10.0)
        f.beat_loop(4.0)
        f.play()
        start = f.loop
        self.assertTrue(start.active)
        length = start.length_s

        repetitions = 100
        steps = int(repetitions * length / FRAME_S)
        for index in range(steps):
            f.clock.advance(FRAME_S)
            f.deck.tick()
            loop = f.loop
            self.assertTrue(
                loop.active,
                f"Loop nach {index} Bildern beendet, Grund="
                f"{loop.exit_reason.value if loop.exit_reason else '-'}",
            )
            # Grenzen und Laenge bleiben unveraendert - sie werden nicht
            # bei jedem Bild neu gerechnet.
            self.assertAlmostEqual(loop.in_s, start.in_s, places=9)
            self.assertAlmostEqual(loop.out_s, start.out_s, places=9)
            self.assertEqual(loop.beats, 4.0)
            position = f.deck.state.position_s
            self.assertGreaterEqual(position, loop.in_s - 1e-6)
            self.assertLess(position, loop.out_s + 1e-6)

        self.assertIsNone(f.loop.exit_reason)

    def test_25b_no_drift_over_many_repetitions(self) -> None:
        """Der Ueberhang wird uebertragen, die Phase laeuft nicht weg."""
        f = LoopFixture(bpm=128.0)
        f.seek(10.0)
        f.beat_loop(4.0)
        f.play()
        length = f.loop.length_s

        # Eine ganze Zahl von Umlaeufen: danach muss die Position wieder
        # (fast) auf dem Loop-Anfang stehen.
        laps = 60
        total = laps * length
        step = total / round(total / FRAME_S)
        for _ in range(round(total / step)):
            f.clock.advance(step)
            f.deck.tick()
        offset = (f.deck.state.position_s - f.loop.in_s) % length
        drift = min(offset, length - offset)
        self.assertLess(drift, 1e-6, f"Drift nach {laps} Umlaeufen: {drift}")


# ----------------------------------------------------------------------
# TEST 26/27: Laenge aendern und Pads
# ----------------------------------------------------------------------


class LengthChangeTests(unittest.TestCase):
    def test_26_changing_the_length_keeps_the_loop_active(self) -> None:
        f = LoopFixture()
        f.seek(10.0)
        f.beat_loop(4.0)
        f.play()
        start_in = f.loop.in_s

        for beats in (8.0, 16.0, 2.0, 1.0, 0.5):
            f.run(0.1)
            f.beat_loop(beats)
            self.assertTrue(f.loop.active, f"nach {beats} Beats beendet")
            self.assertEqual(f.loop.beats, beats)
            # Der Anfang bleibt stehen, nur das Ende wandert.
            self.assertAlmostEqual(f.loop.in_s, start_in, places=9)
            self.assertIsNone(f.loop.exit_reason)

    def test_26b_halve_and_double_keep_the_loop_active(self) -> None:
        f = LoopFixture()
        f.seek(10.0)
        f.beat_loop(8.0)
        f.play()
        for command_type, expected in (
            (CommandType.LOOP_HALVE, 4.0),
            (CommandType.LOOP_HALVE, 2.0),
            (CommandType.LOOP_DOUBLE, 4.0),
            (CommandType.LOOP_DOUBLE, 8.0),
        ):
            f.deck.execute(command(command_type, 1))
            f.run(0.05)
            self.assertTrue(f.loop.active)
            self.assertEqual(f.loop.beats, expected)

    def test_27_beat_loop_pads_never_end_the_loop(self) -> None:
        """Abschnitt 8/27: Pad-Druck setzt oder aendert, Loslassen tut nichts."""
        f = LoopFixture()
        f.seek(10.0)
        f.play()

        f.tap(ids.PAD_E)                      # 4 Beats
        self.assertTrue(f.loop.active)
        self.assertEqual(f.loop.beats, 4.0)
        f.run(0.2)
        self.assertTrue(f.loop.active, "Loslassen hat den Loop beendet")

        f.tap(ids.PAD_F)                      # 8 Beats
        self.assertTrue(f.loop.active)
        self.assertEqual(f.loop.beats, 8.0)

        f.tap(ids.PAD_B)                      # 1/2 Beat
        self.assertTrue(f.loop.active)
        self.assertEqual(f.loop.beats, 0.5)
        self.assertIsNone(f.loop.exit_reason)

    def test_27b_pad_release_alone_changes_nothing(self) -> None:
        f = LoopFixture()
        f.seek(10.0)
        f.beat_loop(4.0)
        before = f.loop
        f.layer.press(ids.PAD_E)
        f.layer.release(ids.PAD_E)
        f.layer.release(ids.PAD_E)            # zweites Loslassen
        self.assertTrue(f.loop.active)
        self.assertAlmostEqual(f.loop.in_s, before.in_s, places=9)

    def test_27c_every_pad_length_comes_from_the_central_table(self) -> None:
        f = LoopFixture()
        f.seek(10.0)
        f.play()
        for index, beats in enumerate(BEAT_LOOP_LENGTHS):
            f.deck.execute(command(CommandType.PAD, 1, index=index, pressed=True))
            self.assertTrue(f.loop.active, f"Pad {index} hat den Loop beendet")
            self.assertEqual(f.loop.beats, beats)


# ----------------------------------------------------------------------
# TEST 28/29/30: Transport, GUI-Last, Tempo
# ----------------------------------------------------------------------


class TransportTests(unittest.TestCase):
    def test_28_pause_and_play_keep_the_loop(self) -> None:
        f = LoopFixture()
        f.seek(10.0)
        f.beat_loop(4.0)
        f.play()
        f.run(0.5)
        before = f.loop

        f.play()                              # Pause
        self.assertIs(f.deck.state.play_state, PlayState.PAUSED)
        self.assertTrue(f.loop.active)
        f.run(1.0)
        self.assertTrue(f.loop.active)

        f.play()                              # weiter
        f.run(1.0)
        self.assertTrue(f.loop.active)
        self.assertAlmostEqual(f.loop.in_s, before.in_s, places=9)
        self.assertAlmostEqual(f.loop.out_s, before.out_s, places=9)
        position = f.deck.state.position_s
        self.assertGreaterEqual(position, f.loop.in_s - 1e-6)
        self.assertLess(position, f.loop.out_s + 1e-6)

    def test_29_nothing_that_only_reads_the_state_ends_the_loop(self) -> None:
        """Abschnitt 3/29: Anzeige, Tempo, Quantize, Beatgrid, Jog, Search.

        Alles hier liest den Zustand oder aendert etwas anderes. Keines
        davon darf den Loop beenden.
        """
        f = LoopFixture()
        f.seek(10.0)
        f.beat_loop(4.0)
        f.play()
        before = f.loop

        harmless = (
            ("Zustand lesen", lambda: f.deck.state),
            ("Quantize", lambda: f.tap(ids.QUANTIZE)),
            ("Tempo", lambda: f.deck.execute(
                command(CommandType.TEMPO_SET, 1, value=0.8))),
            ("Tempo zurueck", lambda: f.deck.execute(
                command(CommandType.TEMPO_SET, 1, value=0.5))),
            ("Beatgrid verschieben", lambda: f.deck.execute(
                command(CommandType.BEATGRID_SHIFT, 1, delta_s=0.01))),
            ("Pad-Modus", lambda: f.tap(ids.HOT_CUE)),
            ("Pad-Modus zurueck", lambda: f.tap(ids.BEAT_JUMP)),
            ("Jog", lambda: f.deck.execute(command(
                CommandType.JOG_MOVE, 1, delta=200, ticks_per_rev=800))),
            ("Jog weit", lambda: f.deck.execute(command(
                CommandType.JOG_MOVE, 1, delta=8000, ticks_per_rev=800))),
            ("Search halten", lambda: f.deck.execute(command(
                CommandType.SEARCH, 1, direction=+1, pressed=True))),
            ("Search los", lambda: f.deck.execute(command(
                CommandType.SEARCH, 1, direction=+1, pressed=False))),
            ("Master", lambda: f.tap(ids.MASTER)),
            ("Sync", lambda: f.tap(ids.BEAT_SYNC)),
            ("Slip", lambda: f.tap(ids.SLIP)),
            ("4 BEAT", lambda: f.tap(ids.BEAT_LOOP_4)),
            ("8 BEAT", lambda: f.tap(ids.BEAT_LOOP_8)),
        )
        for label, action in harmless:
            with self.subTest(label):
                action()
                f.run(0.1)
                self.assertTrue(
                    f.loop.active,
                    f"{label} hat den Loop beendet, Grund="
                    f"{f.loop.exit_reason.value if f.loop.exit_reason else '-'}",
                )
        self.assertAlmostEqual(f.loop.in_s, before.in_s, places=9)

    def test_30_the_loop_is_stable_at_every_tempo(self) -> None:
        for bpm in (60.0, 90.0, 128.0, 150.0, 155.0, 160.0, 174.0, 200.0):
            with self.subTest(bpm=bpm):
                f = LoopFixture(bpm=bpm)
                f.seek(10.0)
                f.beat_loop(4.0)
                f.play()
                start = f.loop
                f.run(40 * start.length_s)
                self.assertTrue(f.loop.active)
                self.assertAlmostEqual(f.loop.in_s, start.in_s, places=9)
                self.assertAlmostEqual(f.loop.out_s, start.out_s, places=9)
                position = f.deck.state.position_s
                self.assertGreaterEqual(position, f.loop.in_s - 1e-6)
                self.assertLess(position, f.loop.out_s + 1e-6)

    def test_30b_a_tempo_change_does_not_move_the_loop(self) -> None:
        """Abschnitt 6: die Grenzen bleiben, wo sie gesetzt wurden."""
        f = LoopFixture()
        f.seek(10.0)
        f.beat_loop(4.0)
        f.play()
        before = f.loop
        f.deck.execute(command(CommandType.TEMPO_SET, 1, value=1.0))
        f.run(2.0)
        self.assertAlmostEqual(f.loop.in_s, before.in_s, places=9)
        self.assertAlmostEqual(f.loop.out_s, before.out_s, places=9)
        self.assertTrue(f.loop.active)


# ----------------------------------------------------------------------
# TEST 31: Blockgrenzen in der Audioausgabe
# ----------------------------------------------------------------------


def ramp_samples(seconds: float = 20.0) -> np.ndarray:
    count = int(SR * seconds)
    samples = np.zeros((count, 2), dtype=np.float32)
    samples[:, 0] = np.arange(count, dtype=np.float32) / count
    samples[:, 1] = samples[:, 0]
    return samples


class AudioLoopTests(unittest.TestCase):
    """Die Ausgabe laeuft blockweise - der Loop darf das nicht merken."""

    def voice(self) -> DeckVoice:
        voice = DeckVoice(1, sample_rate=SR)
        voice.load(ramp_samples())
        voice.set_playing(True)
        voice.set_speed(1.0)
        return voice

    def render(
        self, voice: DeckVoice, start_s: float, end_s: float, blocks: int = 400
    ) -> int:
        out = np.zeros((BLOCK, 2), dtype=np.float32)
        outside = 0
        for _ in range(blocks):
            out[:] = 0.0
            voice.render(out, BLOCK)
            position = voice.position_frames / SR
            if not (start_s - 1e-6 <= position < end_s + 1e-6):
                outside += 1
        return outside

    def test_31_loop_ends_off_the_block_boundary_still_wrap(self) -> None:
        cases = {
            "4 Beat bei 128": 4 * 60 / 128,
            "1/4 Beat bei 155": 0.25 * 60 / 155,
            "1/64 Beat bei 174": (1 / 64) * 60 / 174,
            "genau ein Block": BLOCK / SR,
            "halber Block": BLOCK / 2 / SR,
            "krumme Laenge": 0.123456,
        }
        for label, length_s in cases.items():
            with self.subTest(label):
                voice = self.voice()
                start, end = 5.0, 5.0 + length_s
                voice.seek_seconds(start)
                voice.set_loop(True, start, end)
                outside = self.render(voice, start, end)
                self.assertEqual(outside, 0, f"{label}: {outside} Bloecke draussen")
                self.assertTrue(voice._loop_active)  # noqa: SLF001

    def test_31b_a_loop_shorter_than_the_minimum_is_stretched_not_dropped(
        self,
    ) -> None:
        """Frueher schaltete die Ausgabe hier still ab.

        ``DeckState.loop.active`` blieb ``True``, die Anzeige zeigte weiter
        einen Loop, und die Wiedergabe lief einfach hindurch - das sah aus
        wie ein Loop, der von selbst aufhoert.
        """
        voice = self.voice()
        start = 5.0
        end = start + (MIN_LOOP_FRAMES - 1) / SR
        voice.seek_seconds(start)
        voice.set_loop(True, start, end)
        limit = start + MIN_LOOP_FRAMES / SR
        outside = self.render(voice, start, limit)
        self.assertEqual(outside, 0)
        self.assertTrue(voice._loop_active)  # noqa: SLF001
        self.assertGreater(voice.loop_wraps, 0)

    def test_31c_the_output_never_turns_a_loop_off_by_itself(self) -> None:
        voice = self.voice()
        voice.seek_seconds(5.0)
        voice.set_loop(True, 5.0, 5.0 + 0.5)
        out = np.zeros((BLOCK, 2), dtype=np.float32)
        for _ in range(200):
            out[:] = 0.0
            voice.render(out, BLOCK)
            self.assertTrue(voice._loop_active)  # noqa: SLF001

    def test_31d_wraps_stay_within_the_block_budget(self) -> None:
        voice = self.voice()
        voice.seek_seconds(5.0)
        voice.set_loop(True, 5.0, 5.0 + MIN_LOOP_FRAMES / SR)
        out = np.zeros((BLOCK, 2), dtype=np.float32)
        before = voice.loop_wraps
        voice.render(out, BLOCK)
        self.assertLessEqual(voice.loop_wraps - before, MAX_LOOP_WRAPS_PER_BLOCK)


# ----------------------------------------------------------------------
# Abschnitt 3/12/24: nur erlaubte Gruende
# ----------------------------------------------------------------------


class ExitReasonTests(unittest.TestCase):
    def leaves(self, action) -> LoopExitReason | None:
        f = LoopFixture()
        f.seek(10.0)
        f.beat_loop(4.0)
        f.play()
        f.run(0.2)
        action(f)
        f.run(0.2)
        return None if f.loop.active else f.loop.exit_reason

    def test_reloop_exit_is_the_normal_way_out(self) -> None:
        reason = self.leaves(lambda f: f.tap(ids.RELOOP_EXIT))
        self.assertIs(reason, LoopExitReason.RELOOP_EXIT)

    def test_an_explicit_jump_outside_names_its_reason(self) -> None:
        reason = self.leaves(
            lambda f: f.deck.execute(
                command(CommandType.SEEK, 1, position_s=120.0)
            )
        )
        self.assertIs(reason, LoopExitReason.JUMPED_OUT)

    def test_the_same_pad_again_names_its_reason(self) -> None:
        f = LoopFixture()
        f.seek(10.0)
        f.play()
        f.tap(ids.PAD_E)
        self.assertTrue(f.loop.active)
        f.tap(ids.PAD_E)
        self.assertFalse(f.loop.active)
        self.assertIs(f.loop.exit_reason, LoopExitReason.BEAT_LOOP_PAD)

    def test_a_new_track_names_its_reason(self) -> None:
        f = LoopFixture()
        f.seek(10.0)
        f.beat_loop(4.0)
        f.deck.load_track(make_track(bpm=140.0))
        self.assertFalse(f.loop.active)
        self.assertIs(f.loop.exit_reason, LoopExitReason.TRACK_CHANGED)

    def test_reloop_brings_the_last_loop_back(self) -> None:
        f = LoopFixture()
        f.seek(10.0)
        f.beat_loop(4.0)
        before = f.loop
        f.tap(ids.RELOOP_EXIT)
        self.assertFalse(f.loop.active)
        f.tap(ids.RELOOP_EXIT)
        self.assertTrue(f.loop.active)
        self.assertAlmostEqual(f.loop.in_s, before.in_s, places=9)
        self.assertAlmostEqual(f.loop.out_s, before.out_s, places=9)
        self.assertIsNone(f.loop.exit_reason)

    def test_a_new_loop_clears_the_old_reason(self) -> None:
        f = LoopFixture()
        f.seek(10.0)
        f.beat_loop(4.0)
        f.tap(ids.RELOOP_EXIT)
        self.assertIsNotNone(f.loop.exit_reason)
        f.beat_loop(8.0)
        self.assertTrue(f.loop.active)
        self.assertIsNone(f.loop.exit_reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
