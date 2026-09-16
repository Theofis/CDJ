"""Ein aktiver Loop darf die Wiedergabe niemals anhalten.

Der Fehler, um den es hier geht: die Position blieb exakt am Loop-Ende
stehen. ``playing`` blieb ``True``, ``loop.active`` blieb ``True``, und
trotzdem bewegte sich nichts mehr.

Die Ursache lag in ``DeckVoice.render``. ``_frames_until_boundary`` schnitt
den Rest bis zur Loop-Grenze mit ``int()`` ab. Blieb bis zur Grenze weniger
als ein ganzes Sample, kam 0 heraus - und 0 wurde als "Grenze erreicht"
gelesen. Der Umlauf rechnete dann mit einem **negativen** Ueberhang, und
Pythons Modulo bildete den genau auf dieselbe Position zurueck. Die Stimme
lief im Kreis, ohne ein einziges Sample zu erzeugen.

Erreichbar war das immer dann, wenn die Position nicht auf einem ganzen
Sample lag - nach ``seek_seconds`` auf einen krummen Zeitpunkt (Hot Cue,
Beat Jump, quantisierter Loop-Eingang), nach einem Jog-Stoss, oder bei jedem
Tempo ausser genau 0 %.

Die Tests hier decken beide Wege ab: die Ausgabe (``DeckVoice``) und das
Deck darueber mit angehaengter Ausgabe.
"""

from __future__ import annotations

import unittest

import numpy as np

from virtual_cdj.audio.engine import MIN_LOOP_FRAMES, DeckVoice
from virtual_cdj.deck.commands import CommandType, command
from virtual_cdj.deck.engine import Deck
from virtual_cdj.deck.state import (
    BeatGrid,
    LoopExitReason,
    PadMode,
    PlayState,
    TrackInfo,
)

SR = 48000
BLOCK = 512
DURATION_S = 60.0


def samples(duration_s: float = DURATION_S) -> np.ndarray:
    """Eine Rampe - jede Position ist am Wert erkennbar."""
    count = int(SR * duration_s)
    ramp = np.linspace(-1.0, 1.0, count, dtype=np.float32)
    return np.stack((ramp, ramp), axis=1)


class Voice:
    """``DeckVoice`` mit von Hand getaktetem Audio-Thread."""

    def __init__(self, speed: float = 1.0) -> None:
        self.voice = DeckVoice(1, sample_rate=SR)
        self.voice.load(samples())
        self.voice.set_playing(True)
        self.voice.set_speed(speed)
        self.out = np.zeros((BLOCK, 2), dtype=np.float32)

    def loop(self, start_s: float, end_s: float) -> None:
        self.voice.set_loop(True, start_s, end_s)

    def seek(self, seconds: float) -> None:
        self.voice.seek_seconds(seconds)

    def block(self) -> bool:
        self.out[:] = 0.0
        return self.voice.render(self.out, BLOCK)

    def run(self, blocks: int) -> list[float]:
        """``blocks`` Bloecke rendern. Rueckgabe: Position je Block."""
        return [self.block() or True and self.position for _ in range(blocks)]

    @property
    def position(self) -> float:
        return self.voice.position_frames / SR

    @property
    def frames(self) -> float:
        return self.voice.position_frames


# ----------------------------------------------------------------------
# Die eigentliche Ursache
# ----------------------------------------------------------------------


