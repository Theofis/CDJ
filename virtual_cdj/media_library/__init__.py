"""Externe Bibliotheken (Rekordbox-USB, spaeter weitere Quellen).

Dieses Paket ist die Bruecke zwischen einem Datentraeger (USB-Stick, spaeter
Netzwerk-Freigabe eines anderen selbstgebauten CDJs) und der bestehenden
Browse-/Deck-Architektur in ``virtual_cdj.deck.library`` und
``virtual_cdj.deck.state``.

Es wird bewusst **keine** parallele Datenstruktur erfunden: Ergebnis jeder
Quelle ist am Ende ``TrackListLibrary`` / ``SourceInfo`` (Browse-Modell) und
``TrackInfo`` samt ``BeatGrid`` / ``WaveformSet`` / ``HotCue`` / ``MemoryCue``
(Deck-Modell) - genau das, was auch die eigene Analyse liefert. Die uebrige
Software unterscheidet danach nicht mehr, ob ein Track von einer lokalen
Datei, einem Rekordbox-USB oder spaeter einem Netzwerk-CDJ kommt.

Aufbau:

    media_library/
        provider.py     ExternalLibraryProvider - die Schnittstelle
        volumes.py       Datentraeger-Erkennung (USB, dynamisch, kein
                          fester Laufwerksbuchstabe)
        rekordbox/        Rekordbox-spezifische Formate
            pdb_header.py   Kopf von export.pdb (DeviceSQL) - Phase 1
            detector.py      erkennt eine Rekordbox-Exportstruktur

Siehe docs/rekordbox-usb-import.md fuer die vollstaendige Dokumentation
inklusive der Quellen der Formatkenntnisse und dem Stand je Phase.
"""

from __future__ import annotations
