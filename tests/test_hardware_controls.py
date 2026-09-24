"""Funktionstests der nachgezogenen CDJ-Bedienfunktionen.

Abgedeckt werden genau die Bedienelemente aus der Aufgabenstellung:

* USB STOP (Quelle trennen, **kein** Betriebssystem-Auswurf)
* MEMORY (Cue-Punkt und laufender Loop)
* CUE/LOOP CALL links/rechts (gemerkte Punkte durchblaettern)
* CALL/DELETE (gemerkten Punkt loeschen, Hotcue-Modifikator)
* TIME MODE kurz / AUTO CUE lang auf derselben Taste
* AUTO CUE (Einsatzerkennung im Signal und Wirkung beim Laden)
* manueller Hotcue-Aufrufmodus (kurzer Druck auf CALL/DELETE)
* TRACK SEARCH gehalten + Jogwheel (schneller Trackwechsel)
* Richtungsschalter FWD / REV / SLIP REV
* WIDE -100 % (Stillstand)
* VINYL SPEED ADJUST (Zustand vorhanden, DSP fehlt)

Bewusst **nicht** hier: LEDs jeder Art und die Oberflaeche. Kein Test
oeffnet ein Fenster, zeichnet etwas oder prueft eine Leuchte. Geprueft
werden Zuordnung, Kommandos, Deck-Engine, Zustand und der Datentraeger-
Dienst.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.test_deck import ClockedDeck, make_track
from tests.test_usb_sources import make_stick, volume
from virtual_cdj.app import CdjApplication
from virtual_cdj.core import controls, ids
from virtual_cdj.core.input_layer import InputLayer
from virtual_cdj.core.model import LONG_PRESS_S, Source
from virtual_cdj.deck.commands import CommandType, command
from virtual_cdj.deck.auto_cue import detect_audio_start
from virtual_cdj.deck.engine import TRACK_SEARCH_JOG_REVOLUTIONS, Deck
from virtual_cdj.deck.mapping import InputMapper
from virtual_cdj.deck.mode_manager import OperatingMode
from virtual_cdj.deck.state import (
    AUTO_CUE_LEVELS,
    DEFAULT_AUTO_CUE_LEVEL,
    TEMPO_RANGES,
    AutoCueLevel,
    CueKind,
    Direction,
    HotCue,
    MemoryCue,
    PadMode,
    PlayState,
    WaveformData,
    WaveformSet,
    empty_state,
)
from virtual_cdj.jog import STEPS_PER_REV
from virtual_cdj.media_library.devices import (
    DeviceStatus,
    MediaDevice,
    UsbDeviceService,
)
from virtual_cdj.media_library.volumes import VolumeInfo, VolumeWatcher


# ----------------------------------------------------------------------
# Hilfsmittel
# ----------------------------------------------------------------------


def send(deck: Deck, command_type: CommandType, **params) -> None:
    """Ein Kommando am Deck ausfuehren - der Weg, den auch die Taster gehen."""
    deck.execute(command(command_type, deck.deck_id, "TEST", **params))


class FakeUsb:
    """``UsbDeviceService`` mit eingesetzter Laufwerksliste.

    Kein Test fasst ein echtes Laufwerk an: die Liste der Datentraeger ist
    eine gewoehnliche Liste. Die Sticks tragen keine Datenbank, deshalb
    laeuft auch kein Lesethread mit.
    """

    def __init__(self, volumes: list[VolumeInfo] | None = None) -> None:
        self.volumes: list[VolumeInfo] = list(volumes or [])
        self.service = UsbDeviceService(
            watcher=VolumeWatcher(
                rescan_interval_s=0.0,
                lister=lambda: tuple(self.volumes),
                clock=lambda: 0.0,
            )
        )
        self.detached: list[MediaDevice] = []
        self.service.on_detached.append(self.detached.append)

    def close(self) -> None:
        self.service.close()


class MediaTestCase(unittest.TestCase):
    """Basis fuer alles, was einen Stick im Dateisystem braucht."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def stick(self, name: str, *, serial: str, label: str) -> VolumeInfo:
        """Ein Wechseldatentraeger ohne rekordbox-Bibliothek.

        Ohne Datenbank bleibt der Zustand ``NO_LIBRARY`` - der Stick ist
        trotzdem eine Quelle (``is_media_source``), und genau darum geht es
        bei USB STOP. Es laeuft dabei kein Lesethread.
        """
        root = make_stick(self.tmp / name, database=None, analysis_dir=False)
        return volume(root, serial=serial, label=label)


# ----------------------------------------------------------------------
# Abschnitt 1: USB STOP
# ----------------------------------------------------------------------


class UsbStopServiceTests(MediaTestCase):
    """``UsbDeviceService.disconnect`` - die Quelle, nicht das Laufwerk."""

    def setUp(self) -> None:
        super().setUp()
        self.usb = FakeUsb([self.stick("stick1", serial="S1", label="EINS")])
        self.addCleanup(self.usb.close)
        self.usb.service.poll(force=True)
        self.device = self.usb.service.devices[0]

    def test_stick_is_a_source_before_stop(self) -> None:
        self.assertTrue(self.device.is_media_source)
        self.assertIs(self.device.status, DeviceStatus.NO_LIBRARY)

    def test_stop_removes_the_device(self) -> None:
        released = self.usb.service.disconnect(self.device.device_id)
        self.assertIsNotNone(released)
        self.assertEqual(self.usb.service.devices, ())
        self.assertIsNone(self.usb.service.device(self.device.device_id))

    def test_stop_reports_the_device_as_detached(self) -> None:
        self.usb.service.disconnect(self.device.device_id)
        self.assertEqual(
            [d.device_id for d in self.usb.detached], [self.device.device_id]
        )

    def test_released_device_stays_away_while_still_plugged_in(self) -> None:
        """Der Stick steckt noch - er darf trotzdem nicht zurueckkommen."""
        self.usb.service.disconnect(self.device.device_id)
        for _ in range(5):
            self.usb.service.poll(force=True)
        self.assertEqual(self.usb.service.devices, ())

    def test_refresh_does_not_bring_a_released_device_back(self) -> None:
        self.usb.service.disconnect(self.device.device_id)
        self.usb.service.refresh()
        self.assertEqual(self.usb.service.devices, ())

    def test_physically_pulling_and_replugging_starts_over(self) -> None:
        """Abziehen hebt die Freigabe auf - sonst waere der Stick tot."""
        self.usb.service.disconnect(self.device.device_id)
        stick = self.usb.volumes[0]
        self.usb.volumes.clear()
        self.usb.service.poll(force=True)
        self.usb.volumes.append(stick)
        self.usb.service.poll(force=True)
        self.assertEqual(len(self.usb.service.devices), 1)

    def test_unknown_device_is_not_an_error(self) -> None:
        self.assertIsNone(self.usb.service.disconnect("gibt-es-nicht"))

    def test_second_stop_on_the_same_device_is_harmless(self) -> None:
        self.usb.service.disconnect(self.device.device_id)
        self.assertIsNone(self.usb.service.disconnect(self.device.device_id))
        self.assertEqual(len(self.usb.detached), 1)

    def test_other_devices_are_untouched(self) -> None:
        self.usb.volumes.append(
            self.stick("stick2", serial="S2", label="ZWEI")
        )
        self.usb.service.poll(force=True)
        self.assertEqual(len(self.usb.service.devices), 2)
        self.usb.service.disconnect(self.device.device_id)
        remaining = self.usb.service.devices
        self.assertEqual([d.volume.label for d in remaining], ["ZWEI"])

    def test_no_operating_system_eject_anywhere(self) -> None:
        """USB STOP darf das Laufwerk nicht erzwungen aushaengen.

        Ein erzwungener Auswurf bei offenen Puffern ist der uebliche Weg,
        auf dem rekordbox-Sticks kaputtgehen. Der Dienst darf deshalb gar
        keine Moeglichkeit dazu haben.
        """
        source = (
            Path(__file__).resolve().parents[1]
            / "virtual_cdj" / "media_library" / "devices.py"
        ).read_text(encoding="utf-8")
        forbidden = (
            "subprocess", "os.system", "ctypes", "DeviceIoControl",
        )
        for name in forbidden:
            self.assertNotIn(name, source)


