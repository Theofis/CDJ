"""Datei-Tags lesen - ausserhalb des kritischen Analysepfads.

Warum es dieses Modul gibt
--------------------------
Das Auslesen der Tags ueber ``mutagen`` hat den ganzen Python-Prozess
mitgenommen: ``Windows fatal exception: code 0x80000003``, ausgeloest im
Thread ``analysis-worker``, waehrend die Garbage Collection mitten in
``mutagen.wave._pre_load_header`` lief. Gemessen: mit aktivem Tag-Lesen
brachen 5 von 6 Gesamtlaeufen der Testsuite ab, mit uebersprungenem
Tag-Lesen keiner von zwei.

Ein solcher Absturz ist **kein** Python-Fehler. ``try/except`` faengt ihn
nicht, denn der Interpreter kommt nie bis zum ``except``. Deshalb reicht
es nicht, den Aufruf abzusichern; er darf schlicht nicht im selben Prozess
laufen wie die Analyse.

Der Aufbau folgt daraus
-----------------------
1. **Trennung.** Audioanalyse (Waveform, Peaks, BPM, Tonart) und
   Tag-Lesen sind zwei Schritte. Der zweite darf ausfallen, ohne den
   ersten mitzunehmen.
2. **Vorrang vorhandener Daten.** Was aus rekordbox (``export.pdb``) oder
   der Bibliothek schon bekannt ist, wird nicht noch einmal aus der Datei
   geholt. Fuer Tracks vom USB-Stick laeuft ``mutagen`` damit ueberhaupt
   nicht.
3. **Prozessisolation.** Bleibt doch etwas zu lesen, passiert das in einem
   kurzlebigen eigenen Prozess. Stirbt der, ist genau ein Tag-Lesen
   fehlgeschlagen - der Player laeuft weiter.

Kein Prozess-Pool, keine neue Auftragsverwaltung: ein ``subprocess.run``
mit einem kleinen Skript, JSON zurueck, fertig. Gemessen rund 135 ms je
Aufruf; er laeuft im Worker-Thread, nie in der Oberflaeche oder im
Audio-Thread, und ``TrackLoader`` merkt sich das Ergebnis je Datei.

**Nicht** im Hauptthread. Dort waere derselbe Absturz nur schlimmer: er
wuerde die Oberflaeche und die laufende Wiedergabe mitnehmen.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass, fields, replace
from pathlib import Path

log = logging.getLogger(__name__)

#: Zeitgrenze fuer den Leseprozess. Grosszuegig - er soll nur nicht
#: ewig haengen, wenn eine Datei auf einem langsamen Datentraeger liegt.
READ_TIMEOUT_S = 10.0

#: Das Skript des Leseprozesses. Absichtlich winzig: es importiert das
#: Projekt **nicht**, nur ``mutagen`` und die Standardbibliothek. Damit
#: bleibt der Prozessstart billig, und ein Absturz kann nichts anderes
#: beschaedigen.
_READER_SOURCE = """
import json, sys
try:
    import mutagen
except Exception:
    print("{}")
    raise SystemExit(0)
handle = None
try:
    handle = mutagen.File(sys.argv[1], easy=True)
except Exception:
    pass
if handle is None:
    print("{}")
    raise SystemExit(0)


def first(*keys):
    for key in keys:
        try:
            value = handle.get(key)
        except Exception:
            continue
        if value:
            item = value[0] if isinstance(value, list) else value
            return str(item)
    return ""