class StallAtLoopOutTests(unittest.TestCase):
    """Der Fehlerfall selbst: weniger als ein Sample vor der Grenze."""

    def test_a_position_a_fraction_before_loop_out_still_wraps(self) -> None:
        """Genau der Zustand, in dem die Wiedergabe stehen blieb."""
        v = Voice()
        in_s, out_s = 10.0, 12.0
        v.loop(in_s, out_s)
        # Eine halbe Sampledauer vor dem Loop-Ende.
        v.seek(out_s - 0.5 / SR)
        before = v.frames

        v.block()

        self.assertNotAlmostEqual(
            v.frames, before, places=6,
            msg="Die Position hat sich kein Stueck bewegt",
        )
        self.assertTrue(
            in_s <= v.position < out_s,
            f"Position {v.position:.6f} liegt nicht im Loop",
        )

    def test_the_voice_keeps_producing_audio_at_that_position(self) -> None:
        v = Voice()
        v.loop(10.0, 12.0)
        v.seek(12.0 - 0.5 / SR)
        self.assertTrue(
            v.block(), "Die Stimme hat keinen Ton geliefert"
        )

    def test_it_does_not_stall_for_any_sub_sample_offset(self) -> None:
        for numerator in (1, 2, 3, 5, 7, 9, 15, 31, 63, 99):
            offset = numerator / 100.0 / SR
            with self.subTest(offset=f"{numerator}/100 Sample"):
                v = Voice()
                v.loop(10.0, 12.0)
                v.seek(12.0 - offset)
                before = v.frames
                v.block()
                self.assertNotAlmostEqual(v.frames, before, places=6)


class TempoTests(unittest.TestCase):
    """Bei jedem Tempo ausser genau 0 % ist die Position gebrochen."""

    def test_no_tempo_ever_freezes_the_position(self) -> None:
        for percent in (-16.0, -6.0, -1.0, 0.0, 1.0, 6.0, 16.0, 50.0, 100.0):
            speed = 1.0 + percent / 100.0
            with self.subTest(tempo=f"{percent:+g} %"):
                v = Voice(speed=speed)
                v.loop(10.0, 12.0)
                v.seek(10.0)
                seen: list[float] = []
                for _ in range(400):
                    v.block()
                    seen.append(v.frames)
                self.assertEqual(
                    len(seen), 400,
                )
                # Kein Block darf die Position unveraendert lassen.
                frozen = [
                    i for i in range(1, len(seen))
                    if abs(seen[i] - seen[i - 1]) < 1e-9
                ]
                self.assertEqual(
                    frozen, [],
                    f"Position eingefroren in {len(frozen)} Bloecken",
                )

    def test_every_tempo_stays_inside_the_loop(self) -> None:
        for percent in (-16.0, -8.0, 0.0, 8.0, 16.0):
            speed = 1.0 + percent / 100.0
            with self.subTest(tempo=f"{percent:+g} %"):
                v = Voice(speed=speed)
                v.loop(10.0, 12.0)
                v.seek(10.0)
                for _ in range(500):
                    v.block()
                    self.assertTrue(
                        10.0 - 1e-6 <= v.position < 12.0 + 1e-6,
                        f"{v.position:.6f} liegt ausserhalb",
                    )