class UsbStopApplicationTests(MediaTestCase):
    """USB STOP im Anwendungsrahmen: Quelle verschwindet, Deck spielt weiter."""

    def setUp(self) -> None:
        super().setUp()
        self.app = CdjApplication(
            [1], start_audio=False, usb=False,
            operating_mode=OperatingMode.CDJ,
        )
        self.addCleanup(self.app.close)
        self.usb = FakeUsb([self.stick("stick1", serial="S1", label="EINS")])
        self.addCleanup(self.usb.close)
        self.app._enable_usb(self.usb.service)

    def _source_ids(self) -> list[str]:
        return [library.source_id for library in self.app.library.libraries]

    def test_source_is_present_before_stop(self) -> None:
        self.assertIn("USB:S1", self._source_ids())

    def test_stop_removes_the_source_from_the_library(self) -> None:
        released = self.app.stop_media(1)
        self.assertEqual(released, "S1")
        self.assertNotIn("USB:S1", self._source_ids())

    def test_command_never_reaches_the_deck(self) -> None:
        """USB STOP ist Sache der Anwendung, nicht des Decks."""
        seen: list[object] = []
        self.app.set_command_sink(1, seen.append)
        self.app.dispatch(command(CommandType.USB_STOP, 1, "TEST"))
        self.assertEqual(seen, [])
        self.assertNotIn("USB:S1", self._source_ids())

    def test_ambiguous_target_stops_nothing(self) -> None:
        """Zwei Sticks, kein geladener Track: lieber keinen als den falschen."""
        self.usb.volumes.append(
            self.stick("stick2", serial="S2", label="ZWEI")
        )
        self.usb.service.poll(force=True)
        self.assertEqual(self.app.stop_media(1), "")
        self.assertEqual(len(self.app.usb.devices), 2)

    def test_stop_without_any_device_is_harmless(self) -> None:
        self.app.stop_media(1)
        self.assertEqual(self.app.stop_media(1), "")

    def test_button_is_mapped_to_the_command(self) -> None:
        sent: list[object] = []
        mapper = InputMapper(1, sent.append)
        layer = InputLayer()
        mapper.handle_event(layer.press(ids.USB_STOP, Source.SCRIPT))
        self.assertEqual([cmd.type for cmd in sent], [CommandType.USB_STOP])


# ----------------------------------------------------------------------
# Abschnitt 2-4: MEMORY, CUE/LOOP CALL, DELETE
# ----------------------------------------------------------------------


class MemoryPointTests(unittest.TestCase):
    """MEMORY legt Punkte in ``track.memory_cues`` ab - nur im Speicher."""

    def setUp(self) -> None:
        self.clock = ClockedDeck()
        self.deck = self.clock.deck
        self.deck.load_track(make_track())

    def points(self) -> tuple[MemoryCue, ...]:
        return self.deck.state.track.memory_cues

    def test_memory_stores_the_cue_point(self) -> None:
        send(self.deck, CommandType.SEEK, position_s=20.0)
        send(self.deck, CommandType.CUE, pressed=True)
        send(self.deck, CommandType.CUE, pressed=False)
        send(self.deck, CommandType.MEMORY)
        self.assertEqual(len(self.points()), 1)
        self.assertIs(self.points()[0].kind, CueKind.CUE)
        self.assertAlmostEqual(self.points()[0].position_s, 20.0)

    def test_memory_stores_a_running_loop_with_both_edges(self) -> None:
        send(self.deck, CommandType.SEEK, position_s=10.0)
        send(self.deck, CommandType.BEAT_LOOP, beats=4.0)
        self.assertTrue(self.deck.state.loop.active)
        send(self.deck, CommandType.MEMORY)
        point = self.points()[-1]
        self.assertIs(point.kind, CueKind.LOOP)
        self.assertAlmostEqual(point.position_s, self.deck.state.loop.in_s)
        self.assertAlmostEqual(point.loop_end_s, self.deck.state.loop.out_s)

    def test_same_point_is_not_stored_twice(self) -> None:
        send(self.deck, CommandType.SEEK, position_s=20.0)
        send(self.deck, CommandType.CUE, pressed=True)
        send(self.deck, CommandType.CUE, pressed=False)
        send(self.deck, CommandType.MEMORY)
        send(self.deck, CommandType.MEMORY)
        self.assertEqual(len(self.points()), 1)

    def test_points_stay_sorted_by_position(self) -> None:
        for position in (40.0, 10.0, 25.0):
            send(self.deck, CommandType.SEEK, position_s=position)
            send(self.deck, CommandType.CUE, pressed=True)
            send(self.deck, CommandType.CUE, pressed=False)
            send(self.deck, CommandType.MEMORY)
        self.assertEqual(
            [round(p.position_s, 3) for p in self.points()],
            [10.0, 25.0, 40.0],
        )

    def test_memory_without_a_track_does_nothing(self) -> None:
        deck = Deck(2)
        deck.execute(command(CommandType.MEMORY, 2, "TEST"))
        self.assertIsNone(deck.state.track)


