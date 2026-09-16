"""Rekordbox-spezifische Formate (Phase 1: nur Erkennung).

    pdb_header.py   Kopf von export.pdb (Seitengroesse, Tabellenverzeichnis)
    detector.py      RekordboxLibraryDetector-Funktion: erkennt die Struktur
                      auf einem Datentraeger

Tabellenzeilen (Tracks, Playlists, ...) und ANLZ-Analysedateien (Beatgrid,
Cues, Waveform) werden hier noch nicht gelesen - das ist Phase 2 ff.
"""

from __future__ import annotations
