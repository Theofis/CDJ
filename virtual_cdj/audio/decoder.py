"""Dekoder-Abstraktion.

Der Rest des Programms sieht nur ``AudioDecoder`` und weiss nicht, welche
Bibliothek eine Datei tatsaechlich liest. Ein weiteres Backend (z. B. PyAV
fuer AAC/M4A) wird ueber ``register_backend`` ergaenzt, ohne dass sich an
Analyse, Engine oder GUI etwas aendert.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .format import AudioBuffer, to_internal


class DecodeError(RuntimeError):
    """Datei konnte nicht dekodiert werden."""


class UnsupportedFormat(DecodeError):
    """Kein registriertes Backend kann dieses Format lesen."""


@dataclass(frozen=True)
class AudioMetadata:
    """Was ohne vollstaendiges Dekodieren feststellbar ist."""

    path: str
    format: str
    subtype: str
    sample_rate: int
    channels: int
    frames: int

    @property
    def duration_s(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0


class AudioDecoder(ABC):
    """Liest eine Audiodatei in das interne Format."""

    #: Kleinbuchstabige Endungen, die dieses Backend liest.
    extensions: frozenset[str] = frozenset()

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise DecodeError(f"Datei nicht gefunden: {self.path}")

    @abstractmethod
    def load_metadata(self) -> AudioMetadata:
        """Kopfdaten lesen, ohne Audio zu dekodieren."""

    @abstractmethod
    def decode(self) -> AudioBuffer:
        """Ganze Datei dekodieren, in der Rate der Datei."""

    @abstractmethod
    def decode_region(self, start_frame: int, frames: int) -> AudioBuffer:
        """Abschnitt dekodieren, in der Rate der Datei."""

    # -- abgeleitet -------------------------------------------------------

    @property
    def duration(self) -> float:
        return self.load_metadata().duration_s

    @property
    def samplerate(self) -> int:
        return self.load_metadata().sample_rate

    @property
    def channels(self) -> int:
        return self.load_metadata().channels


# --------------------------------------------------------------------------
# libsndfile-Backend: WAV, FLAC, AIFF, MP3, OGG
# --------------------------------------------------------------------------


class SoundFileDecoder(AudioDecoder):
    """Backend auf Basis von ``soundfile`` / libsndfile."""

    extensions = frozenset({
        ".wav", ".wave", ".flac", ".aif", ".aiff", ".aifc",
        ".mp3", ".ogg", ".oga", ".opus", ".w64", ".caf", ".au",
    })

    def __init__(self, path: str | Path) -> None:
        super().__init__(path)
        self._metadata: AudioMetadata | None = None

    def load_metadata(self) -> AudioMetadata:
        if self._metadata is not None:
            return self._metadata
        import soundfile as sf

        try:
            info = sf.info(str(self.path))
        except Exception as error:  # pragma: no cover - defekte Datei
            raise DecodeError(f"{self.path.name}: {error}") from error

        self._metadata = AudioMetadata(
            path=str(self.path),
            format=info.format,
            subtype=info.subtype or "",
            sample_rate=int(info.samplerate),
            channels=int(info.channels),
            frames=int(info.frames),
        )
        return self._metadata

    def decode(self) -> AudioBuffer:
        import soundfile as sf

        try:
            data, sample_rate = sf.read(
                str(self.path), dtype="float32", always_2d=True
            )
        except Exception as error:  # pragma: no cover - defekte Datei
            raise DecodeError(f"{self.path.name}: {error}") from error
        return to_internal(data, int(sample_rate))

    def decode_region(self, start_frame: int, frames: int) -> AudioBuffer:
        import soundfile as sf

        metadata = self.load_metadata()
        start = max(0, min(start_frame, metadata.frames))
        count = max(0, min(frames, metadata.frames - start))
        if count == 0:
            return AudioBuffer.empty(metadata.sample_rate)
        try:
            with sf.SoundFile(str(self.path)) as handle:
                handle.seek(start)
                data = handle.read(count, dtype="float32", always_2d=True)
        except Exception as error:  # pragma: no cover - defekte Datei
            raise DecodeError(f"{self.path.name}: {error}") from error
        return to_internal(data, metadata.sample_rate)


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

BackendFactory = Callable[[Path], AudioDecoder]

_BACKENDS: list[tuple[frozenset[str], BackendFactory]] = []


def register_backend(
    extensions: frozenset[str] | set[str] | tuple[str, ...],
    factory: BackendFactory,
) -> None:
    """Weiteres Dekoder-Backend anmelden.

    Beispiel fuer AAC/M4A, sobald PyAV installiert ist::

        register_backend({".m4a", ".aac", ".mp4"}, PyAvDecoder)
    """
    _BACKENDS.append((frozenset(e.lower() for e in extensions), factory))


register_backend(SoundFileDecoder.extensions, SoundFileDecoder)


def supported_extensions() -> frozenset[str]:
    result: set[str] = set()
    for extensions, _ in _BACKENDS:
        result |= extensions
    return frozenset(result)


def open_decoder(path: str | Path) -> AudioDecoder:
    """Passenden Dekoder fuer eine Datei liefern.

    Raises:
        UnsupportedFormat: wenn kein Backend die Endung kennt.
    """
    path = Path(path)
    extension = path.suffix.lower()
    for extensions, factory in _BACKENDS:
        if extension in extensions:
            return factory(path)
    raise UnsupportedFormat(
        f"Kein Dekoder fuer {extension or '(ohne Endung)'}. "
        f"Unterstuetzt: {', '.join(sorted(supported_extensions()))}"
    )


def decode_file(path: str | Path) -> tuple[AudioBuffer, AudioMetadata]:
    """Bequemer Weg: Datei oeffnen, Kopfdaten und Audio liefern."""
    decoder = open_decoder(path)
    metadata = decoder.load_metadata()
    return decoder.decode(), metadata