class CueLoopCallTests(unittest.TestCase):
    """CUE/LOOP CALL blaettert durch die gemerkten Punkte (S. 62)."""

    def setUp(self) -> None:
        self.clock = ClockedDeck()
        self.deck = self.clock.deck
        self.deck.load_track(
            make_track(
                memory_cues=(
                    MemoryCue(position_s=10.0, kind=CueKind.CUE),
                    MemoryCue(
                        position_s=30.0, kind=CueKind.LOOP, loop_end_s=32.0
                    ),
                    MemoryCue(position_s=50.0, kind=CueKind.CUE),
                )
            )
        )

    def call(self, direction: int) -> None:
        send(self.deck, CommandType.CUE_LOOP_CALL, direction=direction)

    def test_forward_goes_to_the_next_point(self) -> None:
        self.call(+1)
        self.assertAlmostEqual(self.deck.state.position_s, 10.0)
        self.call(+1)
        self.assertAlmostEqual(self.deck.state.position_s, 30.0)

    def test_backward_goes_to_the_previous_point(self) -> None:
        send(self.deck, CommandType.SEEK, position_s=55.0)
        self.call(-1)
        self.assertAlmostEqual(self.deck.state.position_s, 50.0)
        self.call(-1)
        self.assertAlmostEqual(self.deck.state.position_s, 30.0)

    def test_beyond_the_last_point_nothing_moves(self) -> None:
        send(self.deck, CommandType.SEEK, position_s=60.0)
        self.call(+1)
        self.assertAlmostEqual(self.deck.state.position_s, 60.0)

    def test_memory_loop_is_activated_on_recall(self) -> None:
        self.call(+1)
        self.call(+1)
        loop = self.deck.state.loop
        self.assertTrue(loop.active)
        self.assertAlmostEqual(loop.in_s, 30.0)
        self.assertAlmostEqual(loop.out_s, 32.0)

    def test_call_out_of_a_running_loop_really_leaves_it(self) -> None:
        """Ein Loop darf keine Sackgasse sein."""
        self.call(+1)
        self.call(+1)                     # Memory Loop 30-32 laeuft
        self.call(+1)                     # weiter auf 50
        self.assertAlmostEqual(self.deck.state.position_s, 50.0)
        self.assertFalse(self.deck.state.loop.active)

    def test_selection_index_follows_the_called_point(self) -> None:
        self.call(+1)
        self.assertEqual(self.deck.state.memory_index, 0)
        self.call(+1)
        self.assertEqual(self.deck.state.memory_index, 1)

    def test_without_stored_points_the_gap_is_recorded(self) -> None:
        deck = Deck(3)
        deck.load_track(make_track(track_id="t3"))
        deck.execute(command(CommandType.CUE_LOOP_CALL, 3, "TEST", direction=1))
        self.assertTrue(
            any("CUE_LOOP_CALL" in entry for entry in deck.unsupported)
        )

    def test_buttons_are_mapped_to_both_directions(self) -> None:
        sent: list[object] = []
        mapper = InputMapper(1, sent.append)
        layer = InputLayer()
        mapper.handle_event(layer.press(ids.CUE_LOOP_CALL_PREV, Source.SCRIPT))
        mapper.handle_event(layer.press(ids.CUE_LOOP_CALL_NEXT, Source.SCRIPT))
        self.assertEqual(
            [(cmd.type, cmd.get("direction")) for cmd in sent],
            [
                (CommandType.CUE_LOOP_CALL, -1),
                (CommandType.CUE_LOOP_CALL, +1),
            ],
        )


class DeleteMemoryTests(unittest.TestCase):
    """CALL/DELETE loescht den angewaehlten Punkt - nur im Speicher."""

    def setUp(self) -> None:
        self.clock = ClockedDeck()
        self.deck = self.clock.deck
        self.deck.load_track(
            make_track(
                memory_cues=(
                    MemoryCue(position_s=10.0, kind=CueKind.CUE),
                    MemoryCue(
                        position_s=30.0, kind=CueKind.LOOP, loop_end_s=32.0
                    ),
                ),
                hot_cues=(HotCue(index=0, position_s=5.0, color=""),),
            )
        )

    def positions(self) -> list[float]:
        return [
            round(p.position_s, 3) for p in self.deck.state.track.memory_cues
        ]

    def press_delete(self) -> None:
        send(self.deck, CommandType.DELETE, pressed=True)
        send(self.deck, CommandType.DELETE, pressed=False)

    def test_delete_removes_the_called_cue(self) -> None:
        send(self.deck, CommandType.CUE_LOOP_CALL, direction=+1)
        self.press_delete()
        self.assertEqual(self.positions(), [30.0])

    def test_delete_removes_the_called_loop(self) -> None:
        send(self.deck, CommandType.SEEK, position_s=40.0)
        send(self.deck, CommandType.CUE_LOOP_CALL, direction=-1)
        self.press_delete()
        self.assertEqual(self.positions(), [10.0])

    def test_selection_is_cleared_after_deleting(self) -> None:
        send(self.deck, CommandType.CUE_LOOP_CALL, direction=+1)
        self.press_delete()
        self.assertIsNone(self.deck.state.memory_index)

    def test_second_delete_does_not_eat_the_next_point(self) -> None:
        send(self.deck, CommandType.CUE_LOOP_CALL, direction=+1)
        self.press_delete()
        self.press_delete()
        self.assertEqual(self.positions(), [30.0])

    def test_delete_stays_the_hot_cue_modifier_without_a_selection(self) -> None:
        send(self.deck, CommandType.PAD_MODE, mode=PadMode.HOT_CUE)
        send(self.deck, CommandType.DELETE, pressed=True)
        send(self.deck, CommandType.PAD, index=0, pressed=True)
        send(self.deck, CommandType.DELETE, pressed=False)
        self.assertEqual(self.deck.state.track.hot_cues, ())
        self.assertEqual(self.positions(), [10.0, 30.0])

    def test_deleting_a_memory_point_does_not_touch_hot_cues(self) -> None:
        send(self.deck, CommandType.CUE_LOOP_CALL, direction=+1)
        self.press_delete()
        self.assertEqual(len(self.deck.state.track.hot_cues), 1)

    # -- Trennung der beiden Loeschfunktionen --------------------------

    def test_memory_delete_is_its_own_command(self) -> None:
        """Eigenes Kommando, unabhaengig vom Hotcue-Loeschen."""
        send(self.deck, CommandType.CUE_LOOP_CALL, direction=+1)
        send(self.deck, CommandType.MEMORY_DELETE)
        self.assertEqual(self.positions(), [30.0])

    def test_memory_delete_needs_a_selection(self) -> None:
        send(self.deck, CommandType.MEMORY_DELETE)
        self.assertEqual(self.positions(), [10.0, 30.0])
        self.assertTrue(
            any("MEMORY_DELETE" in entry for entry in self.deck.unsupported)
        )

    def test_memory_delete_never_touches_hot_cues(self) -> None:
        send(self.deck, CommandType.CUE_LOOP_CALL, direction=+1)
        send(self.deck, CommandType.MEMORY_DELETE)
        self.assertEqual(len(self.deck.state.track.hot_cues), 1)

    def test_memory_delete_does_not_open_the_hot_cue_call_mode(self) -> None:
        send(self.deck, CommandType.CUE_LOOP_CALL, direction=+1)
        send(self.deck, CommandType.MEMORY_DELETE, pressed=True)
        send(self.deck, CommandType.MEMORY_DELETE, pressed=False)
        self.assertFalse(self.deck.state.hot_cue_call_mode)

    def test_memory_delete_is_not_the_hot_cue_modifier(self) -> None:
        """Gehaltenes MEMORY_DELETE macht kein Pad zum Loeschknopf."""
        send(self.deck, CommandType.PAD_MODE, mode=PadMode.HOT_CUE)
        send(self.deck, CommandType.MEMORY_DELETE, pressed=True)
        send(self.deck, CommandType.PAD, index=0, pressed=True)
        self.assertEqual(len(self.deck.state.track.hot_cues), 1)

    def test_hot_cue_delete_never_touches_memory_points(self) -> None:
        send(self.deck, CommandType.PAD_MODE, mode=PadMode.HOT_CUE)
        send(self.deck, CommandType.DELETE, pressed=True)
        send(self.deck, CommandType.PAD, index=0, pressed=True)
        send(self.deck, CommandType.DELETE, pressed=False)
        self.assertEqual(self.deck.state.track.hot_cues, ())
        self.assertEqual(self.positions(), [10.0, 30.0])

    def test_shift_and_delete_is_the_unambiguous_memory_path(self) -> None:
        """Ohne Umweg ueber den Tastenverlauf."""
        sent: list[object] = []
        mapper = InputMapper(1, sent.append)
        layer = InputLayer()
        mapper.handle_event(layer.press(ids.SHIFT, Source.SCRIPT))
        mapper.handle_event(layer.press(ids.DELETE, Source.SCRIPT))
        self.assertEqual(
            [cmd.type for cmd in sent], [CommandType.MEMORY_DELETE]
        )

    def test_the_two_functions_are_separate_commands(self) -> None:
        """Der Kern des Audits: nichts bedeutet stillschweigend dasselbe."""
        self.assertIsNot(CommandType.DELETE, CommandType.MEMORY_DELETE)
        self.assertNotEqual(
            CommandType.DELETE.value, CommandType.MEMORY_DELETE.value
        )

    def test_both_functions_have_their_own_handler(self) -> None:
        from virtual_cdj.deck.engine import _HANDLERS

        self.assertIsNot(
            _HANDLERS[CommandType.DELETE],
            _HANDLERS[CommandType.MEMORY_DELETE],
        )

    def test_only_one_physical_button_carries_both(self) -> None:
        """Festgehalten, solange es so ist.

        Am Geraet gibt es fuer beide Funktionen nur diese eine Taste. Die
        Trennung liegt deshalb in Kommando und Handler, nicht in zwei
        Bedienelementen. Kommt ein zweiter Taster dazu, faellt dieser Test
        auf und die Zuordnung kann nachgezogen werden.
        """
        from virtual_cdj.deck.mapping import _MOMENTARY, _ON_PRESS

        delete_controls = [
            control_id
            for table in (_ON_PRESS, _MOMENTARY)
            for control_id, (command_type, _params) in table.items()
            if command_type
            in (CommandType.DELETE, CommandType.MEMORY_DELETE)
        ]
        self.assertEqual(delete_controls, [ids.DELETE])


