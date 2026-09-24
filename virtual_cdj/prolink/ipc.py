"""Kleines versioniertes Wire-Format für den internen Simulator.

Das ist ausdrücklich **kein** PRO-DJ-LINK-Paketformat.  Newline-delimited
JSON reicht für localhost, benötigt keine Dependency und ist in Tests sowie
mit normalen Diagnosewerkzeugen lesbar.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .models import BeatEvent, PlayerState

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024


class MessageError(ValueError):
    """Ungültige oder nicht unterstützte Simulatornachricht."""


def encode_player_state(state: PlayerState) -> bytes:
    return _encode("player_state", asdict(state))


def encode_beat_event(event: BeatEvent) -> bytes:
    return _encode("beat_event", asdict(event))


def _encode(kind: str, payload: dict[str, Any]) -> bytes:
    envelope = {
        "version": PROTOCOL_VERSION,
        "type": kind,
        "payload": payload,
    }
    return (
        json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def decode_message(line: bytes | str) -> PlayerState | BeatEvent:
    """Eine vollständige Zeile dekodieren und strukturell prüfen."""
    raw = line.encode("utf-8") if isinstance(line, str) else line
    if len(raw) > MAX_MESSAGE_BYTES:
        raise MessageError("Nachricht ist zu groß")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MessageError("Nachricht ist kein gültiges UTF-8/JSON") from exc
    if not isinstance(envelope, dict):
        raise MessageError("Envelope muss ein Objekt sein")
    if envelope.get("version") != PROTOCOL_VERSION:
        raise MessageError("Nicht unterstützte Protokollversion")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise MessageError("payload muss ein Objekt sein")

    kind = envelope.get("type")
    try:
        if kind == "player_state":
            state = PlayerState(**payload)
            _validate_player_state(state)
            return state
        if kind == "beat_event":
            event = BeatEvent(**payload)
            _validate_beat_event(event)
            return event
    except (TypeError, ValueError) as exc:
        raise MessageError(f"Ungültiger {kind or 'unbekannter'}-Payload") from exc
    raise MessageError(f"Unbekannter Nachrichtentyp: {kind!r}")


def _validate_player_state(state: PlayerState) -> None:
    if state.player_id <= 0:
        raise ValueError("player_id muss positiv sein")
    if state.last_update_ns < 0:
        raise ValueError("last_update_ns darf nicht negativ sein")
    for bpm in (state.original_bpm, state.effective_bpm):
        if bpm is not None and bpm <= 0:
            raise ValueError("BPM muss positiv sein")
    if state.position_ms is not None and state.position_ms < 0:
        raise ValueError("position_ms darf nicht negativ sein")


def _validate_beat_event(event: BeatEvent) -> None:
    if event.player_id <= 0 or event.beat_number <= 0:
        raise ValueError("Player- und Beatnummer müssen positiv sein")
    if not 1 <= event.beat_in_bar <= 4:
        raise ValueError("beat_in_bar muss zwischen 1 und 4 liegen")
    if event.timestamp_ns < 0 or event.bpm <= 0:
        raise ValueError("Zeitstempel und BPM sind ungültig")
