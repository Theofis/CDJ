"""Evidence-backed PRO DJ LINK packet encoders used by developer tooling.

This module deliberately contains no generic ``make_packet`` escape hatch.
Every public encoder has a fixed layout backed by hardware captures in
``usr-ein/prolink`` and, for the live fields, the Deep Symmetry packet
analysis/beat-link implementation.  Unknown status bytes come from a captured
CDJ-2000NXS/1.44 skeleton and are never synthesised.
"""

from __future__ import annotations

from ipaddress import IPv4Address

from .packets import MAGIC, NEUTRAL_PITCH


CDJ_NAME = "CDJ-2000nexus"
OBSERVER_PLAYER_ID = 7

# Captured CDJ-2000NXS/1.44 status skeleton published by usr-ein/prolink.
# Identifying and mutable fields are zeroed in that source.  Keeping the full
# packet is intentional: roughly 260 bytes are not understood well enough to
# construct and must not be guessed.
CAPTURED_STATUS_SKELETON = bytes.fromhex(
    """
    5173707431576d4a4f4c0a000000000000000000000000000000000000000001040000f800000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000100060400000000000000000100000000000000312e343400000000000000030084fffe000f83127fffffff7fffffff00000000000000ff0000000401ff0000000000000000000000000000000001000000000000000000000f831200000000000000000f010000123456780000000101010101020100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000001500000753000005b4
    """
)


def parse_mac(value: str | bytes) -> bytes:
    """Return a six-byte MAC address or raise a precise ``ValueError``."""

    if isinstance(value, bytes):
        raw = value
    else:
        compact = value.replace(":", "").replace("-", "").strip()
        try:
            raw = bytes.fromhex(compact)
        except ValueError as exc:
            raise ValueError(f"invalid MAC address: {value!r}") from exc
    if len(raw) != 6:
        raise ValueError("MAC address must contain exactly 6 bytes")
    return raw


def format_mac(value: bytes) -> str:
    return ":".join(f"{part:02x}" for part in parse_mac(value))


def _name_bytes(name: str) -> bytes:
    try:
        encoded = name.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("PRO DJ LINK device names must be ASCII") from exc
    if len(encoded) > 20:
        raise ValueError("PRO DJ LINK device names are limited to 20 bytes")
    return encoded.ljust(20, b"\0")


def _validate_player_id(player_id: int) -> int:
    value = int(player_id)
    if not 1 <= value <= 255:
        raise ValueError("player number must be in 1..255")
    return value


def _discovery_header(kind: int, length: int, name: str, *, role: int = 1) -> bytearray:
    raw = bytearray(length)
    raw[:10] = MAGIC
    raw[0x0A] = kind
    raw[0x0B] = 0
    raw[0x0C:0x20] = _name_bytes(name)
    raw[0x20] = 1
    raw[0x21] = 2 if role == 1 else 1  # DeviceKind: CDJ=2, mixer=1.
    raw[0x22] = 0
    raw[0x23] = length
    return raw


def _update_header(raw: bytearray, kind: int, name: str, player_id: int) -> None:
    raw[:10] = MAGIC
    raw[0x0A] = kind
    raw[0x0B:0x1F] = _name_bytes(name)
    raw[0x1F] = 1
    raw[0x21] = _validate_player_id(player_id)
    raw[0x22:0x24] = (len(raw) - 0x24).to_bytes(2, "big")


def encode_hello(*, name: str = CDJ_NAME, role: int = 1) -> bytes:
    raw = _discovery_header(0x0A, 0x25, name, role=role)
    raw[0x24] = role
    return bytes(raw)


def encode_claim_mac(
    *, iteration: int, mac: str | bytes, name: str = CDJ_NAME, role: int = 1
) -> bytes:
    if iteration not in (1, 2, 3):
        raise ValueError("claim iteration must be 1, 2 or 3")
    raw = _discovery_header(0x00, 0x2C, name, role=role)
    raw[0x24] = iteration
    raw[0x25] = role
    raw[0x26:0x2C] = parse_mac(mac)
    return bytes(raw)


