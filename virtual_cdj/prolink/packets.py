"""Read-only decoder for the documented PRO DJ LINK UDP packets.

Only fields corroborated by the Deep Symmetry packet analysis/beat-link and
real-hardware capture based implementations are decoded.  Unknown bytes stay
in the provider's bounded raw-packet journal; this module never manufactures
or transmits a PRO DJ LINK datagram.
"""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address

ANNOUNCEMENT_PORT = 50_000
BEAT_PORT = 50_001
STATUS_PORT = 50_002
PROLINK_PORTS = (ANNOUNCEMENT_PORT, BEAT_PORT, STATUS_PORT)

MAGIC = b"Qspt1WmJOL"
NEUTRAL_PITCH = 0x0010_0000


class PacketDecodeError(ValueError):
    """A datagram is not a safely decodable packet of the expected kind."""


@dataclass(frozen=True, slots=True)
class DeviceAnnouncementPacket:
    player_id: int
    device_name: str | None
    device_type: str | None
    announced_ip: str
    hardware_address: str
    peer_count: int


@dataclass(frozen=True, slots=True)
class BeatPacket:
    player_id: int
    device_name: str | None
    original_bpm: float | None
    effective_bpm: float | None
    pitch_percent: float
    beat_in_bar: int | None


@dataclass(frozen=True, slots=True)
class PrecisePositionPacket:
    player_id: int
    device_name: str | None
    duration_ms: int
    position_ms: int
    original_bpm: float | None
    effective_bpm: float | None
    pitch_percent: float


@dataclass(frozen=True, slots=True)
class CdjStatusPacket:
    player_id: int
    device_name: str | None
    repeated_player_id: int
    track_source_player: int | None
    track_source_slot: int
    track_type: int
    rekordbox_track_id: int
    play_state: int
    playing: bool
    paused: bool
    reverse: bool
    sync_enabled: bool
    is_master: bool
    on_air: bool
    original_bpm: float | None
    effective_bpm: float | None
    pitch_percent: float
    beat_number: int | None
    beat_in_bar: int | None
    status_flags: int
    reported_body_length: int


@dataclass(frozen=True, slots=True)
class UnknownPacket:
    local_port: int
    packet_type: int


DecodedPacket = (
    DeviceAnnouncementPacket
    | BeatPacket
    | PrecisePositionPacket
    | CdjStatusPacket
    | UnknownPacket
)


def _require_magic(data: bytes) -> None:
    if len(data) < len(MAGIC) + 1:
        raise PacketDecodeError(
            f"packet too short for common header: {len(data)} bytes"
        )
    if data[: len(MAGIC)] != MAGIC:
        raise PacketDecodeError("invalid PRO DJ LINK magic")


def _device_name(data: bytes, offset: int) -> str | None:
    raw = data[offset : offset + 20].split(b"\0", 1)[0]
    if not raw:
        return None
    value = raw.decode("ascii", errors="replace").strip()
    return value or None


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def _pitch(raw: int) -> tuple[float, float]:
    multiplier = raw / float(NEUTRAL_PITCH)
    return multiplier, (multiplier - 1.0) * 100.0


def parse_device_announcement(data: bytes) -> DeviceAnnouncementPacket:
    """Decode a type-0x06, 54-byte keep-alive from UDP 50000."""

    _require_magic(data)
    if data[0x0A] != 0x06:
        raise PacketDecodeError("not a device keep-alive")
    if len(data) != 0x36:
        raise PacketDecodeError(
            f"device keep-alive must be 54 bytes, got {len(data)}"
        )
    player_id = data[0x24]
    if player_id == 0:
        raise PacketDecodeError("device keep-alive has device number 0")

    # Byte 0x34 is the role byte in captured keep-alives. Byte 0x25 is not:
    # real-hardware captures identify it as "was first on network".
    device_type = {0x01: "CDJ", 0x02: "MIXER"}.get(data[0x34])
    mac = ":".join(f"{part:02x}" for part in data[0x26:0x2C])
    return DeviceAnnouncementPacket(
        player_id=player_id,
        device_name=_device_name(data, 0x0C),
        device_type=device_type,
        announced_ip=str(IPv4Address(data[0x2C:0x30])),
        hardware_address=mac,
        peer_count=data[0x30],
    )


def _check_shared_update(
    data: bytes, *, packet_type: int, exact_length: int, label: str
) -> int:
    _require_magic(data)
    if data[0x0A] != packet_type:
        raise PacketDecodeError(f"not a {label} packet")
    if len(data) != exact_length:
        raise PacketDecodeError(
            f"{label} packet must be {exact_length} bytes, got {len(data)}"
        )
    player_id = data[0x21]
    if player_id == 0:
        raise PacketDecodeError(f"{label} packet has device number 0")
    return player_id


