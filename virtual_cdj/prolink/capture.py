"""Dependency-free JSONL raw packet journal and deterministic replay."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


LOG = logging.getLogger("PROLINK")


@dataclass(frozen=True, slots=True)
class JournalRecord:
    version: int
    timestamp_ns: int
    direction: str
    source_ip: str
    source_port: int
    destination_ip: str
    destination_port: int
    packet_type: int | None
    packet_name: str
    length: int
    payload_hex: str

    @property
    def payload(self) -> bytes:
        return bytes.fromhex(self.payload_hex)


class PacketJournal:
    """Append complete datagrams as one self-contained JSON object per line."""

    def __init__(self, path: str | Path, *, raw_dump: bool = False) -> None:
        self.path = Path(path)
        self.raw_dump = raw_dump
        self._lock = threading.Lock()

    def record(
        self,
        *,
        timestamp_ns: int,
        direction: str,
        source_ip: str,
        source_port: int,
        destination_ip: str,
        destination_port: int,
        data: bytes,
        packet_name: str,
    ) -> JournalRecord:
        payload = bytes(data)
        record = JournalRecord(
            version=1,
            timestamp_ns=int(timestamp_ns),
            direction=direction,
            source_ip=source_ip,
            source_port=int(source_port),
            destination_ip=destination_ip,
            destination_port=int(destination_port),
            packet_type=payload[0x0A] if len(payload) > 0x0A else None,
            packet_name=packet_name,
            length=len(payload),
            payload_hex=payload.hex(),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(record), sort_keys=True, separators=(",", ":"))
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
        if self.raw_dump:
            LOG.debug(
                "[PROLINK %s] timestamp_ns=%d source=%s:%d destination=%s:%d "
                "type=%s length=%d hex=%s",
                direction.upper(),
                record.timestamp_ns,
                source_ip,
                source_port,
                destination_ip,
                destination_port,
                packet_name,
                record.length,
                record.payload_hex,
            )
        return record


def read_journal(path: str | Path) -> tuple[JournalRecord, ...]:
    records: list[JournalRecord] = []
    with Path(path).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                record = JournalRecord(**raw)
                if record.version != 1 or record.length != len(record.payload):
                    raise ValueError("unsupported version or length mismatch")
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"invalid PRO LINK journal line {line_number}: {exc}"
                ) from exc
            records.append(record)
    return tuple(records)


class ReplayTarget(Protocol):
    def ingest_datagram(
        self,
        data: bytes,
        *,
        local_port: int,
        source: tuple[str, int] = ("0.0.0.0", 0),
        received_ns: int | None = None,
    ) -> None: ...

    def poll(self) -> None: ...


def replay_journal(
    path: str | Path,
    target: ReplayTarget,
    *,
    realtime: bool = False,
    speed: float = 1.0,
    include_tx: bool = True,
) -> int:
    """Feed journaled bytes through the provider's normal ingestion/parser."""

    if speed <= 0:
        raise ValueError("replay speed must be positive")
    records = [
        record
        for record in read_journal(path)
        if record.direction == "rx" or include_tx
    ]
    if not records:
        return 0
    first_record_ns = records[0].timestamp_ns
    start_ns = time.monotonic_ns()
    for record in records:
        relative_ns = record.timestamp_ns - first_record_ns
        if realtime and relative_ns > 0:
            deadline = start_ns + int(relative_ns / speed)
            remaining = deadline - time.monotonic_ns()
            if remaining > 0:
                time.sleep(remaining / 1_000_000_000)
        target.ingest_datagram(
            record.payload,
            local_port=record.destination_port,
            source=(record.source_ip, record.source_port),
            received_ns=time.monotonic_ns(),
        )
        target.poll()
    return len(records)