# ----------------------------------------------------------------------
# Abschnitt 5: AUTO CUE
# ----------------------------------------------------------------------


def silent_then_loud(
    silence_s: float,
    *,
    duration_s: float = 20.0,
    peaks_per_second: float = 320.0,
) -> WaveformSet:
    """Waveform mit Anlaufstille und anschliessend vollem Pegel.

    So wie die Analyse sie liefert: ``peak``/``rms`` auf den lautesten
    Punkt des Tracks normiert, ein Eintrag je Zeitfenster.
    """
    windows = int(round(duration_s * peaks_per_second))
    quiet = int(round(silence_s * peaks_per_second))
    peak = tuple(0.0 if index < quiet else 1.0 for index in range(windows))
    band = tuple(value * 0.8 for value in peak)
    return WaveformSet(levels={
        "detailed": WaveformData(
            peaks_per_second=peaks_per_second,
            low=band, mid=band, high=band,
            peak=peak, rms=peak,
        )
    })


def ramp_waveform(peaks_per_second: float = 320.0) -> WaveformSet:
    """Langsam ansteigender Pegel - damit sich Schwellen unterscheiden."""
    ramp = tuple(index / 3200.0 for index in range(3200))
    return WaveformSet(levels={
        "detailed": WaveformData(
            peaks_per_second=peaks_per_second,
            low=ramp, mid=ramp, high=ramp,
            peak=ramp, rms=ramp,
        )
    })


class AutoCuePointTests(unittest.TestCase):
    """Die Einsatzerkennung selbst - ohne Deck, ohne Audiodatei."""

    def test_finds_the_start_after_silence(self) -> None:
        start = detect_audio_start(silent_then_loud(2.0), threshold_db_below_peak=-60.0)
        self.assertIsNotNone(start)
        # Die Stelle liegt unmittelbar davor, nie mitten im Einsatz.
        self.assertLessEqual(start, 2.0)
        self.assertGreater(start, 2.0 - 0.02)

    def test_without_silence_the_start_is_the_track_start(self) -> None:
        start = detect_audio_start(silent_then_loud(0.0), threshold_db_below_peak=-60.0)
        self.assertAlmostEqual(start, 0.0)

    def test_no_waveform_means_no_answer(self) -> None:
        """Lieber "weiss nicht" als eine erfundene Position."""
        self.assertIsNone(detect_audio_start(None, threshold_db_below_peak=-60.0))
        self.assertIsNone(detect_audio_start(WaveformSet(), threshold_db_below_peak=-60.0))

    def test_a_track_below_the_threshold_gives_no_answer(self) -> None:
        quiet = WaveformSet(levels={
            "detailed": WaveformData(
                peaks_per_second=320.0,
                low=(0.0,) * 100, mid=(0.0,) * 100, high=(0.0,) * 100,
                peak=(0.0,) * 100, rms=(0.0,) * 100,
            )
        })
        self.assertIsNone(detect_audio_start(quiet, threshold_db_below_peak=-60.0))

    def test_a_lower_threshold_reacts_earlier(self) -> None:
        """Die Schwellenreihe muss sich auch auswirken."""
        waveform = ramp_waveform()
        quiet_level = detect_audio_start(waveform, threshold_db_below_peak=-78.0)
        loud_level = detect_audio_start(waveform, threshold_db_below_peak=-36.0)
        self.assertLess(quiet_level, loud_level)

    def test_older_analyses_without_dynamics_still_work(self) -> None:
        """Nur die drei Baender - dann gilt das lauteste."""
        band = (0.0,) * 320 + (1.0,) * 320
        waveform = WaveformSet(levels={
            "detailed": WaveformData(
                peaks_per_second=320.0, low=band, mid=band, high=band,
            )
        })
        self.assertAlmostEqual(
            detect_audio_start(waveform, threshold_db_below_peak=-60.0), 1.0, places=2
        )

    def test_every_device_level_has_a_threshold_except_memory(self) -> None:
        for level in AUTO_CUE_LEVELS:
            if level is AutoCueLevel.MEMORY:
                self.assertIsNone(level.threshold_db_below_peak)
            else:
                self.assertLess(level.threshold_db_below_peak, 0.0)

    def test_the_level_series_matches_the_device(self) -> None:
        self.assertIs(AUTO_CUE_LEVELS[0], AutoCueLevel.MEMORY)
        self.assertEqual(
            [level.threshold_db_below_peak for level in AUTO_CUE_LEVELS[1:]],
            [-78.0, -72.0, -66.0, -60.0, -54.0, -48.0, -42.0, -36.0],
        )


