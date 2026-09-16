"""Behaelter der ``ANLZ``-Analysedateien (``.DAT``, ``.EXT``, ``.2EX``).

Aufbau: ein kurzer Dateikopf, danach eine Kette von Abschnitten
("Tags"). Jeder Abschnitt nennt seine eigene Laenge, deshalb kann ein
Leser ueber Abschnitte hinwegspringen, **die er gar nicht versteht**::

    "PMAI"  len_header  len_file   ... Kopf ...
    "PQTZ"  len_header  len_tag    ... Beatgrid ...
    "PCOB"  len_header  len_tag    ... Cues ...
    "XXXX"  len_header  len_tag    ... unbekannt, bleibt unveraendert ...

Alle Zahlen sind **big-endian** - anders als in ``export.pdb``. Texte
sind UTF-16BE.

Warum das hier wichtig ist
--------------------------
Diese Klasse ist die Grundlage fuer die Regel "keine unbekannten Daten
zerstoeren". Ein Abschnitt wird als **Rohbytes** gehalten, nicht als
gedeutete Felder. Wer nur die Cues aendert, baut genau diesen einen
Abschnitt neu; alle anderen wandern Byte fuer Byte unveraendert in die
neue Datei - auch die, deren Bedeutung niemand kennt.

Solange nichts geaendert wurde, liefert ``to_bytes()`` **exakt** die
eingelesenen Bytes zurueck. Das ist keine Behauptung, sondern in
``tests/test_rekordbox_anlz.py`` gegen jede Beispieldatei geprueft.

Quelle: crate-digger ``rekordbox_anlz.ksy`` (Deep Symmetry), gegengeprueft
gegen rekordcrate.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace
from pathlib import Path

#: Kennung am Dateianfang.
FILE_MAGIC = b"PMAI"
#: Laenge des Tag-Kopfs: Kennung + ``len_header`` + ``len_tag``.
TAG_HEADER_SIZE = 12
#: Offset von ``len_file`` im Dateikopf.
FILE_LENGTH_AT = 8

#: Obergrenze fuer die Zahl der Abschnitte - faengt eine kaputte Kette ab.
MAX_TAGS = 10_000


class AnlzFormatError(ValueError):
    """Datei ist keine gueltige ANLZ-Datei oder ist beschaedigt."""


@dataclass(frozen=True)
class AnlzTag:
    """Ein Abschnitt, vollstaendig als Rohbytes."""

    #: Vier Zeichen, z. B. ``"PQTZ"``. Nicht dekodierbare Kennungen
    #: werden als ``"?"``-Text gefuehrt, der Abschnitt bleibt trotzdem
    #: erhalten.
    fourcc: str
    #: Laenge des Abschnittskopfs. Ab hier beginnt der eigentliche Inhalt.
    len_header: int
    #: Gesamtlaenge des Abschnitts, Kopf eingerechnet.
    len_tag: int
    #: Der komplette Abschnitt, unveraendert - inklusive Kopf.
    data: bytes

    @property
    def content(self) -> bytes:
        """Der Nutzinhalt hinter dem Abschnittskopf."""
        return self.data[self.len_header:]

    @property
    def fields(self) -> bytes:
        """Die typabhaengigen Felder zwischen Tag-Kopf und Inhalt."""
        return self.data[TAG_HEADER_SIZE:self.len_header]

    def __len__(self) -> int:
        return len(self.data)


def build_tag(fourcc: str, len_header: int, payload: bytes) -> AnlzTag:
    """Einen Abschnitt aus Feldern und Inhalt zusammensetzen.

    ``payload`` ist alles hinter den zwoelf Byte Tag-Kopf, also die
    typabhaengigen Felder **und** der Inhalt. ``len_header`` sagt, wo
    darin der Inhalt beginnt.
    """
    if len(fourcc) != 4:
        raise ValueError(f"Abschnittskennung muss vier Zeichen haben: {fourcc!r}")
    len_tag = TAG_HEADER_SIZE + len(payload)
    data = (
        fourcc.encode("ascii")
        + struct.pack(">II", len_header, len_tag)
        + payload
    )
    return AnlzTag(
        fourcc=fourcc, len_header=len_header, len_tag=len_tag, data=data
    )


@dataclass(frozen=True)
class AnlzFile:
    """Eine eingelesene Analysedatei."""

    tags: tuple[AnlzTag, ...] = ()
    #: Der Dateikopf, unveraendert.
    header: bytes = b""
    #: Bytes hinter dem letzten vollstaendigen Abschnitt. In gesunden
    #: Dateien leer; wird trotzdem mitgeschrieben, damit nichts verloren
    #: geht.
    trailer: bytes = b""
    path: str = ""
    #: Ob seit dem Einlesen etwas geaendert wurde. Nur dann wird die
    #: Laengenangabe im Dateikopf angefasst.
    dirty: bool = False
    warnings: tuple[str, ...] = ()

    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, data: bytes, path: str = "") -> AnlzFile:
        """Datei aus ihren Rohbytes lesen.

        Raises:
            AnlzFormatError: Kennung fehlt oder der Kopf ist unbrauchbar.
                Ein *abgeschnittener* Abschnitt am Ende ist dagegen kein
                Fehler - er landet in ``trailer`` und in ``warnings``, und
                der Rest bleibt lesbar.
        """
        if len(data) < TAG_HEADER_SIZE:
            raise AnlzFormatError(
                f"Datei zu klein fuer einen ANLZ-Kopf ({len(data)} Byte)"
            )
        if data[:4] != FILE_MAGIC:
            raise AnlzFormatError(
                "Datei beginnt nicht mit PMAI - keine ANLZ-Analysedatei"
            )
        len_header, len_file = struct.unpack_from(">II", data, 4)
        if len_header < TAG_HEADER_SIZE or len_header > len(data):
            raise AnlzFormatError(
                f"unplausible Kopflaenge {len_header} bei {len(data)} Byte"
            )

        warnings: list[str] = []
        if len_file != len(data):
            warnings.append(
                f"Kopf meldet {len_file} Byte, Datei hat {len(data)}"
            )

        tags: list[AnlzTag] = []
        at = len_header
        for _ in range(MAX_TAGS):
            if at >= len(data):
                break
            if at + TAG_HEADER_SIZE > len(data):
                warnings.append(
                    f"abgeschnittener Abschnittskopf bei Byte {at}"
                )
                break
            fourcc = _fourcc(data[at:at + 4])
            tag_header, tag_len = struct.unpack_from(">II", data, at + 4)
            if tag_len < TAG_HEADER_SIZE or at + tag_len > len(data):
                warnings.append(
                    f"Abschnitt {fourcc} bei Byte {at} meldet Laenge "
                    f"{tag_len} und reicht ueber das Dateiende hinaus"
                )
                break
            if not TAG_HEADER_SIZE <= tag_header <= tag_len:
                warnings.append(
                    f"Abschnitt {fourcc} bei Byte {at} hat unplausible "
                    f"Kopflaenge {tag_header}"
                )
                break
            tags.append(
                AnlzTag(
                    fourcc=fourcc,
                    len_header=tag_header,
                    len_tag=tag_len,
                    data=data[at:at + tag_len],
                )
            )
            at += tag_len
        else:
            warnings.append(f"mehr als {MAX_TAGS} Abschnitte - Rest ignoriert")

        return cls(
            tags=tuple(tags),
            header=data[:len_header],
            trailer=data[at:],
            path=path,
            warnings=tuple(warnings),
        )

    @classmethod
    def read(cls, path: str | Path) -> AnlzFile:
        """Datei vom Datentraeger lesen.

        Raises:
            AnlzFormatError: Datei fehlt, ist nicht lesbar oder ungueltig.
        """
        file_path = Path(path)
        try:
            data = file_path.read_bytes()
        except OSError as error:
            raise AnlzFormatError(
                f"Analysedatei nicht lesbar: {error}"
            ) from error
        return cls.parse(data, path=str(file_path))

    # ------------------------------------------------------------------

    def to_bytes(self) -> bytes:
        """Die Datei wieder zu Bytes zusammensetzen.

        Ohne Aenderung ist das Ergebnis Byte fuer Byte die eingelesene
        Datei - auch dann, wenn deren Kopf eine falsche Laenge nennt. Erst
        wenn wirklich etwas geaendert wurde, wird ``len_file`` berichtigt.
        """
        body = (
            self.header
            + b"".join(tag.data for tag in self.tags)
            + self.trailer
        )
        if not self.dirty:
            return body
        patched = bytearray(body)
        struct.pack_into(">I", patched, FILE_LENGTH_AT, len(body))
        return bytes(patched)

    # ------------------------------------------------------------------

    def tag(self, fourcc: str) -> AnlzTag | None:
        """Der erste Abschnitt dieser Kennung."""
        for tag in self.tags:
            if tag.fourcc == fourcc:
                return tag
        return None

    def tags_of(self, fourcc: str) -> tuple[AnlzTag, ...]:
        """Alle Abschnitte dieser Kennung.

        Mehrzahl, weil es Cue-Listen zweimal gibt: einmal fuer Memory
        Cues, einmal fuer Hot Cues.
        """
        return tuple(tag for tag in self.tags if tag.fourcc == fourcc)

    @property
    def fourccs(self) -> tuple[str, ...]:
        return tuple(tag.fourcc for tag in self.tags)

    def replace_tag(self, old: AnlzTag, new: AnlzTag) -> AnlzFile:
        """Einen Abschnitt ersetzen; alle anderen bleiben unangetastet."""
        tags = list(self.tags)
        for index, tag in enumerate(tags):
            if tag is old:
                tags[index] = new
                break
        else:
            raise ValueError(
                f"Abschnitt {old.fourcc} gehoert nicht zu dieser Datei"
            )
        return replace(self, tags=tuple(tags), dirty=True)

    def append_tag(self, new: AnlzTag) -> AnlzFile:
        """Einen Abschnitt hinten anfuegen."""
        return replace(self, tags=self.tags + (new,), dirty=True)

    def remove_tag(self, old: AnlzTag) -> AnlzFile:
        tags = tuple(tag for tag in self.tags if tag is not old)
        if len(tags) == len(self.tags):
            raise ValueError(
                f"Abschnitt {old.fourcc} gehoert nicht zu dieser Datei"
            )
        return replace(self, tags=tags, dirty=True)


def _fourcc(raw: bytes) -> str:
    """Kennung als Text. Nicht druckbare Bytes werden sichtbar gemacht,
    damit eine kaputte Datei im Protokoll erkennbar ist."""
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return raw.hex().upper()
    if not text.isprintable():
        return raw.hex().upper()
    return text