def encode_claim_ip(
    *,
    ip: str,
    mac: str | bytes,
    player_id: int,
    iteration: int,
    name: str = CDJ_NAME,
    role: int = 1,
    assignment_mode: int = 2,
) -> bytes:
    if iteration not in (1, 2, 3):
        raise ValueError("claim iteration must be 1, 2 or 3")
    if assignment_mode not in (1, 2):
        raise ValueError("assignment mode must be 1 (auto) or 2 (manual)")
    raw = _discovery_header(0x02, 0x32, name, role=role)
    raw[0x24:0x28] = IPv4Address(ip).packed
    raw[0x28:0x2E] = parse_mac(mac)
    raw[0x2E] = _validate_player_id(player_id)
    raw[0x2F] = iteration
    raw[0x30] = role
    raw[0x31] = assignment_mode
    return bytes(raw)


def encode_claim_number(
    *, player_id: int, iteration: int, name: str = CDJ_NAME, role: int = 1
) -> bytes:
    if iteration not in (1, 2, 3):
        raise ValueError("claim iteration must be 1, 2 or 3")
    raw = _discovery_header(0x04, 0x26, name, role=role)
    raw[0x24] = _validate_player_id(player_id)
    raw[0x25] = iteration
    return bytes(raw)


def encode_number_conflict(
    *, player_id: int, ip: str, name: str = CDJ_NAME, role: int = 1
) -> bytes:
    raw = _discovery_header(0x08, 0x29, name, role=role)
    raw[0x24] = _validate_player_id(player_id)
    raw[0x25:0x29] = IPv4Address(ip).packed
    return bytes(raw)


def encode_keep_alive(
    *,
    player_id: int,
    ip: str,
    mac: str | bytes,
    name: str = CDJ_NAME,
    peer_count: int = 1,
    was_first_on_network: bool = False,
    role: int = 1,
    generation: int = 0,
) -> bytes:
    """Encode the captured 54-byte type-0x06 announcement."""

    if not 0 <= int(peer_count) <= 255:
        raise ValueError("peer count must fit in one byte")
    if not 0 <= int(generation) <= 255:
        raise ValueError("generation must fit in one byte")
    raw = _discovery_header(0x06, 0x36, name, role=role)
    raw[0x24] = _validate_player_id(player_id)
    raw[0x25] = 2 if was_first_on_network else 1
    raw[0x26:0x2C] = parse_mac(mac)
    raw[0x2C:0x30] = IPv4Address(ip).packed
    raw[0x30] = int(peer_count)
    raw[0x31:0x34] = b"\0\0\0"
    raw[0x34] = role
    raw[0x35] = int(generation)
    return bytes(raw)


def pitch_to_wire(pitch_percent: float) -> int:
    multiplier = 1.0 + float(pitch_percent) / 100.0
    if multiplier <= 0:
        raise ValueError("pitch must result in a positive playback multiplier")
    return max(0, min(0xFFFF_FFFF, round(NEUTRAL_PITCH * multiplier)))


def encode_beat(
    *,
    player_id: int,
    bpm: float,
    pitch_percent: float,
    beat_in_bar: int,
    name: str = CDJ_NAME,
) -> bytes:
    """Encode the exact 96-byte type-0x28 beat layout.

    The six time-to-grid fields are deliberately based on the track's original
    BPM.  Pitch changes packet arrival cadence and the fixed-point pitch field;
    the captured protocol's timing fields remain at zero-pitch grid distance.
    """

    bpm_value = float(bpm)
    if not 0 < bpm_value < 655.35:
        raise ValueError("BPM must be greater than 0 and below 655.35")
    if beat_in_bar not in (1, 2, 3, 4):
        raise ValueError("beat in bar must be 1, 2, 3 or 4")
    raw = bytearray(0x60)
    _update_header(raw, 0x28, name, player_id)
    period_ms = 60_000.0 / bpm_value
    beats_to_bar = 5 - beat_in_bar
    factors = (1, 2, beats_to_bar, 4, beats_to_bar + 4, 8)
    for offset, factor in zip(range(0x24, 0x3C, 4), factors):
        raw[offset : offset + 4] = round(period_ms * factor).to_bytes(4, "big")
    raw[0x3C:0x54] = b"\xff" * 24
    raw[0x54:0x58] = pitch_to_wire(pitch_percent).to_bytes(4, "big")
    raw[0x58:0x5A] = b"\0\0"
    raw[0x5A:0x5C] = round(bpm_value * 100).to_bytes(2, "big")
    raw[0x5C] = beat_in_bar
    raw[0x5D:0x5F] = b"\0\0"
    raw[0x5F] = _validate_player_id(player_id)
    return bytes(raw)