class AutoCueTests(unittest.TestCase):
    """AUTO CUE: nach dem Laden am ersten Audioeinsatz warten (S. 44)."""

    def setUp(self) -> None:
        self.clock = ClockedDeck()
        self.deck = self.clock.deck

    def use_level(self, level: AutoCueLevel) -> None:
        """Pegelstufe waehlen - die Voreinstellung ist MEMORY."""
        self.deck._update(auto_cue_level=level)

    def test_off_by_default(self) -> None:
        self.assertFalse(self.deck.state.auto_cue)

    def test_default_level_is_memory(self) -> None:
        """Werkseinstellung des Geraets (Handbuch S. 78)."""
        self.assertIs(self.deck.state.auto_cue_level, AutoCueLevel.MEMORY)
        self.assertIsNone(
            self.deck.state.auto_cue_level.threshold_db_below_peak
        )

    def test_the_default_comes_from_one_constant(self) -> None:
        self.assertIs(DEFAULT_AUTO_CUE_LEVEL, AutoCueLevel.MEMORY)
        self.assertIs(empty_state(1).auto_cue_level, DEFAULT_AUTO_CUE_LEVEL)

    def test_toggles(self) -> None:
        send(self.deck, CommandType.AUTO_CUE)
        self.assertTrue(self.deck.state.auto_cue)
        send(self.deck, CommandType.AUTO_CUE)
        self.assertFalse(self.deck.state.auto_cue)

    def test_release_does_not_toggle_back(self) -> None:
        send(self.deck, CommandType.AUTO_CUE, pressed=True)
        send(self.deck, CommandType.AUTO_CUE, pressed=False)
        self.assertTrue(self.deck.state.auto_cue)

    def test_load_stops_before_the_first_sound(self) -> None:
        self.use_level(AutoCueLevel.MINUS_60)
        send(self.deck, CommandType.AUTO_CUE)
        self.deck.load_track(make_track(waveform=silent_then_loud(2.0)))
        state = self.deck.state
        self.assertAlmostEqual(state.position_s, 2.0, places=2)
        self.assertAlmostEqual(state.cue_point_s, state.position_s)
        self.assertIs(state.play_state, PlayState.STOPPED)

    def test_a_track_without_silence_starts_at_zero(self) -> None:
        self.use_level(AutoCueLevel.MINUS_60)
        send(self.deck, CommandType.AUTO_CUE)
        self.deck.load_track(make_track(waveform=silent_then_loud(0.0)))
        self.assertAlmostEqual(self.deck.state.position_s, 0.0)

    def test_hot_cues_are_not_a_substitute(self) -> None:
        """Ein Hotcue ist gesetzt worden - er sagt nichts ueber den Einsatz."""
        self.use_level(AutoCueLevel.MINUS_60)
        send(self.deck, CommandType.AUTO_CUE)
        self.deck.load_track(
            make_track(hot_cues=(HotCue(index=1, position_s=8.0, color=""),))
        )
        self.assertAlmostEqual(self.deck.state.position_s, 0.0)

    def test_memory_cues_are_not_used_at_a_level_threshold(self) -> None:
        self.use_level(AutoCueLevel.MINUS_60)
        send(self.deck, CommandType.AUTO_CUE)
        self.deck.load_track(
            make_track(
                memory_cues=(MemoryCue(position_s=12.5, kind=CueKind.CUE),),
                waveform=silent_then_loud(2.0),
            )
        )
        self.assertAlmostEqual(self.deck.state.position_s, 2.0, places=2)

    def test_without_analysis_the_track_start_stays(self) -> None:
        """Keine Stelle raten, wenn es keine Waveform gibt."""
        self.use_level(AutoCueLevel.MINUS_60)
        send(self.deck, CommandType.AUTO_CUE)
        self.deck.load_track(make_track())
        self.assertAlmostEqual(self.deck.state.position_s, 0.0)

    def test_off_means_the_track_starts_at_zero(self) -> None:
        self.use_level(AutoCueLevel.MINUS_60)
        self.deck.load_track(make_track(waveform=silent_then_loud(2.0)))
        self.assertAlmostEqual(self.deck.state.position_s, 0.0)

    def test_setting_survives_the_track_change(self) -> None:
        send(self.deck, CommandType.AUTO_CUE)
        self.deck.load_track(make_track())
        self.assertTrue(self.deck.state.auto_cue)


class AutoCueMemoryLevelTests(unittest.TestCase):
    """Nur die Stufe MEMORY darf gespeicherte Punkte benutzen."""

    def setUp(self) -> None:
        self.clock = ClockedDeck()
        self.deck = self.clock.deck
        self.deck._update(auto_cue=True, auto_cue_level=AutoCueLevel.MEMORY)

    def test_memory_level_uses_the_earliest_memory_cue(self) -> None:
        self.deck.load_track(
            make_track(
                memory_cues=(
                    MemoryCue(position_s=40.0, kind=CueKind.CUE),
                    MemoryCue(position_s=12.5, kind=CueKind.CUE),
                ),
                waveform=silent_then_loud(2.0),
            )
        )
        self.assertAlmostEqual(self.deck.state.position_s, 12.5)

    def test_memory_level_ignores_hot_cues(self) -> None:
        self.deck.load_track(
            make_track(hot_cues=(HotCue(index=0, position_s=8.0, color=""),))
        )
        self.assertAlmostEqual(self.deck.state.position_s, 0.0)

    def test_memory_level_without_points_stays_at_the_start(self) -> None:
        self.deck.load_track(make_track(waveform=silent_then_loud(2.0)))
        self.assertAlmostEqual(self.deck.state.position_s, 0.0)


# ----------------------------------------------------------------------
# Abschnitt 5b: TIME MODE kurz / AUTO CUE lang
# ----------------------------------------------------------------------


