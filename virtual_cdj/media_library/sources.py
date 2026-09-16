"""Brücke: erkannter Datentraeger -> vorhandenes Browse-Modell.

Hier trifft ``media_library`` (Datentraeger, Rekordbox) auf ``deck.library``
(SOURCE und BROWSE). Es entsteht **keine** zweite Architektur: das Ergebnis
ist dieselbe ``TrackListLibrary`` mit denselben ``TrackInfo``-Objekten, die
auch eine lokal geladene Datei erzeugt. Der Browser merkt nicht, woher ein
Track kommt.

    MediaDevice            -> SourceInfo        (was SOURCE anzeigt)
    DeviceLibrary.tracks   -> TrackInfo         (was BROWSE anzeigt)
    DeviceLibrary.playlists-> Playlist-Zuordnung

Was hier noch **nicht** uebersetzt wird
---------------------------------------
``TrackInfo.waveform``, ``beat_grid``, ``hot_cues`` und ``memory_cues``
bleiben leer. Diese Daten stehen in den ``ANLZ*``-Dateien, deren Lesen an
einem echten Stick ~77 ms je Track kostet - sie werden je Track nachgeladen
(naechster Schritt), nicht hier fuer die ganze Bibliothek auf einmal. Leer
heisst hier ausdruecklich "noch nicht geladen", nicht "nicht vorhanden";
es wird nichts geschaetzt.

Playlist-Ordner
---------------
``TrackListLibrary`` kennt zurzeit nur **eine** Playlist-Ebene
(Name -> Track-IDs), Rekordbox erlaubt beliebig tiefe Ordner. Bis die
Hierarchie im Browse-Modell ankommt, wird der Baum hier mit ``" / "``
zwischen den Ebenen zu eindeutigen Namen abgeflacht. Das Trennzeichen hat
absichtlich Leerzeichen: Playlist-Namen duerfen selbst einen Schraegstrich
enthalten (auf dem Teststick gibt es ``"Hardtechno/Industrial"``), und ein
nacktes ``"/"`` waere dort nicht mehr unterscheidbar.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from ..deck.library import SourceInfo, SourceKind, TrackListLibrary
from ..deck.state import TrackInfo
from .devices import MediaDevice
from .model import DeviceLibrary, LibraryTrack, PlaylistNode

#: Trennzeichen zwischen Playlist-Ordner und Playlist in der abgeflachten
#: Darstellung. Siehe Modulkopf.
PLAYLIST_SEPARATOR = " / "

#: Kennzeichnet die Quelle eines Tracks in ``TrackInfo.source``.
USB_SOURCE_NAME = "USB"


def track_uid(volume_id: str, track_id: int) -> str:
    """Trackkennung, eindeutig **ueber Datentraeger hinweg**.

    Zwei rekordbox-Sticks vergeben unabhaengig voneinander Track-IDs ab 1.
    Ohne den Datentraeger im Schluessel wuerde beim Wechsel zwischen zwei
    Sticks der falsche Track gefunden.
    """
    return f"rb:{volume_id or '?'}:{track_id}"


def on_device_path(root_path: str, stored_path: str) -> str:
    """Rekordbox-Pfad in einen Pfad auf diesem Datentraeger uebersetzen.

    In der Datenbank stehen Pfade relativ zur Wurzel des Sticks und mit
    Schraegstrichen, z. B. ``"/Contents/Artist/Album/song.mp3"``. Welchen
    Laufwerksbuchstaben der Stick gerade hat, steht nirgends in der Datei -
    er kommt von der Datentraegererkennung. Genau deshalb darf kein
    Laufwerksbuchstabe fest angenommen werden.

    Ein leerer Eintrag bleibt leer; daraus wird kein Pfad erfunden.
    """
    if not stored_path:
        return ""
    if not root_path:
        return stored_path
    return str(Path(root_path) / stored_path.lstrip("/\\").replace("/", "\\"))


def to_track_info(
    track: LibraryTrack, *, volume_id: str, root_path: str = ""
) -> TrackInfo:
    """Einen ``LibraryTrack`` in das Deck-Modell uebersetzen.

    Nur Metadaten. Felder, zu denen die Datenbank nichts sagt, bleiben leer
    bzw. ``0`` - die Oberflaeche zeigt dafuer einen Gedankenstrich.
    ``waveform``, ``beat_grid`` und die Cues bleiben leer: die stehen in den
    ANLZ-Dateien und werden je Track nachgeladen.
    """
    return TrackInfo(
        track_id=track_uid(volume_id, track.id),
        title=track.title,
        artist=track.artist,
        album=track.album,
        genre=track.genre,
        label=track.label,
        duration_s=track.duration_s,
        original_bpm=track.bpm,
        key=track.key,
        rating=track.rating,
        file_path=on_device_path(root_path, track.file_path),
        artwork_path=on_device_path(root_path, track.artwork_path),
        source=USB_SOURCE_NAME,
        color=track.color,
    )


def flatten_playlists(
    roots: Sequence[PlaylistNode], *, volume_id: str
) -> dict[str, tuple[str, ...]]:
    """Playlist-Baum zu ``Name -> Track-IDs`` abflachen.

    Ordner werden zu Namensteilen, die Reihenfolge innerhalb einer Playlist
    bleibt die der Datenbank. Gleichnamige Playlists in verschiedenen
    Ordnern bleiben durch den vorangestellten Ordnernamen unterscheidbar.
    """
    flat: dict[str, tuple[str, ...]] = {}

    def walk(node: PlaylistNode, prefix: str) -> None:
        name = f"{prefix}{PLAYLIST_SEPARATOR}{node.name}" if prefix else node.name
        if node.is_folder:
            for child in node.children:
                walk(child, name)
            return
        # Zwei Playlists mit identischem vollem Pfad kann rekordbox nicht
        # anlegen; sollte es sie doch geben, gewinnt nicht stillschweigend
        # die letzte - die zweite bekommt ihre ID angehaengt.
        key = name if name not in flat else f"{name} #{node.id}"
        flat[key] = tuple(
            track_uid(volume_id, track_id) for track_id in node.track_ids
        )

    for root in roots:
        walk(root, "")
    return flat


def build_source_info(device: MediaDevice) -> SourceInfo:
    """Die Geraeteinformation, die SOURCE anzeigt (Handbuch S. 18).

    ``songs`` und ``playlists`` setzt ``TrackListLibrary.info`` spaeter aus
    der echten Liste - hier stehen sie nur fuer den Fall, dass die Quelle
    ohne Bibliothek gebaut wird.
    """
    volume = device.volume
    return SourceInfo(
        source_id=device.source_id,
        name=device.name,
        # Ein festes Laufwerk mit rekordbox-Struktur ist eine gueltige
        # Quelle, aber kein USB-Stick - es bekommt deshalb auch nicht
        # dessen Symbol (S. 18).
        kind=SourceKind.USB if volume.is_removable else SourceKind.FILE,
        songs=device.track_count,
        playlists=device.playlist_count,
        total_mb=volume.total_mb,
        available_mb=volume.free_mb,
        has_library=device.status.has_library,
        status=device.status_text,
        library_format=device.format_text,
        filesystem=volume.filesystem,
        warning=device.warning,
        note=volume.root_path,
    )


def build_library(
    device: MediaDevice,
    *,
    tag_list: Callable[[], Sequence[str]] | None = None,
) -> TrackListLibrary:
    """Eine Browse-Quelle aus einem erkannten Datentraeger.

    Funktioniert in jedem Zustand: ohne gelesene Bibliothek entsteht eine
    leere Quelle, die in SOURCE trotzdem mit Namen, Format und Zustand
    erscheint - ein erkannter, aber unlesbarer Stick soll sichtbar sein und
    nicht einfach fehlen.

    ``tag_list`` liefert die Kennungen der Tag List dieses Datentraegers.
    Die Liste selbst fuehrt der ``TagListService``; sie wird hier nur
    angeschlossen, damit die Kategorie "TAG LIST" dieselbe Bibliothek und
    dieselben Tracks benutzt wie jede andere Ansicht.
    """
    library = device.library
    if library is None:
        return TrackListLibrary(
            build_source_info(device), lambda: (), tag_list=tag_list
        )

    volume_id = library.volume_id or device.device_id
    root_path = library.root_path or device.volume.root_path
    tracks = tuple(
        to_track_info(track, volume_id=volume_id, root_path=root_path)
        for track in library.tracks
    )
    playlists = flatten_playlists(library.playlists, volume_id=volume_id)
    history = _history_ids(library, volume_id=volume_id)

    return TrackListLibrary(
        build_source_info(device),
        lambda: tracks,
        playlists=lambda: playlists,
        history=lambda: history,
        tag_list=tag_list,
    )


def _history_ids(
    library: DeviceLibrary, *, volume_id: str
) -> tuple[str, ...]:
    """Verlauf des Players, aelteste Wiedergabe zuerst.

    ``TrackListLibrary`` erwartet eine flache Liste in Abspielreihenfolge
    und dreht sie fuer die Anzeige selbst um (S. 42).
    """
    seen: set[str] = set()
    out: list[str] = []
    for playlist in library.history:
        for track_id in playlist.track_ids:
            uid = track_uid(volume_id, track_id)
            if uid not in seen:
                seen.add(uid)
                out.append(uid)
    return tuple(out)


__all__ = [
    "PLAYLIST_SEPARATOR",
    "USB_SOURCE_NAME",
    "build_library",
    "build_source_info",
    "flatten_playlists",
    "on_device_path",
    "to_track_info",
    "track_uid",
]
