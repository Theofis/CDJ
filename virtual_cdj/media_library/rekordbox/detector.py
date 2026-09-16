"""Erkennt eine Rekordbox-Exportstruktur auf einem Datentraeger.

Untersucht nur das Dateisystem und den Dateikopf von ``export.pdb`` - liest
keine Tabelleninhalte. Das ist Phase 1 ("USB erkennen und Rekordbox-Struktur
untersuchen", siehe docs/rekordbox-usb-import.md). Phase 2 baut auf
``DetectedMedium.database_path`` auf und liest Tracks und Playlists wirklich.

Bekannte Struktur eines Rekordbox-USB-Exports (siehe Dokumentation)::

    <Wurzel>/
        PIONEER/
            rekordbox/
                export.pdb          - DeviceSQL-Datenbank (Legacy, gelesen)
                exportLibrary.db     - "Device Library Plus", SQLCipher-
                                        verschluesselte SQLite-Datenbank
                                        neuerer rekordbox-Versionen (erkannt,
                                        noch nicht gelesen)
            USBANLZ/
                <Ordner>/<Unterordner>/ANLZ0000.DAT   - je Track: Beatgrid,
                ANLZ0000.EXT                             Cues, Waveform
                ANLZ0000.2EX
            DEVSETTING.DAT, MYSETTING.DAT, ...   - Geraeteeinstellungen,
                                                    fuer diesen Import ohne
                                                    Bedeutung
        CONTENTS/...   - ueblicher, aber nicht garantierter Ablageort der
                          Audiodateien; der tatsaechliche Pfad steht in
                          jeder Track-Zeile selbst (Phase 2)

Beide Datenbankformate koennen gleichzeitig auf demselben Stick liegen. Ist
``export.pdb`` vorhanden und gueltig, wird sie bevorzugt, weil dieses Projekt
sie tatsaechlich lesen kann.
"""

from __future__ import annotations

from pathlib import Path

from ..provider import DetectedMedium, LibraryFormat
from ..volumes import VolumeInfo
from .pdb_header import PdbFormatError, parse_header

#: Pfade relativ zur Wurzel eines Datentraegers.
LEGACY_DB_RELATIVE = Path("PIONEER") / "rekordbox" / "export.pdb"
DEVICE_LIBRARY_PLUS_RELATIVE = Path("PIONEER") / "rekordbox" / "exportLibrary.db"
ANALYSIS_DIR_RELATIVE = Path("PIONEER") / "USBANLZ"

#: Erste 16 Byte einer unverschluesselten SQLite-Datenbank. Falls
#: ``exportLibrary.db`` damit beginnt, ist sie entgegen der Dokumentation
#: *nicht* SQLCipher-verschluesselt - wird als Hinweis vermerkt statt
#: stillschweigend angenommen.
_SQLITE_MAGIC = b"SQLite format 3\x00"


def detect_rekordbox(volume: VolumeInfo) -> DetectedMedium:
    """Datentraeger auf eine Rekordbox-Exportstruktur untersuchen.

    Liest hoechstens den Dateikopf einer gefundenen Datenbank - niemals die
    komplette Datei, niemals Zeileninhalte. Wirft nie eine Ausnahme: jedes
    Ergebnis ist ein ``DetectedMedium``, im Zweifel mit
    ``format=LibraryFormat.UNKNOWN`` und einer erklaerenden ``note``
    (Abschnitt 17, Fehlerbehandlung).
    """
    root = Path(volume.root_path)
    base = dict(
        root_path=volume.root_path,
        volume_id=volume.serial,
        label=volume.label,
    )

    legacy_path = root / LEGACY_DB_RELATIVE
    plus_path = root / DEVICE_LIBRARY_PLUS_RELATIVE
    analysis_dir = root / ANALYSIS_DIR_RELATIVE
    has_analysis_dir = analysis_dir.is_dir()

    plus_note = ""
    if plus_path.is_file():
        plus_note = _describe_device_library_plus(plus_path)

    if legacy_path.is_file():
        return _detect_legacy(
            legacy_path,
            has_analysis_dir=has_analysis_dir,
            extra_note=plus_note,
            **base,
        )

    if plus_path.is_file():
        return DetectedMedium(
            **base,
            format=LibraryFormat.REKORDBOX_DEVICE_LIBRARY_PLUS,
            database_path=str(plus_path),
            has_analysis_dir=has_analysis_dir,
            note=plus_note,
        )

    return DetectedMedium(**base, format=LibraryFormat.NONE, note="")


def _detect_legacy(
    legacy_path: Path,
    *,
    has_analysis_dir: bool,
    extra_note: str,
    **base: object,
) -> DetectedMedium:
    try:
        header = parse_header(legacy_path)
    except PdbFormatError as error:
        return DetectedMedium(
            **base,  # type: ignore[arg-type]
            format=LibraryFormat.UNKNOWN,
            database_path=str(legacy_path),
            note=f"export.pdb gefunden, aber Kopf ungueltig: {error}",
        )

    notes = []
    if not has_analysis_dir:
        notes.append(
            "PIONEER/USBANLZ fehlt - Beatgrid/Waveform/Cues aus den "
            "ANLZ-Dateien werden fehlen, Fallback-Analyse noetig"
        )
    if header.num_tables == 0:
        notes.append("export.pdb enthaelt keine Tabellen")
    if extra_note:
        notes.append(extra_note)

    return DetectedMedium(
        **base,  # type: ignore[arg-type]
        format=LibraryFormat.REKORDBOX_LEGACY,
        database_path=str(legacy_path),
        has_analysis_dir=has_analysis_dir,
        note="; ".join(notes),
    )


def _describe_device_library_plus(plus_path: Path) -> str:
    try:
        head = plus_path.read_bytes()[: len(_SQLITE_MAGIC)]
    except OSError:
        return "exportLibrary.db (Device Library Plus) vorhanden, nicht lesbar"
    if head == _SQLITE_MAGIC:
        return (
            "exportLibrary.db (Device Library Plus) vorhanden und "
            "unverschluesselt lesbar - Lesen dieses Formats noch nicht "
            "implementiert"
        )
    return (
        "exportLibrary.db (Device Library Plus, SQLCipher-verschluesselt) "
        "vorhanden - Lesen noch nicht implementiert, siehe "
        "docs/rekordbox-usb-import.md"
    )