class TimeModeButtonTests(unittest.TestCase):
    """Eine Taste, zwei Haltedauern - entschieden in der Zuordnungsschicht.

    Die Zeitstempel werden eingesetzt, nicht abgewartet: die Tests sind
    damit von der Rechnergeschwindigkeit unabhaengig.
    """

    def setUp(self) -> None:
        self.sent: list[object] = []
        self.mapper = InputMapper(1, self.sent.append)
        self.layer = InputLayer()

    def hold(self, seconds: float) -> None:
        self.mapper.handle_event(
            self.layer.press(ids.TIME_MODE, Source.SCRIPT, timestamp=1000.0)
        )
        self.mapper.handle_event(
            self.layer.release(
                ids.TIME_MODE, Source.SCRIPT,
                timestamp=1000.0 + seconds * 1000.0,
            )
        )

    def types(self) -> list[CommandType]:
        return [cmd.type for cmd in self.sent]

    def test_the_button_exists_as_a_control(self) -> None:
        self.assertIn(ids.TIME_MODE, controls.INPUT_CONTROLS)

    def test_short_press_switches_the_time_display(self) -> None:
        self.hold(0.1)
        self.assertEqual(self.types(), [CommandType.TIME_MODE])

    def test_long_press_switches_auto_cue(self) -> None:
        self.hold(LONG_PRESS_S + 0.1)
        self.assertEqual(self.types(), [CommandType.AUTO_CUE])

    def test_a_long_press_never_also_sends_a_short_press(self) -> None:
        """Genau ein Kommando je Druck - das ist der Kern."""
        self.hold(2.0)
        self.assertEqual(self.types(), [CommandType.AUTO_CUE])
        self.assertNotIn(CommandType.TIME_MODE, self.types())

    def test_pressing_alone_sends_nothing_yet(self) -> None:
        self.mapper.handle_event(
            self.layer.press(ids.TIME_MODE, Source.SCRIPT, timestamp=1000.0)
        )
        self.assertEqual(self.sent, [])

    def test_a_pending_press_is_not_recorded_as_unhandled(self) -> None:
        self.mapper.handle_event(
            self.layer.press(ids.TIME_MODE, Source.SCRIPT, timestamp=1000.0)
        )
        self.assertNotIn(ids.TIME_MODE, self.mapper.unhandled)

    def test_exactly_at_the_threshold_counts_as_long(self) -> None:
        self.hold(LONG_PRESS_S)
        self.assertEqual(self.types(), [CommandType.AUTO_CUE])

    def test_a_page_change_drops_the_pending_press(self) -> None:
        """Verschluckte Release-Flanke darf keinen Dauerdruck hinterlassen."""
        self.mapper.handle_event(
            self.layer.press(ids.TIME_MODE, Source.SCRIPT, timestamp=1000.0)
        )
        self.mapper.reset_modifiers()
        self.mapper.handle_event(
            self.layer.release(
                ids.TIME_MODE, Source.SCRIPT, timestamp=9000.0
            )
        )
        self.assertEqual(self.sent, [])

    def test_the_long_press_reaches_the_deck(self) -> None:
        """AUTO_CUE ist kein Anzeigebefehl - es muss am Deck ankommen."""
        from virtual_cdj.cdj_ui.screen import DISPLAY_COMMANDS

        self.assertNotIn(CommandType.AUTO_CUE, DISPLAY_COMMANDS)
        self.assertIn(CommandType.TIME_MODE, DISPLAY_COMMANDS)

    def test_there_is_only_one_long_press_threshold(self) -> None:
        """Keine zweite Timerlogik - der Bildschirm nutzt dieselbe Zahl."""
        from virtual_cdj.cdj_ui import screen

        self.assertIs(screen.LONG_PRESS_S, LONG_PRESS_S)


# ----------------------------------------------------------------------
# Abschnitt 6: manueller Hotcue-Aufrufmodus
# ----------------------------------------------------------------------


class HotCueCallModeTests(unittest.TestCase):
    """CALL/DELETE kurz gedrueckt: der naechste Paddruck ruft auf.

    Kein SHIFT. Am Geraet ist es dieselbe Taste, die auch als
    Loesch-Modifikator dient; unterschieden wird am Verlauf des Drucks.
    """

    def setUp(self) -> None:
        self.clock = ClockedDeck()
        self.deck = self.clock.deck
        self.deck.load_track(
            make_track(hot_cues=(HotCue(index=2, position_s=18.0, color=""),))
        )
        send(self.deck, CommandType.PAD_MODE, mode=PadMode.HOT_CUE)

    def tap_call_delete(self) -> None:
        """CALL/DELETE kurz druecken und wieder loslassen."""
        send(self.deck, CommandType.DELETE, pressed=True)
        send(self.deck, CommandType.DELETE, pressed=False)

    def test_off_by_default(self) -> None:
        self.assertFalse(self.deck.state.hot_cue_call_mode)

    def test_call_delete_activates_the_mode(self) -> None:
        self.tap_call_delete()
        self.assertTrue(self.deck.state.hot_cue_call_mode)

    def test_pressing_alone_does_not_activate_it_yet(self) -> None:
        """Erst beim Loslassen ist klar, dass kein Pad mehr kommt."""
        send(self.deck, CommandType.DELETE, pressed=True)
        self.assertFalse(self.deck.state.hot_cue_call_mode)

    def test_call_delete_again_cancels_the_mode(self) -> None:
        self.tap_call_delete()
        self.tap_call_delete()
        self.assertFalse(self.deck.state.hot_cue_call_mode)

    def test_filled_pad_is_called(self) -> None:
        self.tap_call_delete()
        send(self.deck, CommandType.PAD, index=2, pressed=True)
        self.assertAlmostEqual(self.deck.state.position_s, 18.0)

    def test_mode_ends_after_one_pad(self) -> None:
        self.tap_call_delete()
        send(self.deck, CommandType.PAD, index=2, pressed=True)
        self.assertFalse(self.deck.state.hot_cue_call_mode)

    def test_empty_pad_sets_nothing(self) -> None:
        """Im Aufrufmodus wird nur aufgerufen, nie versehentlich gesetzt."""
        self.tap_call_delete()
        send(self.deck, CommandType.SEEK, position_s=44.0)
        send(self.deck, CommandType.PAD, index=5, pressed=True)
        self.assertEqual(
            [cue.index for cue in self.deck.state.track.hot_cues], [2]
        )
        self.assertFalse(self.deck.state.hot_cue_call_mode)

    def test_empty_pad_does_not_move_the_playback(self) -> None:
        self.tap_call_delete()
        send(self.deck, CommandType.SEEK, position_s=44.0)
        send(self.deck, CommandType.PAD, index=5, pressed=True)
        self.assertAlmostEqual(self.deck.state.position_s, 44.0)

    def test_normal_mode_still_sets_an_empty_pad(self) -> None:
        send(self.deck, CommandType.SEEK, position_s=44.0)
        send(self.deck, CommandType.PAD, index=5, pressed=True)
        self.assertEqual(
            sorted(cue.index for cue in self.deck.state.track.hot_cues), [2, 5]
        )

    def test_holding_and_pressing_a_pad_still_deletes(self) -> None:
        """Der Loesch-Modifikator bleibt unveraendert erreichbar."""
        send(self.deck, CommandType.DELETE, pressed=True)
        send(self.deck, CommandType.PAD, index=2, pressed=True)
        send(self.deck, CommandType.DELETE, pressed=False)
        self.assertEqual(self.deck.state.track.hot_cues, ())

    def test_deleting_a_hot_cue_does_not_also_open_the_call_mode(self) -> None:
        """Ein Druck, eine Bedeutung."""
        send(self.deck, CommandType.DELETE, pressed=True)
        send(self.deck, CommandType.PAD, index=2, pressed=True)
        send(self.deck, CommandType.DELETE, pressed=False)
        self.assertFalse(self.deck.state.hot_cue_call_mode)

    def test_an_empty_pad_while_held_still_counts_as_deleting(self) -> None:
        send(self.deck, CommandType.DELETE, pressed=True)
        send(self.deck, CommandType.PAD, index=7, pressed=True)
        send(self.deck, CommandType.DELETE, pressed=False)
        self.assertFalse(self.deck.state.hot_cue_call_mode)
        self.assertEqual(
            [cue.index for cue in self.deck.state.track.hot_cues], [2]
        )

    def test_deleting_a_memory_point_does_not_open_the_call_mode(self) -> None:
        deck = ClockedDeck(4).deck
        deck.load_track(
            make_track(
                track_id="t4",
                memory_cues=(MemoryCue(position_s=10.0, kind=CueKind.CUE),),
            )
        )
        deck.execute(
            command(CommandType.CUE_LOOP_CALL, 4, "TEST", direction=+1)
        )
        deck.execute(command(CommandType.DELETE, 4, "TEST", pressed=True))
        deck.execute(command(CommandType.DELETE, 4, "TEST", pressed=False))
        self.assertEqual(deck.state.track.memory_cues, ())
        self.assertFalse(deck.state.hot_cue_call_mode)

    def test_track_change_ends_the_mode(self) -> None:
        self.tap_call_delete()
        self.deck.load_track(make_track(track_id="t9"))
        self.assertFalse(self.deck.state.hot_cue_call_mode)

    def test_the_button_sends_plain_delete_without_shift(self) -> None:
        sent: list[object] = []
        mapper = InputMapper(1, sent.append)
        layer = InputLayer()
        mapper.handle_event(layer.press(ids.DELETE, Source.SCRIPT))
        mapper.handle_event(layer.release(ids.DELETE, Source.SCRIPT))
        self.assertEqual(
            [(cmd.type, cmd.pressed) for cmd in sent],
            [(CommandType.DELETE, True), (CommandType.DELETE, False)],
        )

    def test_shift_sends_the_memory_delete_command_instead(self) -> None:
        """Der Aufrufmodus haengt nicht mehr an SHIFT."""
        sent: list[object] = []
        mapper = InputMapper(1, sent.append)
        layer = InputLayer()
        mapper.handle_event(layer.press(ids.SHIFT, Source.SCRIPT))
        mapper.handle_event(layer.press(ids.DELETE, Source.SCRIPT))
        self.assertEqual(
            [cmd.type for cmd in sent], [CommandType.MEMORY_DELETE]
        )
        self.assertNotIn(CommandType.HOT_CUE_CALL_MODE,
                         [cmd.type for cmd in sent])

    def test_beat_jump_as_modifier_does_not_open_the_call_mode(self) -> None:
        """CALL/DELETE + BEAT JUMP stellt die Sprungweite - kein Modus."""
        before = self.deck.state.beat_jump_beats
        send(self.deck, CommandType.DELETE, pressed=True)
        send(self.deck, CommandType.BEAT_JUMP, direction=+1)
        send(self.deck, CommandType.DELETE, pressed=False)
        self.assertNotEqual(self.deck.state.beat_jump_beats, before)
        self.assertFalse(self.deck.state.hot_cue_call_mode)

    def test_a_pad_in_another_mode_also_counts_as_modifier_use(self) -> None:
        send(self.deck, CommandType.PAD_MODE, mode=PadMode.BEAT_LOOP)
        send(self.deck, CommandType.DELETE, pressed=True)
        send(self.deck, CommandType.PAD, index=4, pressed=True)
        send(self.deck, CommandType.DELETE, pressed=False)
        self.assertFalse(self.deck.state.hot_cue_call_mode)

    def test_the_command_can_still_be_sent_directly(self) -> None:
        """Fuer Skripte und Tests bleibt der direkte Weg offen."""
        send(self.deck, CommandType.HOT_CUE_CALL_MODE)
        self.assertTrue(self.deck.state.hot_cue_call_mode)