def parse_beat(data: bytes) -> BeatPacket:
    """Decode a type-0x28 beat announcement from UDP 50001."""

    player_id = _check_shared_update(
        data, packet_type=0x28, exact_length=0x60, label="beat"
    )
    raw_pitch = _u32(data, 0x54)
    multiplier, pitch_percent = _pitch(raw_pitch)
    raw_bpm = _u16(data, 0x5A)
    original_bpm = None if raw_bpm in (0, 0xFFFF) else raw_bpm / 100.0
    effective_bpm = (
        original_bpm * multiplier if original_bpm is not None else None
    )
    raw_beat_in_bar = data[0x5C]
    beat_in_bar = raw_beat_in_bar if 1 <= raw_beat_in_bar <= 4 else None
    return BeatPacket(
        player_id=player_id,
        device_name=_device_name(data, 0x0B),
        original_bpm=original_bpm,
        effective_bpm=effective_bpm,
        pitch_percent=pitch_percent,
        beat_in_bar=beat_in_bar,
    )


def parse_precise_position(data: bytes) -> PrecisePositionPacket:
    """Decode a CDJ-3000-family type-0x0b absolute-position packet."""

    player_id = _check_shared_update(
        data, packet_type=0x0B, exact_length=0x3C,
        label="precise position",
    )
    signed_pitch = int.from_bytes(data[0x2C:0x30], "big", signed=True)
    pitch_percent = signed_pitch / 100.0
    multiplier = 1.0 + pitch_percent / 100.0
    raw_effective_bpm = _u32(data, 0x38)
    effective_bpm = (
        raw_effective_bpm / 10.0 if raw_effective_bpm != 0 else None
    )
    original_bpm = (
        effective_bpm / multiplier
        if effective_bpm is not None and multiplier > 0.0
        else None
    )
    return PrecisePositionPacket(
        player_id=player_id,
        device_name=_device_name(data, 0x0B),
        duration_ms=_u32(data, 0x24) * 1000,
        position_ms=_u32(data, 0x28),
        original_bpm=original_bpm,
        effective_bpm=effective_bpm,
        pitch_percent=pitch_percent,
    )


def parse_cdj_status(data: bytes) -> CdjStatusPacket:
    """Decode the established live fields of a type-0x0a CDJ status packet."""

    _require_magic(data)
    if data[0x0A] != 0x0A:
        raise PacketDecodeError("not a CDJ status packet")
    # 0xcc is beat-link's conservative minimum for the live status fields.
    if len(data) < 0xCC:
        raise PacketDecodeError(
            f"CDJ status needs at least 204 bytes, got {len(data)}"
        )
    player_id = data[0x21]
    if player_id == 0:
        raise PacketDecodeError("CDJ status has device number 0")

    flags = data[0x89]
    play_state = data[0x7B]
    play_state_2 = data[0x8B]
    play_state_3 = data[0x9D]
    if len(data) >= 0xD4:
        playing = bool(flags & 0x40)
    else:
        playing = play_state in (0x03, 0x04) or (
            play_state == 0x09 and play_state_2 == 0x7A
        )

    raw_pitch = _u32(data, 0x8C)
    multiplier, pitch_percent = _pitch(raw_pitch)
    raw_bpm = _u16(data, 0x92)
    original_bpm = None if raw_bpm == 0xFFFF else raw_bpm / 100.0
    effective_bpm = (
        original_bpm * multiplier if original_bpm is not None else None
    )
    raw_beat_number = _u32(data, 0xA0)
    beat_number = None if raw_beat_number == 0xFFFF_FFFF else raw_beat_number
    raw_beat_in_bar = data[0xA6]
    beat_in_bar = raw_beat_in_bar if 1 <= raw_beat_in_bar <= 4 else None
    source_player = data[0x28] or None

    return CdjStatusPacket(
        player_id=player_id,
        device_name=_device_name(data, 0x0B),
        repeated_player_id=data[0x24],
        track_source_player=source_player,
        track_source_slot=data[0x29],
        track_type=data[0x2A],
        rekordbox_track_id=_u32(data, 0x2C),
        play_state=play_state,
        playing=playing,
        paused=play_state in (0x05, 0x06),
        reverse=play_state == 0x03 and play_state_3 == 0x01,
        sync_enabled=bool(flags & 0x10),
        # 0x9e is authoritative and can be 2 for a master without beat grid.
        is_master=data[0x9E] != 0,
        on_air=bool(flags & 0x08),
        original_bpm=original_bpm,
        effective_bpm=effective_bpm,
        pitch_percent=pitch_percent,
        beat_number=beat_number,
        beat_in_bar=beat_in_bar,
        status_flags=flags,
        reported_body_length=_u16(data, 0x22),
    )


def parse_datagram(local_port: int, data: bytes) -> DecodedPacket:
    """Decode known read-only packets and preserve unknown kinds as such."""

    _require_magic(data)
    packet_type = data[0x0A]
    if local_port == ANNOUNCEMENT_PORT and packet_type == 0x06:
        return parse_device_announcement(data)
    if local_port == BEAT_PORT:
        if packet_type == 0x28:
            return parse_beat(data)
        if packet_type == 0x0B:
            return parse_precise_position(data)
    if local_port == STATUS_PORT and packet_type == 0x0A:
        return parse_cdj_status(data)
    return UnknownPacket(local_port=local_port, packet_type=packet_type)