class OvershootTests(unittest.TestCase):
    """Der ueberschrittene Teil muss ab Loop In weiterlaufen."""

    def test_the_overshoot_continues_from_loop_in(self) -> None:
        """Das Beispiel aus der Aufgabe, in Samples gerechnet.

        Ein Block traegt von kurz vor Loop Out nach kurz danach. Die neue
        Position ist dann nicht Loop Out, sondern Loop In plus dem Teil,
        der ueber die Grenze hinausging.
        """
        v = Voice()
        in_s, out_s = 10.0, 12.0
        v.loop(in_s, out_s)
        # 100 Samples vor dem Ende: ein Block von 512 laeuft 412 darueber.
        v.seek(out_s - 100 / SR)
        v.block()
        expected = in_s + (BLOCK - 100) / SR
        self.assertAlmostEqual(v.position, expected, places=6)

    def test_no_drift_over_a_long_run(self) -> None:
        """Nach 4000 Bloecken - rund 43 s - muss die Phase exakt stimmen."""
        v = Voice()
        in_s, out_s = 10.0, 12.0
        length = out_s - in_s
        v.loop(in_s, out_s)
        v.seek(in_s)
        blocks = 4000
        for _ in range(blocks):
            v.block()
        travelled = blocks * BLOCK / SR
        self.assertAlmostEqual(v.position, in_s + travelled % length, places=6)
        # 43 s auf einem 2-s-Loop sind 21 Umlaeufe.
        self.assertEqual(v.voice.loop_wraps, int(travelled // length))

    def test_no_drift_over_a_thousand_wraps(self) -> None:
        """Derselbe Nachweis mit einem kurzen Loop - ueber 1000 Umlaeufe.

        Gerechnet wird in Samples, damit der Erwartungswert nicht selbst
        von Gleitkommaresten abhaengt.
        """
        v = Voice()
        in_frames, length_frames = 10 * SR, 1920
        v.loop(in_frames / SR, (in_frames + length_frames) / SR)
        v.seek(in_frames / SR)
        blocks = 4000
        for _ in range(blocks):
            v.block()
        expected = in_frames + (blocks * BLOCK) % length_frames
        self.assertAlmostEqual(v.frames, expected, places=3)
        self.assertGreater(v.voice.loop_wraps, 1000)

    def test_a_loop_shorter_than_a_block_wraps_many_times(self) -> None:
        v = Voice()
        v.loop(10.0, 10.0 + 64 / SR)
        v.seek(10.0)
        before = v.voice.loop_wraps
        v.block()
        self.assertGreater(v.voice.loop_wraps - before, 1)
        self.assertTrue(10.0 <= v.position < 10.0 + 65 / SR)


class LoopOutAdjustTests(unittest.TestCase):
    """Eine neue Grenze gilt sofort - kein alter Wert bleibt liegen."""

    def test_a_new_loop_out_takes_effect_on_the_next_block(self) -> None:
        v = Voice()
        v.loop(10.0, 12.0)
        v.seek(10.0)
        v.block()
        # Loop Out nach vorne ziehen, hinter die aktuelle Position.
        v.loop(10.0, 10.5)
        for _ in range(200):
            v.block()
            self.assertLess(
                v.position, 10.5 + 1e-6,
                "Die Ausgabe benutzt noch das alte Loop Out",
            )

    def test_moving_loop_out_behind_the_playhead_wraps_at_once(self) -> None:
        """Neue Grenze liegt hinter der Position: sofort zurueck zu Loop In."""
        v = Voice()
        v.loop(10.0, 12.0)
        v.seek(11.9)
        v.block()
        self.assertGreater(v.position, 11.8)
        v.loop(10.0, 10.5)
        v.block()
        self.assertTrue(
            10.0 <= v.position < 10.5,
            f"Position {v.position:.6f} haengt hinter der neuen Grenze",
        )

    def test_repeated_adjustment_never_stalls(self) -> None:
        v = Voice()
        v.loop(10.0, 12.0)
        v.seek(10.0)
        out_s = 12.0
        for step in range(120):
            # Abwechselnd verkuerzen und verlaengern, jedes Mal krumm.
            out_s = 10.0 + 0.37 + (step % 17) * 0.043
            v.loop(10.0, out_s)
            before = v.frames
            v.block()
            self.assertNotAlmostEqual(
                v.frames, before, places=6,
                msg=f"Schritt {step}: Position steht bei {v.position:.6f}",
            )

    def test_a_loop_out_at_or_before_loop_in_is_stretched_not_dropped(
        self,
    ) -> None:
        v = Voice()
        v.seek(10.0)
        v.loop(10.0, 10.0)
        for _ in range(50):
            v.block()
        self.assertTrue(v.voice._loop_active)  # noqa: SLF001
        self.assertTrue(
            10.0 <= v.position <= 10.0 + MIN_LOOP_FRAMES / SR + 1e-6
        )


class JogInsideLoopTests(unittest.TestCase):
    """Ein Jog-Stoss macht die Position gebrochen - auch das darf nicht
    stehen bleiben."""

    def test_a_nudge_just_short_of_loop_out_does_not_stall(self) -> None:
        v = Voice()
        v.loop(10.0, 12.0)
        v.seek(10.0)
        v.block()
        # Bis kurz vor die Grenze stossen, mit krummem Rest.
        v.voice.nudge_seconds(12.0 - 0.3 / SR - v.position)
        before = v.frames
        v.block()
        self.assertNotAlmostEqual(v.frames, before, places=6)

    def test_many_nudges_never_freeze_the_voice(self) -> None:
        v = Voice()
        v.loop(10.0, 12.0)
        v.seek(10.0)
        for step in range(200):
            v.voice.nudge_seconds(((step % 7) - 3) * 0.0131)
            before = v.frames
            v.block()
            self.assertNotAlmostEqual(
                v.frames, before, places=6,
                msg=f"Schritt {step}: eingefroren bei {v.position:.6f}",
            )


# ----------------------------------------------------------------------
# Dasselbe eine Schicht hoeher: Deck mit angehaengter Ausgabe
# ----------------------------------------------------------------------


class DeckRig:
    """Deck mit echter Stimme. Der Audio-Thread wird von Hand getaktet."""

    def __init__(self, bpm: float = 120.0) -> None:
        self.voice = DeckVoice(1, sample_rate=SR)
        self.deck = Deck(1)
        self.deck.attach_playback(self.voice)
        self.deck.load_track(
            TrackInfo(
                track_id="t", title="T", duration_s=DURATION_S,
                original_bpm=bpm,
                beat_grid=BeatGrid(first_beat_s=0.0, bpm=bpm, beats_per_bar=4),
            ),
            samples(),
        )
        self.deck.execute(
            command(CommandType.PAD_MODE, 1, mode=PadMode.BEAT_LOOP)
        )
        self.out = np.zeros((BLOCK, 2), dtype=np.float32)

    def send(self, kind: CommandType, **params) -> None:
        self.deck.execute(command(kind, 1, "TEST", **params))

    def play(self) -> None:
        if not self.deck.state.is_playing:
            self.send(CommandType.PLAY_PAUSE)

    def blocks(self, count: int) -> list[float]:
        """Audioblöcke rendern und das Deck jeweils nachziehen."""
        seen = []
        for _ in range(count):
            self.out[:] = 0.0
            self.voice.render(self.out, BLOCK)
            self.deck.tick()
            seen.append(self.deck.state.position_s)
        return seen

    @property
    def loop(self):
        return self.deck.state.loop


class DeckLoopTests(unittest.TestCase):
    def rig(self) -> DeckRig:
        rig = DeckRig()
        rig.send(CommandType.SEEK, position_s=10.0)
        rig.play()
        return rig

    def assert_moving(self, seen: list[float], label: str = "") -> None:
        frozen = [
            i for i in range(1, len(seen))
            if abs(seen[i] - seen[i - 1]) < 1e-9
        ]
        self.assertEqual(
            frozen, [],
            f"{label}: Position steht in {len(frozen)} von "
            f"{len(seen)} Bloecken still",
        )

    def test_a_beat_loop_runs_without_ever_stopping(self) -> None:
        rig = self.rig()
        rig.send(CommandType.BEAT_LOOP, beats=4.0)
        self.assertTrue(rig.loop.active)
        self.assert_moving(rig.blocks(600), "4-Beat-Loop")
        self.assertTrue(rig.loop.active)
        self.assertIs(rig.deck.state.play_state, PlayState.PLAYING)

    def test_the_position_never_rests_on_loop_out(self) -> None:
        rig = self.rig()
        rig.send(CommandType.BEAT_LOOP, beats=1.0)
        out_s = rig.loop.out_s
        assert out_s is not None
        for position in rig.blocks(800):
            self.assertNotAlmostEqual(
                position, out_s, places=6,
                msg="Die Position liegt genau auf Loop Out",
            )

    def test_halving_and_doubling_never_stops_playback(self) -> None:
        rig = self.rig()
        rig.send(CommandType.BEAT_LOOP, beats=4.0)
        for step in range(30):
            rig.send(
                CommandType.LOOP_HALVE if step % 2 == 0
                else CommandType.LOOP_DOUBLE
            )
            self.assert_moving(rig.blocks(20), f"Schritt {step}")
            self.assertTrue(rig.loop.active, f"Schritt {step}: Loop weg")

    def test_every_beat_loop_pad_in_turn_keeps_it_running(self) -> None:
        rig = self.rig()
        for index in range(8):
            rig.send(CommandType.PAD, index=index, pressed=True)
            rig.send(CommandType.PAD, index=index, pressed=False)
            self.assert_moving(rig.blocks(30), f"Pad {index}")

    def test_exit_and_reloop_many_times(self) -> None:
        rig = self.rig()
        rig.send(CommandType.BEAT_LOOP, beats=2.0)
        for round_number in range(25):
            rig.send(CommandType.RELOOP_EXIT)
            self.assertFalse(rig.loop.active, f"Runde {round_number}")
            self.assert_moving(rig.blocks(20), f"Runde {round_number} aus")
            rig.send(CommandType.RELOOP_EXIT)
            self.assertTrue(rig.loop.active, f"Runde {round_number}")
            self.assert_moving(rig.blocks(20), f"Runde {round_number} an")

    def test_quantize_on_and_off_does_not_stop_the_loop(self) -> None:
        rig = self.rig()
        rig.send(CommandType.BEAT_LOOP, beats=4.0)
        for step in range(20):
            rig.send(CommandType.QUANTIZE_TOGGLE)
            self.assert_moving(rig.blocks(25), f"Quantize Schritt {step}")
            self.assertTrue(rig.loop.active)

    def test_loop_in_and_out_adjust_with_the_jog(self) -> None:
        rig = self.rig()
        rig.send(CommandType.BEAT_LOOP, beats=4.0)
        rig.send(CommandType.LOOP_IN)  # IN ADJUST an
        for step in range(40):
            rig.send(
                CommandType.JOG_MOVE, delta=40 if step % 2 else -40,
                velocity=0.0, direction="CW", touched=True,
                ticks_per_rev=800,
            )
            self.assert_moving(rig.blocks(10), f"IN ADJ Schritt {step}")
        rig.send(CommandType.LOOP_IN)
        rig.send(CommandType.LOOP_OUT)  # OUT ADJUST an
        for step in range(40):
            rig.send(
                CommandType.JOG_MOVE, delta=-40 if step % 2 else 40,
                velocity=0.0, direction="CW", touched=True,
                ticks_per_rev=800,
            )
            self.assert_moving(rig.blocks(10), f"OUT ADJ Schritt {step}")
        self.assertTrue(rig.loop.active)

    def test_beat_jump_and_cue_around_a_loop(self) -> None:
        rig = self.rig()
        rig.send(CommandType.BEAT_LOOP, beats=4.0)
        rig.blocks(30)
        rig.send(CommandType.BEAT_JUMP, direction=1, beats=4.0)
        self.assert_moving(rig.blocks(30), "nach Beat Jump")
        rig.send(CommandType.CUE, pressed=True)
        rig.send(CommandType.CUE, pressed=False)
        rig.play()
        self.assert_moving(rig.blocks(30), "nach Cue")

    def test_a_long_run_stays_inside_the_loop(self) -> None:
        """Ueber 4000 Bloecke - rund 43 Sekunden Ausgabe."""
        rig = self.rig()
        rig.send(CommandType.BEAT_LOOP, beats=1.0)
        in_s, out_s = rig.loop.in_s, rig.loop.out_s
        assert in_s is not None and out_s is not None
        seen = rig.blocks(4000)
        self.assert_moving(seen, "Dauerlauf")
        for position in seen:
            self.assertTrue(
                in_s - 1e-6 <= position < out_s + 1e-6,
                f"{position:.6f} liegt ausserhalb [{in_s}, {out_s})",
            )
        self.assertTrue(rig.loop.active)
        self.assertIsNone(rig.loop.exit_reason)

    def test_a_mixed_session_never_leaves_the_loop_hanging(self) -> None:
        """Alles nacheinander - so entstand der Fehler beim Ausprobieren."""
        rig = self.rig()
        actions = [
            (CommandType.BEAT_LOOP, {"beats": 4.0}),
            (CommandType.LOOP_HALVE, {}),
            (CommandType.LOOP_DOUBLE, {}),
            (CommandType.QUANTIZE_TOGGLE, {}),
            (CommandType.LOOP_HALVE, {}),
            (CommandType.RELOOP_EXIT, {}),
            (CommandType.RELOOP_EXIT, {}),
            (CommandType.BEAT_LOOP, {"beats": 0.5}),
            (CommandType.LOOP_DOUBLE, {}),
            (CommandType.QUANTIZE_TOGGLE, {}),
        ]
        for round_number in range(12):
            for kind, params in actions:
                rig.send(kind, **params)
                self.assert_moving(
                    rig.blocks(12), f"Runde {round_number} {kind.value}"
                )
        self.assertIs(rig.deck.state.play_state, PlayState.PLAYING)

    def test_leaving_and_relooping_keeps_the_same_boundaries(self) -> None:
        rig = self.rig()
        rig.send(CommandType.BEAT_LOOP, beats=4.0)
        in_s, out_s = rig.loop.in_s, rig.loop.out_s
        rig.send(CommandType.RELOOP_EXIT)
        self.assertIs(rig.loop.exit_reason, LoopExitReason.RELOOP_EXIT)
        rig.blocks(100)
        rig.send(CommandType.RELOOP_EXIT)
        self.assertTrue(rig.loop.active)
        self.assertAlmostEqual(rig.loop.in_s, in_s, places=9)
        self.assertAlmostEqual(rig.loop.out_s, out_s, places=9)
        self.assert_moving(rig.blocks(100), "nach Reloop")


class SingleAuthorityTests(unittest.TestCase):
    """Deck-State und Ausgabe muessen dieselbe Grenze fuehren."""

    def test_the_voice_uses_exactly_the_decks_loop_boundaries(self) -> None:
        rig = DeckRig()
        rig.send(CommandType.SEEK, position_s=10.0)
        rig.play()
        for beats in (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0):
            with self.subTest(beats=beats):
                rig.send(CommandType.BEAT_LOOP, beats=beats)
                rig.blocks(2)
                loop = rig.loop
                assert loop.in_s is not None and loop.out_s is not None
                start = int(round(loop.in_s * SR))
                end = max(
                    int(round(loop.out_s * SR)), start + MIN_LOOP_FRAMES
                )
                self.assertTrue(rig.voice._loop_active)  # noqa: SLF001
                self.assertEqual(rig.voice._loop_start, start)  # noqa: SLF001
                self.assertEqual(rig.voice._loop_end, end)  # noqa: SLF001

    def test_an_adjustment_reaches_the_voice_immediately(self) -> None:
        rig = DeckRig()
        rig.send(CommandType.SEEK, position_s=10.0)
        rig.play()
        rig.send(CommandType.BEAT_LOOP, beats=4.0)
        for _ in range(6):
            rig.send(CommandType.LOOP_HALVE)
            rig.blocks(2)
            loop = rig.loop
            assert loop.out_s is not None
            end = max(
                int(round(loop.out_s * SR)),
                int(round(loop.in_s * SR)) + MIN_LOOP_FRAMES,
            )
            self.assertEqual(rig.voice._loop_end, end)  # noqa: SLF001

    def test_leaving_the_loop_switches_the_voice_off_too(self) -> None:
        rig = DeckRig()
        rig.send(CommandType.SEEK, position_s=10.0)
        rig.play()
        rig.send(CommandType.BEAT_LOOP, beats=4.0)
        rig.blocks(2)
        self.assertTrue(rig.voice._loop_active)  # noqa: SLF001
        rig.send(CommandType.RELOOP_EXIT)
        rig.blocks(2)
        self.assertFalse(rig.voice._loop_active)  # noqa: SLF001


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