# ----------------------------------------------------------------------
# Abschnitt 7: TRACK SEARCH gehalten + Jogwheel
# ----------------------------------------------------------------------


class TrackSearchJogTests(unittest.TestCase):
    """Gehaltenes TRACK SEARCH macht das Jogwheel zum Listenblaetterer."""

    def setUp(self) -> None:
        self.clock = ClockedDeck()
        self.deck = self.clock.deck
        self.deck.load_track(make_track())

    def hold(self, direction: int, pressed: bool = True) -> None:
        send(
            self.deck,
            CommandType.TRACK_SEARCH,
            direction=direction,
            pressed=pressed,
        )

    def turn(self, revolutions: float) -> None:
        send(
            self.deck,
            CommandType.JOG_MOVE,
            delta=int(round(revolutions * STEPS_PER_REV)),
            ticks_per_rev=STEPS_PER_REV,
        )

    def test_button_reports_press_and_release(self) -> None:
        sent: list[object] = []
        mapper = InputMapper(1, sent.append)
        layer = InputLayer()
        mapper.handle_event(layer.press(ids.TRACK_SEARCH_NEXT, Source.SCRIPT))
        mapper.handle_event(layer.release(ids.TRACK_SEARCH_NEXT, Source.SCRIPT))
        self.assertEqual(
            [(cmd.type, cmd.pressed) for cmd in sent],
            [
                (CommandType.TRACK_SEARCH, True),
                (CommandType.TRACK_SEARCH, False),
            ],
        )

    def test_single_press_still_jumps_one_track(self) -> None:
        self.hold(+1)
        self.hold(+1, pressed=False)
        self.assertEqual(self.deck.take_track_requests(), [1])

    def test_holding_and_turning_forward_pages_through_the_list(self) -> None:
        self.hold(+1)
        self.deck.take_track_requests()
        self.turn(1.0)
        expected = int(1.0 / TRACK_SEARCH_JOG_REVOLUTIONS)
        self.assertEqual(self.deck.take_track_requests(), [1] * expected)

    def test_turning_backwards_pages_backwards(self) -> None:
        self.hold(-1)
        self.deck.take_track_requests()
        self.turn(-0.5)
        expected = int(0.5 / TRACK_SEARCH_JOG_REVOLUTIONS)
        self.assertEqual(self.deck.take_track_requests(), [-1] * expected)

    def test_the_turn_direction_decides_not_the_button(self) -> None:
        """Gehalten wird die Taste; geblaettert wird in Drehrichtung."""
        self.hold(+1)
        self.deck.take_track_requests()
        self.turn(-TRACK_SEARCH_JOG_REVOLUTIONS)
        self.assertEqual(self.deck.take_track_requests(), [-1])

    def test_small_turns_accumulate_instead_of_being_lost(self) -> None:
        self.hold(+1)
        self.deck.take_track_requests()
        steps = int(TRACK_SEARCH_JOG_REVOLUTIONS / 0.05)
        for _ in range(steps - 1):
            self.turn(0.05)
        self.assertEqual(self.deck.take_track_requests(), [])
        self.turn(0.05)
        self.assertEqual(self.deck.take_track_requests(), [1])

    def test_position_does_not_move_while_paging(self) -> None:
        send(self.deck, CommandType.SEEK, position_s=30.0)
        self.hold(+1)
        self.deck.take_track_requests()
        self.turn(1.0)
        self.assertAlmostEqual(self.deck.state.position_s, 30.0)

    def test_after_release_the_jog_is_a_jog_again(self) -> None:
        self.hold(+1)
        self.hold(+1, pressed=False)
        self.deck.take_track_requests()
        send(self.deck, CommandType.SEEK, position_s=30.0)
        self.turn(1.0)
        self.assertEqual(self.deck.take_track_requests(), [])
        self.assertNotAlmostEqual(self.deck.state.position_s, 30.0)


# ----------------------------------------------------------------------
# Abschnitt 8: Richtungsschalter
# ----------------------------------------------------------------------


