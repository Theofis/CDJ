"""``RekordboxLibraryReader`` - ``export.pdb`` in das interne Modell.

Setzt die Zeilen aus ``pdb_file`` zu ``DeviceLibrary`` / ``LibraryTrack`` /
``PlaylistNode`` zusammen (``media_library/model.py``). Ab hier kennt die
uebrige Software kein Rekordbox-Detail mehr.

Nur lesend
----------
Die Datei wird ausschliesslich mit ``read_bytes()`` geoeffnet. Es gibt in
diesem Modul keinen Schreibpfad, keine Migration und keine Reparatur einer
beschaedigten Datenbank - eine unlesbare Zeile wird uebersprungen und in
``warnings`` vermerkt, der Datentraeger bleibt unveraendert.

Was hier **nicht** passiert
---------------------------
Die Analysedateien (``ANLZ*``: Beatgrid, Cues, Waveform) werden hier
bewusst nicht gelesen. Gemessen an einem echten Stick mit 658 Tracks:

    export.pdb komplett          0,04 s
    alle ANLZ-Dateien dazu      50,5 s   (~77 ms je Track)

Die Datenbank kann deshalb beim Einstecken vollstaendig gelesen werden;
die Analysedaten muessen je Track nachgeladen werden. ``LibraryTrack``
bleibt hier also ohne ``beat_grid``/``waveforms``/Cues - und zwar leer,
nicht mit Ersatzwerten gefuellt.

Aufloesung der Nachbartabellen
------------------------------
Interpret, Album, Genre, Label, Tonart und Farbe stehen in der Track-Zeile
nur als ID. Fehlt die ID (``0``) oder der Eintrag in der Nachbartabelle,
bleibt das Feld **leer**. Es wird nichts geraten und nichts aus dem
Dateinamen abgeleitet.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from ..model import DeviceLibrary, LibraryTrack, PlaylistNode
from .pdb_file import PdbDatabase
from .pdb_header import PdbFormatError
from .pdb_rows import PlaylistEntryRow, PlaylistTreeRow, TrackRow

log = logging.getLogger(__name__)

#: Wurzel-Kennung des Playlist-Baums: Knoten mit diesem ``parent_id``
#: haengen direkt unter der obersten Ebene.
ROOT_PARENT_ID = 0

#: Obergrenze fuer die Tiefe des Playlist-Baums. Rekordbox erlaubt beliebig
#: tiefe Ordner; die Grenze faengt nur eine im Kreis zeigende ``parent_id``
#: aus einer beschaedigten Datei ab.
MAX_PLAYLIST_DEPTH = 32


class RekordboxLibraryReader:
    """Liest eine ``export.pdb`` in eine ``DeviceLibrary``.

    Der Leser haelt keine offene Datei und keine Verbindung: ``read()``
    oeffnet, liest vollstaendig in den Speicher und schliesst wieder. Damit
    gibt es nichts, was beim Abziehen des Sticks haengen bleiben koennte.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        root_path: str = "",
        volume_id: str = "",
        label: str = "",
    ) -> None:
        self.database_path = Path(database_path)
        self.root_path = root_path
        self.volume_id = volume_id
        self.label = label

    # ------------------------------------------------------------------

    def read(self) -> DeviceLibrary:
        """Datenbank lesen.

        Raises:
            PdbFormatError: Die Datei fehlt, ist zu klein oder hat keinen
                gueltigen Kopf. Alles Feinere (einzelne kaputte Zeilen,
                fehlende Nachbartabellen) landet in ``warnings``, damit ein
                einzelner Defekt nicht die ganze Bibliothek kostet.
        """
        database = PdbDatabase.open(self.database_path)

        artists = database.artists()
        albums = database.albums()
        genres = database.genres()
        labels = database.labels()
        keys = database.keys()
        colors = database.colors()
        artwork = database.artwork()

        rows = database.tracks()
        tracks = tuple(
            self._build_track(
                row,
                artists=artists,
                albums=albums,
                genres=genres,
                labels=labels,
                keys=keys,
                colors=colors,
                artwork=artwork,
            )
            for row in rows
        )

        playlists = self._build_playlists(
            database.playlist_tree(), database.playlist_entries()
        )
        history = self._build_history(
            database.history_playlists(), database.history_entries()
        )

        warnings = tuple(database.warnings)
        log.info(
            "%s gelesen: %d Tracks, %d Playlists, %d Verlaufslisten%s",
            self.database_path,
            len(tracks),
            sum(1 for root in playlists for n in root.walk() if not n.is_folder),
            len(history),
            f", {len(warnings)} Hinweis(e)" if warnings else "",
        )
        return DeviceLibrary(
            root_path=self.root_path,
            volume_id=self.volume_id,
            label=self.label,
            tracks=tracks,
            playlists=playlists,
            history=history,
            warnings=warnings,
            read_only=True,
            read_only_reason=(
                "Schreibzugriff ist in dieser Ausbaustufe nicht vorgesehen"
            ),
        )

    # ------------------------------------------------------------------
    # Tracks
    # ------------------------------------------------------------------

    def _build_track(
        self,
        row: TrackRow,
        *,
        artists: dict[int, str],
        albums: dict[int, str],
        genres: dict[int, str],
        labels: dict[int, str],
        keys: dict[int, str],
        colors: dict[int, str],
        artwork: dict[int, str],
    ) -> LibraryTrack:
        """Eine Track-Zeile in einen ``LibraryTrack`` uebersetzen.

        Alle Verweise werden nachgeschlagen; ein nicht aufloesbarer Verweis
        ergibt ein leeres Feld, keinen Platzhaltertext.
        """
        return LibraryTrack(
            id=row.id,
            file_path=row.file_path,
            file_name=row.file_name,
            title=row.title,
            artist=artists.get(row.artist_id, ""),
            album=albums.get(row.album_id, ""),
            genre=genres.get(row.genre_id, ""),
            label=labels.get(row.label_id, ""),
            remixer=artists.get(row.remixer_id, ""),
            composer=artists.get(row.composer_id, ""),
            original_artist=artists.get(row.original_artist_id, ""),
            comment=row.comment,
            bpm=row.bpm,
            key=keys.get(row.key_id, ""),
            rating=row.rating if 0 <= row.rating <= 5 else 0,
            color=colors.get(row.color_id, ""),
            color_id=row.color_id,
            duration_s=float(row.duration_s),
            year=row.year,
            track_number=row.track_number,
            disc_number=row.disc_number,
            play_count=row.play_count,
            bitrate=row.bitrate,
            sample_rate=row.sample_rate,
            sample_depth=row.sample_depth,
            file_size=row.file_size,
            file_type=row.file_type,
            date_added=row.date_added,
            release_date=row.release_date,
            analyze_date=row.analyze_date,
            mix_name=row.mix_name,
            isrc=row.isrc,
            artwork_path=artwork.get(row.artwork_id, ""),
            analyze_path=row.analyze_path,
            autoload_hot_cues=row.autoload_hot_cues,
        )

    # ------------------------------------------------------------------
    # Playlists
    # ------------------------------------------------------------------

    def _build_playlists(
        self,
        tree: Sequence[PlaylistTreeRow],
        entries: Sequence[PlaylistEntryRow],
    ) -> tuple[PlaylistNode, ...]:
        """Den Playlist-Baum aufbauen - beliebig tief, Reihenfolge erhalten.

        Die Track-Reihenfolge einer Playlist ist nicht die Reihenfolge der
        Zeilen in der Datenbank, sondern ``entry_index``. Sie wird
        uebernommen, wie sie dort steht (Aufgabe: "originale
        Trackreihenfolge").
        """
        by_playlist: dict[int, list[PlaylistEntryRow]] = {}
        for entry in entries:
            by_playlist.setdefault(entry.playlist_id, []).append(entry)

        children: dict[int, list[PlaylistTreeRow]] = {}
        for row in tree:
            children.setdefault(row.parent_id, []).append(row)

        seen: set[int] = set()

        def build(row: PlaylistTreeRow, depth: int) -> PlaylistNode:
            seen.add(row.id)
            sub: tuple[PlaylistNode, ...] = ()
            if row.is_folder and depth < MAX_PLAYLIST_DEPTH:
                sub = tuple(
                    build(child, depth + 1)
                    for child in sorted(
                        children.get(row.id, ()), key=lambda r: r.sort_order
                    )
                    # Schleifensicherung: eine beschaedigte ``parent_id``
                    # darf keinen unendlichen Baum erzeugen.
                    if child.id not in seen
                )
            track_ids: tuple[int, ...] = ()
            if not row.is_folder:
                ordered = sorted(
                    by_playlist.get(row.id, ()), key=lambda e: e.entry_index
                )
                track_ids = tuple(entry.track_id for entry in ordered)
            return PlaylistNode(
                id=row.id,
                name=row.name,
                is_folder=row.is_folder,
                sort_order=row.sort_order,
                parent_id=row.parent_id,
                children=sub,
                track_ids=track_ids,
            )

        roots = sorted(
            children.get(ROOT_PARENT_ID, ()), key=lambda r: r.sort_order
        )
        return tuple(build(row, 0) for row in roots)

    def _build_history(
        self,
        playlists: dict[int, str],
        entries: Sequence[PlaylistEntryRow],
    ) -> tuple[PlaylistNode, ...]:
        """Verlaufslisten - flache Listen, kein Baum."""
        by_playlist: dict[int, list[PlaylistEntryRow]] = {}
        for entry in entries:
            by_playlist.setdefault(entry.playlist_id, []).append(entry)
        return tuple(
            PlaylistNode(
                id=playlist_id,
                name=name,
                is_folder=False,
                track_ids=tuple(
                    entry.track_id
                    for entry in sorted(
                        by_playlist.get(playlist_id, ()),
                        key=lambda e: e.entry_index,
                    )
                ),
            )
            for playlist_id, name in sorted(playlists.items())
        )


def read_library(
    database_path: str | Path,
    *,
    root_path: str = "",
    volume_id: str = "",
    label: str = "",
) -> DeviceLibrary:
    """Kurzform fuer einen einmaligen Lesevorgang.

    Raises:
        PdbFormatError: siehe ``RekordboxLibraryReader.read``.
    """
    return RekordboxLibraryReader(
        database_path,
        root_path=root_path,
        volume_id=volume_id,
        label=label,
    ).read()


__all__ = [
    "MAX_PLAYLIST_DEPTH",
    "PdbFormatError",
    "RekordboxLibraryReader",
    "read_library",
]
