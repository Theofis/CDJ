"""Parser fuer die ANLZ-Analysedateien von rekordbox.

Aufgeteilt nach Inhalt, damit jeder Teil fuer sich lesbar und pruefbar
bleibt::

    AnlzParser              diese Datei - fuegt zusammen
    +-- container.py        Dateikopf und Abschnittskette, Rohbytes
    +-- beatgrid.py         PQTZ
    +-- cues.py             PCOB / PCO2 (Cues, Hot Cues, Loops)
    +-- waveform.py         PWAV / PWV2 / PWV3 / PWV4 / PWV5
    +-- metadata.py         PPTH, PVBR, PSSI

Grundsatz: ein beschaedigter Abschnitt kostet **nur diesen Abschnitt**.
Wenn das Beatgrid unlesbar ist, sollen die Hot Cues trotzdem ankommen -
deshalb wird jeder Teil einzeln abgesichert und der Grund in
``warnings`` vermerkt, statt eine Ausnahme nach oben durchzureichen.

Alle Zahlen der ANLZ-Dateien sind big-endian, Texte UTF-16BE - anders als
in ``export.pdb``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .beatgrid import (
    TAG_BEAT_GRID,
    Beat,
    BeatGridFormatError,
    encode_beat_grid,
    parse_beat_grid,
)
from .container import (
    AnlzFile,
    AnlzFormatError,
    AnlzTag,
    build_tag,
)
from .cues import (
    TAG_CUES,
    TAG_CUES_EXTENDED,
    CueEntry,
    CueFormatError,
    CueList,
    CueListKind,
    encode_cue_list,
    parse_cue_list,
)
from .metadata import (
    TAG_PATH,
    AnlzMetadataError,
    parse_path,
)
from .waveform import (
    KNOWN_TAGS as WAVEFORM_TAGS,
    WaveformColumns,
    WaveformFormatError,
    parse_waveform,
)

__all__ = [
    "AnlzAnalysis",
    "AnlzFile",
    "AnlzFormatError",
    "AnlzParser",
    "AnlzTag",
    "Beat",
    "CueEntry",
    "CueList",
    "CueListKind",
    "WaveformColumns",
    "build_tag",
    "encode_beat_grid",
    "encode_cue_list",
]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnlzAnalysis:
    """Was aus einer Analysedatei gelesen wurde."""

    #: Die Datei selbst, mit allen Abschnitten als Rohbytes. Der Writer
    #: arbeitet hierauf.
    file: AnlzFile
    beat_grid: tuple[Beat, ...] = ()
    cue_lists: tuple[CueList, ...] = ()
    waveforms: tuple[WaveformColumns, ...] = ()
    #: Pfad der Audiodatei laut ``PPTH``.
    audio_path: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def path(self) -> str:
        return self.file.path

    def cue_list(self, kind: CueListKind, *, extended: bool) -> CueList | None:
        for cue_list in self.cue_lists:
            if cue_list.kind is kind and cue_list.extended == extended:
                return cue_list
        return None

    def waveform(self, fourcc: str) -> WaveformColumns | None:
        for waveform in self.waveforms:
            if waveform.fourcc == fourcc:
                return waveform
        return None


class AnlzParser:
    """Liest eine Analysedatei und deutet die Abschnitte, die gesichert
    sind. Unbekannte Abschnitte bleiben unangetastet in ``file``."""

    @staticmethod
    def parse(file: AnlzFile) -> AnlzAnalysis:
        warnings: list[str] = list(file.warnings)
        beats: tuple[Beat, ...] = ()
        cue_lists: list[CueList] = []
        waveforms: list[WaveformColumns] = []
        audio_path = ""

        for tag in file.tags:
            try:
                if tag.fourcc == TAG_BEAT_GRID:
                    beats = parse_beat_grid(tag)
                elif tag.fourcc in (TAG_CUES, TAG_CUES_EXTENDED):
                    cue_lists.append(parse_cue_list(tag))
                elif tag.fourcc in WAVEFORM_TAGS:
                    waveforms.append(parse_waveform(tag))
                elif tag.fourcc == TAG_PATH:
                    audio_path = parse_path(tag)
            except (
                BeatGridFormatError,
                CueFormatError,
                WaveformFormatError,
                AnlzMetadataError,
            ) as error:
                warnings.append(f"{tag.fourcc}: {error}")
                log.warning("%s: %s: %s", file.path, tag.fourcc, error)

        return AnlzAnalysis(
            file=file,
            beat_grid=beats,
            cue_lists=tuple(cue_lists),
            waveforms=tuple(waveforms),
            audio_path=audio_path,
            warnings=tuple(warnings),
        )

    @staticmethod
    def read(path: str | Path) -> AnlzAnalysis:
        """Datei lesen und deuten.

        Raises:
            AnlzFormatError: Datei fehlt oder ist keine ANLZ-Datei. Alles
                Feinere landet in ``warnings``.
        """
        return AnlzParser.parse(AnlzFile.read(path))
