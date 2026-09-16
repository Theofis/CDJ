"""``TagListService`` - die Tag List je Datentraeger (Handbuch S. 40-41).

Die Tag List ist die Arbeitsliste des DJs: Tracks, die er waehrend des Sets
vormerkt. Am Geraet haengt sie **am Datentraeger**, nicht am Player - steckt
ein anderer Stick, ist es eine andere Tag List.

Was diese Schicht ist und was nicht
-----------------------------------
Sie fuehrt **nur Kennungen**, keine Tracks. Die Tracks selbst stehen
weiterhin in der einen Bibliothek des Datentraegers; die Tag List ist eine
Auswahl daraus. Deshalb entsteht hier auch keine zweite Trackliste, kein
zweiter Player-Zustand und keine zweite Ladefunktion - die Ansicht
"TAG LIST" ist eine Kategorie derselben ``TrackListLibrary`` wie TRACK,
ARTIST oder PLAYLIST.

Nur im Speicher
---------------
Nichts davon wird auf den Stick geschrieben. Das Entfernen eines Tracks aus
der Tag List entfernt einen Eintrag in dieser Liste - keine Audiodatei,
keine Playlist, keinen Datensatz in der rekordbox-Datenbank.

``CREATE PLAYLIST`` (Handbuch S. 41) braucht dagegen einen Schreibzugriff
auf den Stick. Der ist in diesem Projekt nicht implementiert, und es wird
auch nicht heimlich etwas anderes getan: ``create_playlist()`` fragt einen
``PlaylistWriter``, und ohne einen solchen meldet es ehrlich, dass die
Funktion nicht verfuegbar ist. Der Platz dafuer ist damit vorbereitet, ohne
dass eine Datenbank in Gefahr geraet.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

#: Obergrenze am Geraet (Handbuch S. 40).
MAX_TAG_LIST_TRACKS = 100

#: Namensschema fuer aus der Tag List erzeugte Playlists (S. 41).
PLAYLIST_NAME_PREFIX = "TAG LIST"
_PLAYLIST_NAME_PATTERN = re.compile(
    rf"^{re.escape(PLAYLIST_NAME_PREFIX)}\s+(\d+)$"
)


class TagListFull(RuntimeError):
    """Die Tag List ist voll - der Track wurde nicht aufgenommen."""


class PlaylistWriter(Protocol):
    """Schreibzugriff auf die Playlists eines Datentraegers.

    Absichtlich eine eigene, schmale Schnittstelle: solange es keine
    Implementierung gibt, ist auch kein Weg vorhanden, auf dem versehentlich
    auf den Stick geschrieben werden koennte. Eine spaetere Implementierung
    lebt neben ``rekordbox/reader.py`` und wird hier eingehaengt - der Rest
    des Programms aendert sich dadurch nicht.
    """

    def existing_playlist_names(self, device_id: str) -> Sequence[str]:
        """Bereits vorhandene Playlistnamen - fuer die Nummernvergabe."""
        ...

    def create_playlist(
        self, device_id: str, name: str, track_ids: Sequence[str]
    ) -> None:
        """Playlist anlegen. Darf scheitern; dann bleibt der Stick, wie er war."""
        ...


@dataclass(frozen=True)
class TagListResult:
    """Ergebnis einer Aenderung - fuer die Rueckmeldung auf dem Bildschirm."""

    #: Ob sich etwas geaendert hat.
    changed: bool = False
    #: Ob der Track danach in der Liste steht.
    tagged: bool = False
    #: Klartext, wenn etwas nicht ging. Leer heisst "in Ordnung".
    message: str = ""


class TagListService:
    """Die Tag Lists aller bekannten Datentraeger.

    Ein Eintrag je Datentraeger. Die Listen bleiben bestehen, solange das
    Programm laeuft - wird ein Stick abgezogen und wieder eingesteckt, ist
    seine Vormerkliste noch da, was dem Verhalten am Geraet entspricht.
    """

    def __init__(
        self,
        *,
        writer: PlaylistWriter | None = None,
        max_tracks: int = MAX_TAG_LIST_TRACKS,
    ) -> None:
        self._lists: dict[str, list[str]] = {}
        self._writer = writer
        self._max = max_tracks
        #: Wird nach jeder Aenderung gerufen (Datentraegerkennung).
        self.on_changed: list[Callable[[str], None]] = []

    # ------------------------------------------------------------------
    # Lesen
    # ------------------------------------------------------------------

    def track_ids(self, device_id: str) -> tuple[str, ...]:
        """Kennungen der Tag List, in der Reihenfolge des Hinzufuegens."""
        return tuple(self._lists.get(device_id, ()))

    def ids_for(self, device_id: str) -> Callable[[], tuple[str, ...]]:
        """Abfragefunktion fuer eine ``TrackListLibrary``.

        Die Bibliothek haelt keine Kopie, sondern fragt bei jedem Aufbau
        neu - so wirkt eine Aenderung sofort in jeder Ansicht.
        """
        return lambda: self.track_ids(device_id)

    def contains(self, device_id: str, track_id: str) -> bool:
        return track_id in self._lists.get(device_id, ())

    def count(self, device_id: str) -> int:
        return len(self._lists.get(device_id, ()))

    def is_full(self, device_id: str) -> bool:
        return self.count(device_id) >= self._max

    @property
    def max_tracks(self) -> int:
        return self._max

    # ------------------------------------------------------------------
    # Aendern
    # ------------------------------------------------------------------

    def add(self, device_id: str, track_id: str) -> TagListResult:
        """Track vormerken.

        Ein bereits vorgemerkter Track wird **nicht** ein zweites Mal
        aufgenommen und auch nicht ans Ende verschoben - die Reihenfolge
        bleibt, wie sie war.
        """
        if not device_id or not track_id:
            return TagListResult(message="kein Track markiert")
        entries = self._lists.setdefault(device_id, [])
        if track_id in entries:
            return TagListResult(tagged=True)
        if len(entries) >= self._max:
            return TagListResult(
                tagged=False,
                message=f"Tag List voll ({self._max} Tracks)",
            )
        entries.append(track_id)
        self._notify(device_id)
        return TagListResult(changed=True, tagged=True)

    def remove(self, device_id: str, track_id: str) -> TagListResult:
        """Einen Eintrag entfernen.

        Entfernt wird ausschliesslich der Eintrag in dieser Liste. Die
        Audiodatei, die Playlists und die rekordbox-Datenbank auf dem Stick
        bleiben unberuehrt - dieser Dienst kann sie gar nicht erreichen.
        """
        entries = self._lists.get(device_id)
        if not entries or track_id not in entries:
            return TagListResult()
        entries.remove(track_id)
        self._notify(device_id)
        return TagListResult(changed=True, tagged=False)

    def toggle(self, device_id: str, track_id: str) -> TagListResult:
        """TAG TRACK / REMOVE: drin -> raus, draussen -> rein (S. 40)."""
        if self.contains(device_id, track_id):
            return self.remove(device_id, track_id)
        return self.add(device_id, track_id)

    def clear(self, device_id: str) -> TagListResult:
        """``REMOVE ALL TRACKS`` (S. 41)."""
        entries = self._lists.get(device_id)
        if not entries:
            return TagListResult()
        count = len(entries)
        entries.clear()
        self._notify(device_id)
        return TagListResult(
            changed=True, message=f"{count} Eintraege entfernt"
        )

    def forget_device(self, device_id: str) -> None:
        """Die Liste eines Datentraegers vergessen.

        Wird beim normalen Abziehen **nicht** gerufen - die Vormerkliste
        soll ein versehentliches Herausziehen ueberleben. Gedacht fuer den
        ausdruecklichen Wunsch, sie zu verwerfen.
        """
        if self._lists.pop(device_id, None) is not None:
            self._notify(device_id)

    # ------------------------------------------------------------------
    # Playlist erzeugen - Schreibzugriff, gekapselt
    # ------------------------------------------------------------------

    @property
    def can_create_playlist(self) -> bool:
        """Ob Schreiben ueberhaupt moeglich ist. Ohne Writer: nein."""
        return self._writer is not None

    def next_playlist_name(self, device_id: str) -> str:
        """Naechster freier Name nach dem Schema ``TAG LIST 001`` (S. 41)."""
        used: set[int] = set()
        if self._writer is not None:
            for name in self._writer.existing_playlist_names(device_id):
                match = _PLAYLIST_NAME_PATTERN.match(name.strip())
                if match:
                    used.add(int(match.group(1)))
        number = 1
        while number in used:
            number += 1
        return f"{PLAYLIST_NAME_PREFIX} {number:03d}"

    def create_playlist(self, device_id: str) -> TagListResult:
        """Aus der Tag List eine Playlist auf dem Datentraeger machen.

        Ohne Schreib-Layer passiert **nichts** - es wird gemeldet, dass die
        Funktion nicht verfuegbar ist. Es wird nicht ersatzweise eine
        Datei angelegt, nichts umbenannt und nichts in die
        rekordbox-Datenbank geschrieben.
        """
        entries = self.track_ids(device_id)
        if not entries:
            return TagListResult(message="Tag List ist leer")
        if self._writer is None:
            return TagListResult(
                message="CREATE PLAYLIST nicht verfuegbar - "
                        "die Bibliothek ist nur lesend geoeffnet",
            )
        name = self.next_playlist_name(device_id)
        try:
            self._writer.create_playlist(device_id, name, entries)
        except Exception as error:  # pragma: no cover - kein Writer vorhanden
            log.exception("Playlist %s konnte nicht angelegt werden", name)
            return TagListResult(message=f"Fehlgeschlagen: {error}")
        return TagListResult(changed=True, message=f"{name} angelegt")

    # ------------------------------------------------------------------

    def _notify(self, device_id: str) -> None:
        for hook in self.on_changed:
            try:
                hook(device_id)
            except Exception:  # pragma: no cover - defekter Zuhoerer
                log.exception("Tag-List-Rueckruf fehlgeschlagen")


__all__ = [
    "MAX_TAG_LIST_TRACKS",
    "PLAYLIST_NAME_PREFIX",
    "PlaylistWriter",
    "TagListFull",
    "TagListResult",
    "TagListService",
]
