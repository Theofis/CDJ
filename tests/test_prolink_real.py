"""Protocol-faithful replay tests for the passive real PRO DJ LINK provider."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from virtual_cdj.app import CdjApplication
from virtual_cdj.deck.mode_manager import OperatingMode
from virtual_cdj.prolink import RealProLinkProvider
from virtual_cdj.prolink.packets import (
    ANNOUNCEMENT_PORT,
    BEAT_PORT,
    STATUS_PORT,
    CdjStatusPacket,
    PrecisePositionPacket,
    parse_datagram,
)


class Clock:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        return self.value

    def advance(self, seconds: float) -> int:
        self.value += int(seconds * 1_000_000_000)
        return self.value


# Real CDJ-2000nexus keep-alive captured in usr-ein/prolink's hardware corpus
# (captures/S24b-e9-control). Only the documented device-number byte is changed
# by announcement() when a test needs the same physical fixture under ID 1.
REAL_KEEP_ALIVE = bytes(
    [
        0x51, 0x73, 0x70, 0x74, 0x31, 0x57, 0x6D, 0x4A, 0x4F, 0x4C,
        0x06, 0x00, 0x43, 0x44, 0x4A, 0x2D, 0x32, 0x30, 0x30, 0x30,
        0x6E, 0x65, 0x78, 0x75, 0x73, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x01, 0x02, 0x00, 0x36, 0x05, 0x02, 0xA0, 0xCE,
        0xC8, 0xE2, 0x26, 0xDE, 0xA9, 0xFE, 0x63, 0x64, 0x01, 0x00,
        0x00, 0x00, 0x01, 0x00,
    ]
)


# First real beat from usr-ein/prolink capture S06-load-and-play: player 1,
# 132.01 BPM, -3.05%, downbeat. It also appears as a pinned byte fixture in
# that project's 1110-packet corpus test.
REAL_BEAT = bytes(
    [
        0x51, 0x73, 0x70, 0x74, 0x31, 0x57, 0x6D, 0x4A, 0x4F, 0x4C,
        0x28, 0x43, 0x44, 0x4A, 0x2D, 0x32, 0x30, 0x30, 0x30, 0x6E,
        0x65, 0x78, 0x75, 0x73, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x01, 0x00, 0x01, 0x00, 0x3C, 0x00, 0x00, 0x01, 0xC6,
        0x00, 0x00, 0x03, 0x8D, 0x00, 0x00, 0x07, 0x1A, 0x00, 0x00,
        0x07, 0x1A, 0x00, 0x00, 0x0E, 0x34, 0x00, 0x00, 0x0E, 0x34,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x0F, 0x83, 0x12, 0x00, 0x00,
        0x33, 0x91, 0x01, 0x00, 0x00, 0x01,
    ]
)


# Captured CDJ-2000NXS/1.44 status skeleton from usr-ein/prolink. Identifying
# fields were zeroed by that project; status_packet() changes only offsets that
# its 46,012-packet corpus and Deep Symmetry independently document.
CAPTURED_STATUS_SKELETON = bytes.fromhex(
    """
    5173707431576d4a4f4c0a000000000000000000000000000000000000000001040000f8
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000100060400000000000000000100000000000000
    00000000312e343400000000000000030084fffe000f83127fffffff7fffffff000000000000
    00ff0000000401ff0000000000000000000000000000000000000000000100000000000000
    0000000000000f831200000000000000000f01000012345678000000010101010102010000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    0000001500000753000005b4
    """
)


def announcement(player_id: int = 1) -> bytes:
    packet = bytearray(REAL_KEEP_ALIVE)
    packet[0x24] = player_id
    return bytes(packet)


def status_packet(
    *, player_id: int = 1, bpm_centi: int = 12_800, master: bool = True
) -> bytes:
    packet = bytearray(CAPTURED_STATUS_SKELETON)
    name = b"CDJ-2000nexus"
    packet[0x0B:0x1F] = name.ljust(20, b"\0")
    packet[0x21] = player_id
    packet[0x24] = player_id
    packet[0x28] = player_id  # own USB
    packet[0x29] = 3
    packet[0x2A] = 1
    packet[0x2C:0x30] = (42).to_bytes(4, "big")
    packet[0x7B] = 3
    packet[0x89] = 0xD4 | (0x20 if master else 0)
    packet[0x8B] = 0xFA
    packet[0x8C:0x90] = (0x0010_0000).to_bytes(4, "big")
    packet[0x90:0x92] = (0x8000).to_bytes(2, "big")
    packet[0x92:0x94] = bpm_centi.to_bytes(2, "big")
    packet[0x9D] = 0x09
    packet[0x9E] = 1 if master else 0
    packet[0x9F] = 0xFF
    packet[0xA0:0xA4] = (67).to_bytes(4, "big")
    packet[0xA6] = 3
    return bytes(packet)


def precise_position_packet() -> bytes:
    # Synthetic at offsets published by beat-link PrecisePosition.java and
    # mirrored by prodjlink-rs. No unknown byte is asserted by the test.
    packet = bytearray(0x3C)
    packet[:10] = b"Qspt1WmJOL"
    packet[0x0A] = 0x0B
    packet[0x0B:0x1F] = b"CDJ-3000".ljust(20, b"\0")
    packet[0x1F] = 1
    packet[0x21] = 1
    packet[0x22:0x24] = (0x18).to_bytes(2, "big")
    packet[0x24:0x28] = (300).to_bytes(4, "big")
    packet[0x28:0x2C] = (45_000).to_bytes(4, "big")
    packet[0x2C:0x30] = (600).to_bytes(4, "big", signed=True)
    packet[0x38:0x3C] = (1362).to_bytes(4, "big")
    return bytes(packet)


class TestPacketDecoders(unittest.TestCase):
    def test_real_keep_alive_and_beat_are_decoded(self) -> None:
        device = parse_datagram(ANNOUNCEMENT_PORT, REAL_KEEP_ALIVE)
        self.assertEqual(device.player_id, 5)
        self.assertEqual(device.device_name, "CDJ-2000nexus")
        self.assertEqual(device.device_type, "CDJ")
        self.assertEqual(device.announced_ip, "169.254.99.100")

        beat = parse_datagram(BEAT_PORT, REAL_BEAT)
        self.assertEqual(beat.player_id, 1)
        self.assertAlmostEqual(beat.original_bpm, 132.01)
        self.assertAlmostEqual(beat.effective_bpm, 127.9836, places=3)
        self.assertAlmostEqual(beat.pitch_percent, -3.05, places=2)
        self.assertEqual(beat.beat_in_bar, 1)

    def test_captured_status_fields_are_decoded(self) -> None:
        status = parse_datagram(STATUS_PORT, status_packet())
        self.assertIsInstance(status, CdjStatusPacket)
        self.assertTrue(status.playing)
        self.assertTrue(status.is_master)
        self.assertTrue(status.sync_enabled)
        self.assertEqual(status.beat_number, 67)
        self.assertEqual(status.beat_in_bar, 3)
        self.assertEqual(status.original_bpm, 128.0)
        self.assertEqual(status.effective_bpm, 128.0)

    def test_precise_position_uses_only_documented_fields(self) -> None:
        position = parse_datagram(BEAT_PORT, precise_position_packet())
        self.assertIsInstance(position, PrecisePositionPacket)
        self.assertEqual(position.position_ms, 45_000)
        self.assertEqual(position.duration_ms, 300_000)
        self.assertEqual(position.effective_bpm, 136.2)
        self.assertAlmostEqual(position.pitch_percent, 6.0)
        self.assertAlmostEqual(position.original_bpm, 128.49, places=2)


class TestRealProvider(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.provider = RealProLinkProvider(
            enable_network=False,
            clock_ns=self.clock,
            stale_after_s=1.0,
            forget_after_s=2.0,
        )

    def feed(self, data: bytes, port: int) -> None:
        self.provider.ingest_datagram(
            data,
            local_port=port,
            source=("169.254.99.100", port),
            received_ns=self.clock(),
        )
        self.provider.poll()

    def test_device_appears_and_status_updates_master_and_bpm(self) -> None:
        states = []
        self.provider.subscribe_player_state(states.append)
        self.feed(announcement(), ANNOUNCEMENT_PORT)
        self.assertEqual(len(self.provider.get_players()), 1)
        self.assertTrue(self.provider.get_players()[0].online)

        self.feed(status_packet(), STATUS_PORT)
        state = self.provider.get_players()[0]
        self.assertTrue(state.is_master)
        self.assertTrue(state.playing)
        self.assertTrue(state.sync_enabled)
        self.assertEqual(state.original_bpm, 128.0)
        self.assertEqual(state.effective_bpm, 128.0)
        self.assertEqual(state.track_id, "prolink:1:3:1:42")
        self.assertGreaterEqual(len(states), 2)

    def test_real_beat_emits_beat_event_and_updates_tempo(self) -> None:
        beats = []
        self.provider.subscribe_beat_events(beats.append)
        self.feed(announcement(), ANNOUNCEMENT_PORT)
        self.feed(REAL_BEAT, BEAT_PORT)
        self.assertEqual(len(beats), 1)
        self.assertEqual(beats[0].beat_in_bar, 1)
        self.assertAlmostEqual(beats[0].bpm, 127.9836, places=3)
        state = self.provider.get_players()[0]
        self.assertTrue(state.playing)
        self.assertEqual(state.beat_number, 1)
        self.assertEqual(state.timing_source, "prolink-beat")

    def test_precise_position_becomes_valid_without_guessing_classic_position(self) -> None:
        self.feed(REAL_BEAT, BEAT_PORT)
        self.assertFalse(self.provider.get_players()[0].position_valid)
        self.feed(precise_position_packet(), BEAT_PORT)
        state = self.provider.get_players()[0]
        self.assertTrue(state.position_valid)
        self.assertEqual(state.position_ms, 45_000.0)
        self.assertEqual(state.duration_ms, 300_000)

    def test_device_becomes_stale_disappears_and_can_reappear(self) -> None:
        observed = []
        self.provider.subscribe_player_state(observed.append)
        self.feed(announcement(), ANNOUNCEMENT_PORT)

        self.clock.advance(1.1)
        self.provider.poll()
        self.assertFalse(self.provider.get_players()[0].online)
        self.assertEqual(observed[-1].timing_source, "prolink-stale")

        self.clock.advance(1.0)
        self.provider.poll()
        self.assertEqual(self.provider.get_players(), ())

        self.feed(announcement(), ANNOUNCEMENT_PORT)
        self.assertTrue(self.provider.get_players()[0].online)

    def test_unknown_and_invalid_packets_are_retained_and_do_not_crash(self) -> None:
        unknown = bytearray(REAL_BEAT)
        unknown[0x0A] = 0x7F
        self.feed(bytes(unknown), BEAT_PORT)
        self.feed(b"not a PRO DJ LINK packet", BEAT_PORT)
        self.feed(announcement(), ANNOUNCEMENT_PORT)

        outcomes = [item.outcome for item in self.provider.get_raw_diagnostics()]
        self.assertIn("unknown", outcomes)
        self.assertIn("malformed", outcomes)
        self.assertIn("parsed", outcomes)
        self.assertEqual(len(self.provider.get_players()), 1)


class TestProviderFailureIsolation(unittest.TestCase):
    @staticmethod
    def _settings(directory: str) -> Path:
        path = Path(directory) / "settings.json"
        path.write_text(json.dumps({"operating_mode": "CDJ"}), encoding="utf-8")
        return path

    def test_socket_failure_does_not_crash_main_application(self) -> None:
        def failing_socket(*_args, **_kwargs):
            raise OSError("test bind failure")

        provider = RealProLinkProvider(
            bind_address="0.0.0.0",
            socket_factory=failing_socket,
            reconnect_s=0.05,
        )
        with tempfile.TemporaryDirectory() as directory:
            app = CdjApplication(
                [3],
                start_audio=False,
                settings_path=self._settings(directory),
                prolink_provider=provider,
                operating_mode=OperatingMode.CDJ,
            )
            try:
                time.sleep(0.08)
                app.tick()
                self.assertIsNone(app.master_state())
                self.assertTrue(
                    any(
                        item.outcome == "network_error"
                        for item in provider.get_raw_diagnostics()
                    )
                )
            finally:
                app.close()

    def test_real_packets_reach_existing_master_and_gui_state(self) -> None:
        provider = RealProLinkProvider(enable_network=False)
        with tempfile.TemporaryDirectory() as directory:
            app = CdjApplication(
                [3],
                start_audio=False,
                settings_path=self._settings(directory),
                prolink_provider=provider,
                operating_mode=OperatingMode.CDJ,
            )
            try:
                provider.ingest_datagram(
                    announcement(),
                    local_port=ANNOUNCEMENT_PORT,
                    source=("169.254.99.100", ANNOUNCEMENT_PORT),
                )
                provider.ingest_datagram(
                    status_packet(),
                    local_port=STATUS_PORT,
                    source=("169.254.99.100", STATUS_PORT),
                )
                provider.ingest_datagram(
                    REAL_BEAT,
                    local_port=BEAT_PORT,
                    source=("169.254.99.100", BEAT_PORT),
                )
                app.tick()

                master = app.master_state()
                view = app.master_view(3)
                self.assertIsNotNone(master)
                self.assertEqual(master.source_type, "prolink")
                self.assertEqual(master.player_id, 1)
                self.assertAlmostEqual(master.bpm, 127.9836, places=3)
                self.assertIsNotNone(master.phase)
                self.assertIsNotNone(view)
                self.assertEqual(view.source_type, "prolink")
                self.assertEqual(view.player_id, 1)
                self.assertEqual(view.track_id, "prolink:1:3:1:42")
                self.assertEqual(view.beat, 1)
            finally:
                app.close()


class TestRealProviderCli(unittest.TestCase):
    def test_real_source_and_interface_are_accepted(self) -> None:
        import run_cdj

        args = run_cdj.parse_args(
            ["--prolink-source", "real", "--prolink-interface", "169.254.1.2"]
        )
        self.assertEqual(args.prolink_source, "real")
        self.assertEqual(args.prolink_interface, "169.254.1.2")


if __name__ == "__main__":
    unittest.main()