class DirectionSwitchTests(unittest.TestCase):
    """FWD / REV / SLIP REV sind ein Zustand, kein Ereignis."""

    def setUp(self) -> None:
        self.clock = ClockedDeck()
        self.deck = self.clock.deck
        self.deck.load_track(make_track())
        send(self.deck, CommandType.PLAY_PAUSE)

    def test_starts_forward(self) -> None:
        self.assertIs(self.deck.state.direction, Direction.FWD)

    def test_reverse_plays_backwards(self) -> None:
        send(self.deck, CommandType.SEEK, position_s=30.0)
        send(self.deck, CommandType.DIRECTION, position=Direction.REV)
        self.assertIs(self.deck.state.direction, Direction.REV)
        self.clock.advance(1.0)
        self.assertLess(self.deck.state.position_s, 30.0)

    def test_forward_again_plays_forwards(self) -> None:
        send(self.deck, CommandType.SEEK, position_s=30.0)
        send(self.deck, CommandType.DIRECTION, position=Direction.REV)
        self.clock.advance(1.0)
        send(self.deck, CommandType.DIRECTION, position=Direction.FWD)
        before = self.deck.state.position_s
        self.clock.advance(1.0)
        self.assertGreater(self.deck.state.position_s, before)

    def test_slip_reverse_is_its_own_position(self) -> None:
        send(self.deck, CommandType.DIRECTION, position=Direction.SLIP_REV)
        self.assertIs(self.deck.state.direction, Direction.SLIP_REV)

    def test_slip_reverse_keeps_a_background_timeline(self) -> None:
        """Zurueck auf FWD springt dorthin, wo der Track ohne REV waere."""
        send(self.deck, CommandType.SEEK, position_s=30.0)
        send(self.deck, CommandType.DIRECTION, position=Direction.SLIP_REV)
        self.clock.advance(2.0)
        send(self.deck, CommandType.DIRECTION, position=Direction.FWD)
        self.assertGreater(self.deck.state.position_s, 30.0)

    def test_same_position_twice_changes_nothing(self) -> None:
        send(self.deck, CommandType.DIRECTION, position=Direction.REV)
        state = self.deck.state
        send(self.deck, CommandType.DIRECTION, position=Direction.REV)
        self.assertIs(self.deck.state.direction, state.direction)

    def test_switch_is_mapped_as_a_position(self) -> None:
        sent: list[object] = []
        mapper = InputMapper(1, sent.append)
        layer = InputLayer()
        mapper.handle_event(
            layer.set_switch(ids.DIRECTION, Direction.REV.value, Source.SCRIPT)
        )
        self.assertEqual(
            [(cmd.type, cmd.get("position")) for cmd in sent],
            [(CommandType.DIRECTION, Direction.REV)],
        )


# ----------------------------------------------------------------------
# Abschnitt 9: WIDE -100 %
# ----------------------------------------------------------------------


class WideTempoTests(unittest.TestCase):
    """WIDE bei ganz unten heisst Stillstand, nicht "sehr langsam"."""

    def setUp(self) -> None:
        self.clock = ClockedDeck()
        self.deck = self.clock.deck
        self.deck.load_track(make_track())
        # Bereichsreihe 6 -> 10 -> 16 -> WIDE. Wo sie beginnt, ist eine
        # Voreinstellung - deshalb bis WIDE weiterschalten, nicht zaehlen.
        for _ in range(len(TEMPO_RANGES)):
            if self.deck.state.tempo_range is None:
                break
            send(self.deck, CommandType.TEMPO_RANGE_CYCLE)
        self.assertIsNone(self.deck.state.tempo_range)

    def test_fader_at_the_bottom_is_minus_hundred_percent(self) -> None:
        send(self.deck, CommandType.TEMPO_SET, value=0.0)
        self.assertAlmostEqual(self.deck.state.tempo_percent, -100.0)

    def test_bpm_becomes_zero(self) -> None:
        send(self.deck, CommandType.TEMPO_SET, value=0.0)
        self.assertAlmostEqual(self.deck.state.current_bpm, 0.0)

    def test_playback_really_stands_still(self) -> None:
        send(self.deck, CommandType.SEEK, position_s=30.0)
        send(self.deck, CommandType.PLAY_PAUSE)
        send(self.deck, CommandType.TEMPO_SET, value=0.0)
        self.clock.advance(2.0)
        self.assertAlmostEqual(self.deck.state.position_s, 30.0, places=6)

    def test_leaving_the_bottom_resumes(self) -> None:
        send(self.deck, CommandType.SEEK, position_s=30.0)
        send(self.deck, CommandType.PLAY_PAUSE)
        send(self.deck, CommandType.TEMPO_SET, value=0.0)
        self.clock.advance(2.0)
        send(self.deck, CommandType.TEMPO_SET, value=0.5)
        self.clock.advance(1.0)
        self.assertGreater(self.deck.state.position_s, 30.0)

    def test_the_deck_is_still_playing_not_paused(self) -> None:
        """Stillstand ist kein Pausenzustand - PLAY bleibt PLAY."""
        send(self.deck, CommandType.PLAY_PAUSE)
        send(self.deck, CommandType.TEMPO_SET, value=0.0)
        self.clock.advance(1.0)
        self.assertIs(self.deck.state.play_state, PlayState.PLAYING)


# ----------------------------------------------------------------------
# Abschnitt 10: VINYL SPEED ADJUST
# ----------------------------------------------------------------------


class VinylSpeedAdjustTests(unittest.TestCase):
    """Der Regler fuehrt einen Zustand - die Huellkurve fehlt noch.

    Bewusst so festgehalten: der Wert wird gespeichert und ist ablesbar,
    aber er beschleunigt und bremst noch nichts. Ein nachgebauter Effekt
    ohne die zugehoerige Start-/Stop-Huellkurve waere eine Erfindung.
    """

    def setUp(self) -> None:
        self.clock = ClockedDeck()
        self.deck = self.clock.deck
        self.deck.load_track(make_track())

    def test_value_is_stored(self) -> None:
        send(self.deck, CommandType.VINYL_SPEED_ADJUST, value=0.8)
        self.assertAlmostEqual(self.deck.state.vinyl_speed_adjust, 0.8)

    def test_value_is_mapped_from_the_control(self) -> None:
        sent: list[object] = []
        mapper = InputMapper(1, sent.append)
        layer = InputLayer()
        mapper.handle_event(
            layer.set_analog(ids.VINYL_SPEED_ADJUST, 0.25, Source.SCRIPT)
        )
        self.assertEqual(
            [(cmd.type, cmd.get("value")) for cmd in sent],
            [(CommandType.VINYL_SPEED_ADJUST, 0.25)],
        )

    def test_it_does_not_secretly_change_the_playback_speed(self) -> None:
        send(self.deck, CommandType.SEEK, position_s=10.0)
        send(self.deck, CommandType.PLAY_PAUSE)
        send(self.deck, CommandType.VINYL_SPEED_ADJUST, value=0.0)
        self.clock.advance(1.0)
        fast = self.deck.state.position_s

        other = ClockedDeck(2)
        other.deck.load_track(make_track())
        send(other.deck, CommandType.SEEK, position_s=10.0)
        send(other.deck, CommandType.PLAY_PAUSE)
        send(other.deck, CommandType.VINYL_SPEED_ADJUST, value=1.0)
        other.advance(1.0)
        self.assertAlmostEqual(fast, other.deck.state.position_s, places=6)


if __name__ == "__main__":
    unittest.main()