def encode_cdj_status(
    *,
    player_id: int,
    bpm: float,
    pitch_percent: float,
    beat_number: int,
    beat_in_bar: int,
    playing: bool,
    master: bool,
    sync: bool,
    on_air: bool,
    track_id: int,
    packet_counter: int,
    name: str = CDJ_NAME,
) -> bytes:
    """Build status by changing only documented fields in a real skeleton."""

    device = _validate_player_id(player_id)
    if not 0 < float(bpm) < 655.35:
        raise ValueError("BPM must be greater than 0 and below 655.35")
    if beat_in_bar not in (1, 2, 3, 4):
        raise ValueError("beat in bar must be 1, 2, 3 or 4")
    if not 1 <= int(beat_number) <= 0xFFFF_FFFE:
        raise ValueError("beat number must be in 1..0xfffffffe")
    if not 1 <= int(track_id) <= 0xFFFF_FFFF:
        raise ValueError("track ID must be in 1..0xffffffff")

    raw = bytearray(CAPTURED_STATUS_SKELETON)
    _update_header(raw, 0x0A, name, device)
    raw[0x24] = device
    raw[0x28] = device
    raw[0x29] = 3  # USB slot, established Slot::USB wire value.
    raw[0x2A] = 1  # rekordbox-analyzed track.
    raw[0x2C:0x30] = int(track_id).to_bytes(4, "big")
    raw[0x6F] = 0  # USB media mounted.
    raw[0x73] = 4  # SD slot empty.
    raw[0x75] = 1  # Link/media available.
    raw[0x7B] = 3 if playing else 5
    raw[0x7C:0x80] = b"1.44"

    flags = raw[0x89]
    for bit, enabled in (
        (0x40, playing),
        (0x20, master),
        (0x10, sync),
        (0x08, on_air),
    ):
        flags = flags | bit if enabled else flags & ~bit
    raw[0x89] = flags
    raw[0x8B] = 0xFA if playing else 0xFE
    pitch = pitch_to_wire(pitch_percent).to_bytes(4, "big")
    for offset in (0x8C, 0x98, 0xC0, 0xC4):
        raw[offset : offset + 4] = pitch
    raw[0x90:0x92] = (0x8000).to_bytes(2, "big")
    raw[0x92:0x94] = round(float(bpm) * 100).to_bytes(2, "big")
    raw[0x9D] = 0x09 if playing else 0x01
    raw[0x9E] = 1 if master else 0
    raw[0x9F] = 0xFF
    raw[0xA0:0xA4] = int(beat_number).to_bytes(4, "big")
    raw[0xA6] = beat_in_bar
    raw[0xC8:0xCC] = (int(packet_counter) & 0xFFFF_FFFF).to_bytes(4, "big")
    return bytes(raw)


def discovery_packet_name(data: bytes) -> str:
    """Return a stable diagnostic name for a UDP-50000 type byte."""

    if len(data) <= 0x0A or data[:10] != MAGIC:
        return "malformed"
    return {
        0x00: "claim_mac",
        0x02: "claim_ip",
        0x04: "claim_number",
        0x05: "number_in_use",
        0x06: "keep_alive",
        0x08: "number_conflict",
        0x0A: "hello",
    }.get(data[0x0A], f"unknown_0x{data[0x0A]:02x}")