print(json.dumps({
    "title": first("title"),
    "artist": first("artist", "albumartist"),
    "album": first("album"),
    "genre": first("genre"),
    "label": first("organization", "label"),
}))
"""


@dataclass(frozen=True)
class TrackTags:
    """Die Metadatenfelder, die aus einer Datei kommen koennen.

    Leer heisst leer - es wird nie ein Wert erfunden. Die Anzeige setzt
    dafuer einen Gedankenstrich.
    """

    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    label: str = ""

    @property
    def is_empty(self) -> bool:
        return not any(
            getattr(self, field.name) for field in fields(self)
        )

    @property
    def is_complete(self) -> bool:
        """Ob jedes Feld belegt ist - dann ist nichts mehr nachzulesen."""
        return all(
            getattr(self, field.name) for field in fields(self)
        )

    def filled_with(self, other: TrackTags) -> TrackTags:
        """Leere Felder aus ``other`` ergaenzen.

        ``self`` gewinnt immer. So kann ein schwaecherer Dateitag einen
        bereits bekannten rekordbox-Wert nicht ueberschreiben.
        """
        return replace(self, **{
            field.name: getattr(self, field.name) or getattr(other, field.name)
            for field in fields(self)
        })

    @classmethod
    def from_mapping(cls, data: object) -> TrackTags:
        """Aus dem JSON des Leseprozesses. Unbekanntes wird verworfen."""
        if not isinstance(data, dict):
            return cls()
        names = {field.name for field in fields(cls)}
        return cls(**{
            key: str(value)
            for key, value in data.items()
            if key in names and isinstance(value, (str, int, float))
        })


@dataclass(frozen=True)
class TagReadResult:
    """Ergebnis eines Lesevorgangs. ``error`` leer heisst: hat geklappt."""

    tags: TrackTags = TrackTags()
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def read_tags(
    path: str | Path,
    *,
    timeout_s: float = READ_TIMEOUT_S,
    isolated: bool = True,
) -> TagReadResult:
    """Tags einer Datei lesen. Wirft nie.

    ``isolated=False`` liest im eigenen Prozess - nur fuer Tests und
    Diagnose. Im Betrieb bleibt es bei ``True``: genau dieser Aufruf hat
    den Prozess mitgenommen.
    """
    source = Path(path)
    if not source.exists():
        return TagReadResult(error=f"Datei nicht vorhanden: {source}")
    if isolated:
        return _read_isolated(source, timeout_s=timeout_s)
    return _read_in_process(source)


def _read_isolated(source: Path, *, timeout_s: float) -> TagReadResult:
    """Im eigenen Prozess lesen - ein Absturz bleibt dort."""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _READER_SOURCE, str(source)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            # Kein Konsolenfenster auf Windows, wenn die Anwendung
            # ohne Terminal laeuft.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return TagReadResult(
            error=f"Tag-Lesen ueberschritt {timeout_s:.0f} s: {source.name}"
        )
    except OSError as error:
        return TagReadResult(error=f"Leseprozess nicht startbar: {error}")

    if completed.returncode != 0:
        # Genau der Fall, um den es hier geht: der Leseprozess ist
        # gestorben. Nur das Tag-Lesen gilt als gescheitert.
        detail = (completed.stderr or "").strip().splitlines()
        return TagReadResult(error=(
            f"Leseprozess beendet mit {completed.returncode}"
            + (f": {detail[-1]}" if detail else "")
        ))

    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as error:
        return TagReadResult(error=f"Antwort nicht lesbar: {error}")
    return TagReadResult(tags=TrackTags.from_mapping(data))


def _read_in_process(source: Path) -> TagReadResult:
    """Im selben Prozess lesen. Nur fuer Tests und Diagnose.

    Faengt Python-Fehler ab. Gegen einen Absturz auf Interpreter-Ebene
    hilft das nicht - deshalb ist es nicht der Betriebsweg.
    """
    try:
        import mutagen
    except ImportError:
        return TagReadResult(error="mutagen nicht installiert")
    try:
        handle = mutagen.File(str(source), easy=True)
    except Exception as error:
        return TagReadResult(error=f"{type(error).__name__}: {error}")
    if handle is None:
        return TagReadResult()

    def first(*keys: str) -> str:
        for key in keys:
            value = handle.get(key)
            if value:
                item = value[0] if isinstance(value, list) else value
                return str(item)
        return ""

    return TagReadResult(tags=TrackTags(
        title=first("title"),
        artist=first("artist", "albumartist"),
        album=first("album"),
        genre=first("genre"),
        label=first("organization", "label"),
    ))


__all__ = [
    "READ_TIMEOUT_S",
    "TagReadResult",
    "TrackTags",
    "read_tags",
]
