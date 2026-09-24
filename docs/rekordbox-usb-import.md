# Rekordbox-USB-Import

Ziel: einen mit rekordbox vorbereiteten USB-Stick so lesen, wie es ein
Pioneer/AlphaTheta-CDJ tut - Bibliothek, Playlists, BPM, Beatgrid, Cues,
Loops und Waveform aus den vorhandenen rekordbox-Daten, ohne sie erneut zu
analysieren. Siehe README.md fuer den Gesamtstand des Projekts.

**Stand:** Phase 1 (USB erkennen) und Phase 2 (Datenbank lesen, SOURCE und
BROWSE anbinden) abgeschlossen und **gegen einen echten Stick geprueft**.
Phasen 3-9 sind unten dokumentiert, aber noch nicht implementiert.

## Gegen echte Hardware geprueft

Alles Folgende ist gemessen, nicht angenommen - an einem Stick mit 658
Tracks (FAT32, `export.pdb` **und** `exportLibrary.db` gleichzeitig
vorhanden, `PIONEER/USBANLZ` vorhanden):

| Was | Ergebnis |
| --- | --- |
| `export.pdb` vollstaendig lesen | **0,04 s** - 658 Tracks, 485 Interpreten, 20 Genres, 40 Tonarten, 8 Farben, 613 Artwork-Pfade, keine Warnung |
| Playlist-Baum | 27 Knoten, echt verschachtelt (ein Ordner mit 17 Playlists), 860 Eintraege |
| Verlauf | 2 Listen, 56 Eintraege |
| Beatgrid (`PQTZ`) | 658 von 658 lesbar |
| Waveforms | `PWAV` 400 Sp., `PWV2` 100 Sp., `PWV3`/`PWV5` ~39 000 Sp., `PWV4` 1200 Sp. - alle lesbar |
| Hot Cues | 257 Tracks mit `PCOB`, 178 zusaetzlich mit `PCO2` |
| Audiodateien | 658 von 658 erreichbar |
| Artwork-Dateien | 613 von 658 vorhanden |
| **alle ANLZ-Dateien vorab lesen** | **50,5 s** (~77 ms je Track) |

Die letzte Zeile ist der wichtigste Befund: die **Datenbank** darf beim
Einstecken vollstaendig gelesen werden, die **Analysedateien** nicht. Sie
muessen je Track nachgeladen werden (Phase 4). Deshalb liefert
`RekordboxLibraryReader` bewusst Tracks **ohne** Beatgrid, Waveform und
Cues - leer heisst dort "noch nicht geladen", nicht "nicht vorhanden".

Ausserdem gefunden: ein Track mit einer **0 Byte grossen `.EXT`-Datei**.
Der Parser meldet das als "Datei zu klein fuer einen ANLZ-Kopf" und laesst
die uebrigen 657 Tracks unberuehrt - genau das gewuenschte Verhalten.

Nicht belegt auf diesem Stick (Tabelle wird gelesen, Feld bleibt leer -
es wird nichts geraten): Label (0 Eintraege), Album (3), Trackfarbe (0 von
658), Tonart nur bei 199 von 658.

## Quellen und Sicherheit der Daten

Es gibt **keine offizielle Dokumentation** der rekordbox-Dateiformate durch
Pioneer/AlphaTheta. Alles hier Beschriebene stammt aus unabhaengiger
Reverse-Engineering-Arbeit, die an mehreren Stellen gegeneinander
gegengeprueft ist:

* **Deep Symmetry**, *DJ Link Ecosystem Analysis*, Abschnitte "Database
  Exports" und "Analysis Files" -
  <https://djl-analysis.deepsymmetry.org/rekordbox-export-analysis/exports.html>,
  <https://djl-analysis.deepsymmetry.org/rekordbox-export-analysis/anlz.html>.
  Das Ergebnis der Arbeit von Henry Betts, Fabian Lesniak, James Elliott und
  weiteren. Primaerquelle fuer `export.pdb` und die `ANLZ*`-Dateien.
* **rekordcrate** (Rust), <https://github.com/Holzhaus/rekordcrate> -
  unabhaengige Implementierung, Kaitai-Struct-Definitionen fuer `pdb` und
  `anlz`. Zum Gegenpruefen der Feldnamen benutzt.
* **rekordbox-pdb** (Python, reine Standardbibliothek),
  <https://github.com/fragmede/rekordbox-pdb> - eigene `FORMAT.md`, laut
  Projekt byte-genau gegen echte Exporte und gegen die
  Kaitai/crate-digger-Referenz verifiziert.
* **pyrekordbox** (Python), <https://github.com/dylanljones/pyrekordbox> -
  fuer die ANLZ-Tag-Feldnamen und fuer `exportLibrary.db`
  ("Device Library Plus").
* **crate-digger** (Java) / **dysentery**, Deep Symmetrys Kaitai-Struct-
  Quelle und Analyse.tex/pdf - Ursprung der djl-analysis-Dokumentation.

Wo sich alle Quellen einig sind (Dateikopf, Tabellentypen, Track- und
Playlist-Zeilen, Beatgrid, Cue-Liste, monochrome Waveforms), gilt das Feld
als **gesichert** und wird implementiert. Wo eine Quelle selbst "unknown"
oder "purpose unclear" schreibt (siehe unten, z. B. `PWV4`-Farbkodierung im
Detail, `PQT2`-Haupteintraege, `PSSI`-`kind`-Bedeutung je Mood), wird das
hier genauso vermerkt und **nicht interpretiert** - lieber ein leeres Feld
als ein erfundener Wert. Dieses Projekt fuegt keine eigenen Vermutungen zu
unbekannten Bytes hinzu.

## Aufbau eines rekordbox-USB-Exports

```
<Wurzel>/
    PIONEER/
        rekordbox/
            export.pdb          DeviceSQL-Datenbank, "Legacy"-Format,
                                 von praktisch jedem CDJ/XDJ gelesen
            exportLibrary.db      "Device Library Plus": SQLCipher-
                                   verschluesselte SQLite-Datenbank neuerer
                                   rekordbox-Versionen (6.8+/7.x), fuer
                                   OPUS-QUAD/OMNIS-DUO/XDJ-AZ und
                                   plattformuebergreifenden Austausch
        USBANLZ/
            <Ordner>/<Unterordner>/
                ANLZ0000.DAT       Grundanalyse: Beatgrid, Cues, Vorschau-
                                   Waveform, VBR-Sprungtabelle, Pfad
                ANLZ0000.EXT        Erweiterte Analyse (Nexus/Nexus2):
                                   erweiterte Cues, farbige Waveform,
                                   Song-Struktur (Phrasen)
                ANLZ0000.2EX        Weitere Erweiterung neuerer Firmware
                                   (z. B. weitere Waveform-Varianten)
        DEVSETTING.DAT, MYSETTING.DAT, MYSETTING2.DAT, DJMMYSETTING.DAT
                                 Geraete-/Nutzereinstellungen, fuer den
                                 Bibliotheksimport ohne Bedeutung
    CONTENTS/...                 Ueblicher, aber **nicht garantierter**
                                 Ablageort der Audiodateien. Der
                                 tatsaechliche Pfad steht in jeder
                                 Track-Zeile selbst (`_file_path`) - dieses
                                 Projekt verlaesst sich nicht auf die
                                 Ordnerkonvention, sondern liest den Pfad
                                 aus der Datenbank (Phase 2).
```

Beide Datenbankformate koennen gleichzeitig auf einem Stick liegen. Ist
`export.pdb` vorhanden und gueltig, bevorzugt dieses Projekt sie, weil sie
tatsaechlich gelesen werden kann (siehe "Device Library Plus" unten).

Der Pfad zur passenden `ANLZ*`-Datei eines Tracks steht nicht in einer
festen Ordnerregel, sondern im Track-Datensatz selbst (String-Index 14,
`_analyze_path`, siehe unten) - auch das wird in Phase 2 aus der Datenbank
gelesen statt aus dem Ordnernamen erraten.

## `export.pdb` (DeviceSQL, "Legacy")

Seitenbasiertes Format ("DeviceSQL"), urspruenglich fuer sehr
speicherschwache Geraete entworfen. Little-Endian durchgehend.

### Dateikopf (implementiert: `media_library/rekordbox/pdb_header.py`)

| Offset | Groesse | Feld | Bedeutung |
| --- | --- | --- | --- |
| `0x00` | 4 | (Nullbytes) | Pruefwert |
| `0x04` | 4 | `len_page` | Seitengroesse in Byte |
| `0x08` | 4 | `num_tables` | Anzahl Tabellen |
| `0x0c` | 4 | unbekannt | evtl. "naechste freie Seite" |
| `0x10` | 4 | unbekannt | in keiner Quelle benannt ("gap") |
| `0x14` | 4 | `sequence` | Aenderungszaehler der Datenbank |
| `0x18` | 4 | (Reserve) | |
| `0x1c` | `num_tables` × 16 | Tabellenzeiger | siehe unten |

Tabellenzeiger (16 Byte): `table_type`(4) + unbekannt(4) + `first_page`(4)
+ `last_page`(4).

**In diesem Projekt implementiert:** genau dieser Kopf - genug, um eine
Datei als "strukturell plausible export.pdb" zu erkennen und die
Einstiegsseiten je Tabelle zu kennen, ohne eine einzige Zeile zu lesen.
Das ist bewusst Phase 1: Erkennung, keine Inhalte.

### Tabellentypen

| ID | Name | Bedeutung |
| --- | --- | --- |
| 0 | tracks | Track-Metadaten |
| 1 | genres | Genrenamen |
| 2 | artists | Interpretennamen |
| 3 | albums | Albennamen + Interpret |
| 4 | labels | Label-Namen |
| 5 | keys | Tonarten |
| 6 | colors | Farbdefinitionen (Track-/Cue-Farben) |
| 7 | playlist_tree | Playlist-/Ordnerhierarchie |
| 8 | playlist_entries | Track-Zugehoerigkeit je Playlist |
| 13 (`0x0d`) | artwork | Pfade zu Cover-Bildern |
| 16 (`0x10`) | columns | Kategorien fuer die CDJ-Browse-Spalten |
| 17 (`0x11`) | history_playlists | Verlaufslisten |
| 18 (`0x12`) | history_entries | Tracks je Verlaufsliste |
| 19 (`0x13`) | history | Sync-Protokoll |

`exportExt.pdb` (MyTags, auf USB-Exports optional) nutzt dieselben
Zahlenwerte fuer andere Tabellen (`tags`, `tag_tracks`) - separate Datei,
fuer dieses Projekt vorerst nicht relevant (Tag-Liste ist laut
`docs/CDJ3000_BROWSE_ANALYSIS.md` ohnehin nicht umgesetzt).

### Seiten- und Zeilenstruktur (Phase 2)

Jede Seite beginnt mit einem 32-Byte-Kopf (Seitenindex, Tabellentyp,
naechste Seite, Zeilenzahlen, Flags, freier/genutzter Heap-Platz). Zeilen
liegen als Heap in der Seite und werden ueber einen Index referenziert, der
vom Seitenende rueckwaerts aufgebaut ist; eine Bitmaske markiert, welche
Zeilen tatsaechlich gueltig sind (geloeschte Zeilen bleiben als Muell im
Heap stehen und **muessen** anhand der Bitmaske uebersprungen werden - sonst
werden geloeschte/ungueltige Daten als echte Tracks gelesen). Das wird in
Phase 2 implementiert.

### Track-Zeile (Phase 2/3)

Fester Kopf (0x5e = 94 Byte), danach 21 Offsets auf DeviceSQL-Strings.

| Offset | Groesse | Feld | Bedeutung |
| --- | --- | --- | --- |
| `0x08` | 4 | `sample_rate` | |
| `0x0c` | 4 | `composer_id` | Interpret (Komponist) |
| `0x10` | 4 | `file_size` | Byte |
| `0x1c` | 4 | `artwork_id` | |
| `0x20` | 4 | `key_id` | |
| `0x24` | 4 | `original_artist_id` | |
| `0x28` | 4 | `label_id` | |
| `0x2c` | 4 | `remixer_id` | |
| `0x30` | 4 | `bitrate` | |
| `0x34` | 4 | `track_number` | |
| `0x38` | 4 | `tempo` | BPM × 100 |
| `0x3c` | 4 | `genre_id` | |
| `0x40` | 4 | `album_id` | |
| `0x44` | 4 | `artist_id` | |
| `0x48` | 4 | `id` | Track-ID |
| `0x4c` | 2 | `disc_number` | |
| `0x4e` | 2 | `play_count` | |
| `0x50` | 2 | `year` | |
| `0x52` | 2 | `sample_depth` | Bit/Sample |
| `0x54` | 2 | `duration` | Sekunden |
| `0x58` | 1 | `color_id` | Track-Farbe (0 = keine) |
| `0x59` | 1 | `rating` | 0-5 Sterne |
| `0x5a` | 2 | `file_type` | 0 unbekannt, 1 MP3, 4 M4A, 5 FLAC, 0x0b WAV, 0x0c AIFF |
| `0x5e` | 21×2 | String-Offsets | siehe unten |

String-Offsets (Index → Inhalt): 0 ISRC, 1 Textautor, 2-4 unbekannt, 5
Nachricht, 6 "ON"/leer (Trackinfo veroeffentlichen), 7 "ON"/leer (Hot Cues
automatisch laden), 8-9 unbekannt, **10 Date Added** (`YYYY-MM-DD`), 11
Release Date, 12 Mix-/Remix-Name, 13 unbekannt, **14 Analyse-Pfad**
(`_analyze_path`, zeigt auf die `ANLZ*`-Datei), 15 Analysedatum, 16
Kommentar, **17 Titel**, 18 unbekannt, **19 Dateiname**, **20 voller
Dateipfad**.

Damit sind praktisch alle in Abschnitt 3 der Aufgabe geforderten Felder
belegt (Titel, Interpret [ueber `artist_id`], Album, Genre, Label,
Kommentar, Dateiname, Dateipfad, Dauer, BPM, Key, Rating, Farbe, Jahr,
Tracknummer, Discnummer, Date Added, Bitrate, Samplerate, Dateiformat,
Track-ID) - bis auf Datentyp-Aufloesung ueber die Nachbartabellen
(`artist_id` → Artists-Tabelle usw., Phase 2).

### DeviceSQL-Strings

Kurze ASCII-Strings: erstes Byte = (Laenge << 1) | 1. Lange Strings:
erstes Byte = Flag (`0x40` ASCII, `0x90` UTF-16LE), dann 2 Byte
Gesamtlaenge, 1 Byte Padding, dann die Daten.

### Playlist-Baum-Zeile (Phase 4)

`parent_id`(4) + unbekannt(4) + `sort_order`(4) + `id`(4) +
`raw_is_folder`(4, ≠0 = Ordner) + `name` (DeviceSQL-String).

Nesting entsteht ueber `parent_id` - **beliebig tief verschachtelbar**.
Das deckt Abschnitt 4 der Aufgabe (verschachtelte Playlist-Ordner) auf
Datenebene vollstaendig ab. Wichtiger Architektur-Befund fuer Phase 7:
das bestehende Browse-Modell `virtual_cdj/deck/library.py`
(`TrackListLibrary.playlists()`) kennt aktuell nur **eine** Ebene
(Playlist-Name → Track-Liste), keine Ordner. Das muss beim Zusammenfuehren
mit Rekordbox-Playlists erweitert werden - siehe "Offene Punkte" unten.

### Playlist-Eintrag-Zeile

`entry_index`(4) + `track_id`(4) + `playlist_id`(4). `entry_index` gibt die
Reihenfolge innerhalb der Playlist vor (Abschnitt 4: "Track-Reihenfolge
... darf nicht veraendert werden").

## `exportLibrary.db` ("Device Library Plus")

Neueres Format (rekordbox 6.8+/7.x) fuer Geraete wie OPUS-QUAD, OMNIS-DUO,
XDJ-AZ. Statt DeviceSQL eine **SQLCipher-verschluesselte SQLite-Datenbank**.
Kann parallel zu `export.pdb` auf demselben Stick liegen.

**Stand in diesem Projekt:** wird **erkannt** (Datei vorhanden, Kopf grob
geprueft), aber **nicht gelesen**. Gruende:

* Der Entschluesselungsschluessel ist nicht offiziell dokumentiert. Externe
  Projekte (u. a. pyrekordbox) leiten ihn aus einer verschleierten
  Konstante ab (Base85 + XOR + zlib) und behaupten, er sei nicht
  geraete-/lizenzabhaengig. Das konnte im Rahmen dieses Arbeitsschritts
  **nicht selbst verifiziert** werden (kein Testexport in diesem Format
  vorhanden) - eine solche Behauptung wird hier nicht ungeprueft
  uebernommen.
* Da `export.pdb` bei jedem bisher bekannten Export weiterhin mitgeschrieben
  wird, deckt der Legacy-Pfad die Anforderung bereits ab.

Wird `export.pdb` **nicht** gefunden, aber `exportLibrary.db` schon, meldet
die Erkennung das ehrlich als "erkannt, aber noch nicht unterstuetzt"
(`LibraryFormat.REKORDBOX_DEVICE_LIBRARY_PLUS`, `is_usable == False`) statt
eine leere Bibliothek vorzutaeuschen.

## ANLZ-Analysedateien

Jede Datei beginnt mit einem Kopf (`PMAI`, `len_header`, `len_file`),
danach folgt eine Kette von Tag-Abschnitten: 4-Byte-FourCC + `len_header` +
`len_tag` (Gesamtlaenge inkl. Kopf) + typspezifische Felder. Ein Parser
kann unbekannte Tags anhand von `len_tag` ueberspringen, ohne sie zu
verstehen - wichtig fuer Vorwaertskompatibilitaet mit neueren
rekordbox-Versionen (Abschnitt 16 der Aufgabe).

| Tag | Datei | Inhalt | Vertrauen |
| --- | --- | --- | --- |
| `PQTZ` | `.DAT` | Beatgrid | **gesichert** (3 Quellen einig) |
| `PQT2` | `.EXT` | erweitertes Beatgrid (Nexus2) | Kopf gesichert, Haupteintraege (2 Byte je Eintrag) **nicht vollstaendig entschluesselt** |
| `PCOB` | `.DAT`/`.EXT` | Cue-/Loop-Liste | **gesichert** |
| `PCO2` | `.EXT` | erweiterte Cue-Liste (Farbe, Kommentartext, Nexus2+) | Kopf gesichert, genaues Byte-Layout der erweiterten Eintraege **nicht vollstaendig bestaetigt** |
| `PPTH` | alle | referenzierter Audio-Pfad (UTF-16BE) | gesichert |
| `PVBR` | `.DAT` | VBR-Sprungtabelle (400 × 4-Byte-Frameindex) | gesichert, fuer dieses Projekt ohne eigenen Bedarf (Decoder macht eigenes VBR-Seeking) |
| `PSSI` | `.EXT` | Song-Struktur/Phrasen | Kopf + Eintragsgroesse gesichert; **Inhalt XOR-maskiert** (Muster unten); Bedeutung von `kind` je nach `mood` **nicht vollstaendig bestaetigt** |
| `PWAV` | `.DAT` | Vorschau-Waveform, monochrom, 400 Spalten | **gesichert** |
| `PWV2` | `.DAT` | kleine Vorschau-Waveform (aeltere Geraete) | gesichert |
| `PWV3` | `.EXT` | Detail-Waveform, monochrom | **gesichert** |
| `PWV4` | `.EXT` | Vorschau-Waveform, farbig, 1200 × 6 Byte | Kopf gesichert, genaue Byte-zu-Farbe-Zuordnung **nicht vollstaendig bestaetigt** |
| `PWV5` | `.EXT` | Detail-Waveform, farbig, 2 Byte/Eintrag (5 Bit Hoehe + 3×3 Bit RGB) | **gesichert** |
| `PWV6`/`PWV7`/`PWVC` | `.2EX` | weitere/neuere Waveform-Varianten | Koepfe teilweise bekannt, Zellinhalt **nicht dokumentiert** |

Details zu `PQTZ`: 24-Byte-Kopf, danach `len_beats` Eintraege zu je 8 Byte
(`beat_number` 1-4, `tempo` = BPM×100, `time` in ms).

Details zu `PCOB`: 24-Byte-Kopf mit `type` (0 = Memory Cues, 1 = Hot Cues)
und `len_cues`; je Eintrag 38 Byte mit `hot_cue` (0 = Memory, 1-8 = A-H),
`status` (4 = aktiver Loop), `type` (1 = Position, 2 = Loop), `time`
(Cue-Position in ms), `loop_time` (Loop-Ende in ms).

`PSSI`-Maskierung (zum Nachbau in Phase 6, damit hier keine Ueberraschung
entsteht): alle Bytes nach dem Feld `len_e` sind mit dem Muster
`CB E1 EE FA E5 EE AD EE E9 D2 E9 EB E1 E9 F3 E8 E9 F4 E1` XOR-verknuepft,
wobei jedes Musterbyte zusaetzlich um `len_e` erhoeht wird.

Farben in `PWV5`: 2 Byte je Spalte, Bits 0-4 Hoehe (0-31), Bits 5-7 Blau,
Bits 8-10 Gruen, Bits 11-13 Rot, Bits 14-15 unbekannt. In `PWAV`/`PWV2`/
`PWV3`: 1 Byte je Spalte, Bits 0-4 Hoehe, Bits 5-7 Weiss-/Saettigungsstufe
(kein RGB, monochrom mit Helligkeitsstufen).

## Architektur

```
virtual_cdj/media_library/
    provider.py          ExternalLibraryProvider (ABC), DetectedMedium,
                          LibraryFormat                      Phase 1  fertig
    volumes.py            VolumeInfo, list_volumes(), VolumeWatcher -
                          Datentraeger-Erkennung samt Kapazitaet,
                          dynamisch, kein fester Laufwerksbuchstabe
                                                              Phase 1  fertig
    devices.py            MediaDevice, DeviceStatus, UsbDeviceService -
                          anstecken/lesen/abziehen, Lesen im eigenen
                          Thread                              Phase 2  fertig
    model.py              DeviceLibrary, LibraryTrack, PlaylistNode,
                          HotCue, Waveform - das interne Modell
                                                              Phase 2  fertig
    sources.py            Bruecke ins Browse-Modell: MediaDevice ->
                          SourceInfo, LibraryTrack -> TrackInfo
                                                              Phase 2  fertig
    rekordbox/
        pdb_header.py       Kopf von export.pdb               Phase 1  fertig
        detector.py          detect_rekordbox()               Phase 1  fertig
        devicesql.py          DeviceSQL-Strings                Phase 2  fertig
        pdb_pages.py           Seiten und Zeilenverzeichnis     Phase 2  fertig
        pdb_rows.py             Zeilentypen                     Phase 2  fertig
        pdb_file.py              PdbDatabase: Tabellen lesen     Phase 2  fertig
        reader.py                 RekordboxLibraryReader:
                                  export.pdb -> DeviceLibrary    Phase 2  fertig
        anlz/                      ANLZ-Tags (Beatgrid, Cues,
                                   Waveform)  geschrieben, aber noch
                                   nicht angebunden              Phase 4
```

Ausdruecklich **nicht** gebaut wurde ein `RekordboxUsbProvider` als
`ExternalLibraryProvider`: `UsbDeviceService` + `sources.build_library()`
leisten dasselbe und sind bereits die Stelle, an der ein zweites Format
eingehaengt wird (`library_reader=`). Eine zweite Abstraktion daneben waere
die parallele Architektur, die die Aufgabe ausschliesst.

`ExternalLibraryProvider` liefert am Ende **keine eigene Datenstruktur**,
sondern die bereits vorhandenen Typen aus `virtual_cdj/deck/library.py`
(`TrackListLibrary`, `SourceInfo`, `SourceKind.USB`) und
`virtual_cdj/deck/state.py` (`TrackInfo`, `BeatGrid`, `WaveformSet`,
`WaveformData`, `HotCue`, `MemoryCue`). Diese Typen decken bereits fast
alles aus Abschnitt 9 der Aufgabe ab (`Library → Playlist → Track →
BeatGrid → WaveformData → CuePoints → Loops`), inklusive Farbfeldern
(`TrackInfo.color`, `HotCue.color`) - hier musste **nichts neu erfunden**
werden, nur mit echten Daten befuellt werden (Phase 2 ff.).

Der Lade-Weg orientiert sich am bestehenden Demo-Track-Provider
(`virtual_cdj/demo/tracks.py`) und dessen Registrierung in
`CdjApplication._enable_demo()`: ein `RekordboxUsbProvider` wird analog
per `AnalysisWorker.register_resolver("rb", ...)` angemeldet
(`rb://<volume_id>/<track_id>`). Anders als beim Demo-Provider wird die
Audiodatei weiterhin ueber den bestehenden Decoder/Resampler geladen
(`audio/decoder.py`, `audio/resample.py`) - nur `TrackAnalyzer.analyse()`
(neue BPM-/Beatgrid-/Waveform-Berechnung) wird uebersprungen, wenn
rekordbox bereits gueltige Daten fuer das jeweilige Feld liefert
(Abschnitt 5/11, Fallback pro Datenbereich statt pro Track).

## Fallback-Strategie (pro Datenbereich, Phase 2 ff.)

| Datenbereich | Rekordbox-Quelle | Fallback |
| --- | --- | --- |
| BPM | `tracks.tempo` (PDB) | `TrackAnalyzer` (Tempo) |
| Beatgrid | `PQTZ` | `TrackAnalyzer` (Tempo/Downbeat) |
| Waveform | `PWAV`/`PWV3`/`PWV5` | `TrackAnalyzer` (Waveform) |
| Hot Cues/Memory Cues/Loops | `PCOB`/`PCO2` | keiner - ohne Rekordbox-Daten bleibt die Liste leer, es werden keine Cues erfunden |
| Key | `tracks.key_id` → Keys-Tabelle | `TrackAnalyzer` (Key), optional |
| Farbe | `tracks.color_id`/`PCOB`-Cue-Farbe | keine - leer bleibt leer |

Jedes Feld wird einzeln geprueft und einzeln zurueckgefallen; ein
fehlendes/beschaedigtes Feld loest **keine** Neuanalyse der uebrigen Felder
aus.

## Cache (Phase 8, geplant)

Idee: `virtual_cdj/audio/cache.py` (`AnalysisCache`) ist bereits ein
Muster fuer "einmal berechnen, dann aus einer Datei lesen" - derselbe
Ansatz laesst sich fuer Rekordbox-Daten wiederverwenden (normalisierte
Zeilen aus `export.pdb`, entschluesselte/entmaskierte ANLZ-Daten), statt
bei jedem Einstecken die komplette PDB erneut zu parsen. Cache-Schluessel:
Datentraeger-Seriennummer (`VolumeInfo.serial`, bereits implementiert),
`PdbHeader.sequence` (Aenderungszaehler der Datenbank, bereits
implementiert), Dateigroesse/-Aenderungszeit von `export.pdb`. Der
USB-Stick bleibt dabei read-only - der Cache liegt lokal, niemals auf dem
Stick.

## Netzwerkarchitektur (Phase 9, vorbereitet)

`ExternalLibraryProvider` kennt keine Quelle konkret - ein
`RekordboxUsbProvider` und ein spaeterer `NetworkLibraryProvider`
(USB-Bibliothek eines anderen selbstgebauten CDJs) implementieren dieselbe
Schnittstelle und liefern dieselben normalisierten Typen. Das entspricht
dem bereits vorhandenen Muster `DeckStateProvider` /
`NetworkDeckStateProvider` in `virtual_cdj/deck/provider.py` - dort ist die
Trennung lokal/Netzwerk bereits vorgezeichnet, hier wird sie fuer
Bibliotheken uebernommen statt neu erfunden.

## Umgesetzt (Phase 1)

* `virtual_cdj/media_library/volumes.py`: `list_volumes()` fragt bei jedem
  Aufruf `GetLogicalDrives`/`GetDriveTypeW`/`GetVolumeInformationW` (Windows,
  `ctypes`, keine zusaetzliche Abhaengigkeit) neu ab - **kein** fest
  einprogrammierter Laufwerksbuchstabe. `VolumeWatcher.poll()` ist fuer den
  regelmaessigen Aufruf aus dem GUI-Tick gedacht (wie
  `CdjApplication.tick()` das mit `AnalysisWorker` schon tut), meldet
  hinzugekommene/entfernte Datentraeger und begrenzt echte
  Betriebssystemaufrufe auf ein Mindestintervall.
* `virtual_cdj/media_library/rekordbox/pdb_header.py`: liest Kopf und
  Tabellenverzeichnis von `export.pdb`, ohne eine Zeile zu lesen. Erkennt
  eine zu kleine, falsch beginnende, unplausible oder abgeschnittene Datei
  und wirft dafuer `PdbFormatError` mit Klartextgrund statt abzustuerzen.
* `virtual_cdj/media_library/rekordbox/detector.py`:
  `detect_rekordbox(volume)` untersucht einen Datentraeger, erkennt
  `export.pdb` (bevorzugt) und `exportLibrary.db`, prueft, ob
  `PIONEER/USBANLZ` vorhanden ist, und liefert immer ein `DetectedMedium` -
  nie eine Ausnahme.
* Tests: `tests/test_media_library.py`, 19 Tests. Da kein echter
  Rekordbox-Stick zur Verfuegung stand, bauen die Tests synthetische
  `export.pdb`-Koepfe nach der oben dokumentierten, gegengeprueften
  Spezifikation nach und pruefen Datentraegererkennung, Formaterkennung und
  Fehlerfaelle (zu kleine/falsche/abgeschnittene Datei, `exportLibrary.db`
  ohne `export.pdb`, beide gleichzeitig, fehlender `USBANLZ`-Ordner).
  Gesamtstand des Projekts: 516 Tests, alle bestanden.

## Umgesetzt (Phase 2)

* `rekordbox/reader.py`: `RekordboxLibraryReader` setzt die Zeilen aus
  `pdb_file.py` zu `DeviceLibrary`/`LibraryTrack`/`PlaylistNode` zusammen.
  Nachbartabellen (Interpret, Album, Genre, Label, Tonart, Farbe, Artwork)
  werden aufgeloest; ein nicht aufloesbarer Verweis ergibt ein **leeres**
  Feld. Der Playlist-Baum entsteht ueber `parent_id` in **voller Tiefe**,
  die Trackreihenfolge kommt aus `entry_index`. Geloeschte Zeilen werden
  anhand der Anwesenheitsmaske uebersprungen.
* `devices.py`: `UsbDeviceService` verbindet `VolumeWatcher` und
  `detect_rekordbox`. Zustaende: `SCANNING` -> `READY` / `NO_LIBRARY` /
  `UNSUPPORTED` / `ERROR`. Das Lesen laeuft in einem eigenen Thread, das
  Ergebnis wird im GUI-Thread aus einer Queue abgeholt - dasselbe Muster
  wie `AnalysisWorker`. Wird der Stick waehrend des Lesens abgezogen, wird
  das Ergebnis verworfen. Ein Stick gilt nur dann als derselbe, wenn Pfad
  **und Seriennummer** gleich geblieben sind; sonst waere ein Tausch auf
  demselben Laufwerksbuchstaben unsichtbar.
* `volumes.py`: zusaetzlich Gesamtgroesse und freier Platz
  (`GetDiskFreeSpaceExW`) fuer die Geraeteinformation auf S. 18.
* `sources.py`: `MediaDevice` -> `SourceInfo`, `LibraryTrack` ->
  `TrackInfo`. Der in der Datenbank gespeicherte Pfad (`/Contents/...`)
  wird mit dem **jetzt erkannten** Wurzelpfad zu einem echten Dateipfad
  verbunden - der Laufwerksbuchstabe steht nirgends in der Datei. Track-IDs
  werden zu `rb:<Seriennummer>:<ID>`, damit zwei Sticks sich nicht
  gegenseitig ueberschreiben.
* `app.py`: `CdjApplication(usb=True)` meldet den Dienst an, `tick()`
  fragt ihn, `on_sources_changed` stoesst die Oberflaeche an,
  `refresh_sources()` liest manuell neu ein. Standard ist `usb=False`,
  damit Tests und Skripte nicht ungefragt die Laufwerke abfragen;
  `run_cdj.py` schaltet ein (`--no-usb` schaltet aus).
* SOURCE zeigt zusaetzlich `Status`, `Library` (erkanntes Format) und
  `Format` (Dateisystem) sowie Warnungen. Ein erkannter, aber nicht
  lesbarer Stick bleibt sichtbar, statt wie eine leere Bibliothek
  auszusehen.

**Dabei gefundener Fehler (behoben):** `deck/library.py` sortierte die
Tracks einer Playlist nach der Position in der **Quelle** statt nach der
Position in der **Playlist** - die von rekordbox vorgegebene Reihenfolge
ging beim Anzeigen wieder verloren. Mit den bisherigen Demo-Daten fiel das
nicht auf, weil beide Reihenfolgen dort gleich waren. Die Spalte "#" zaehlt
jetzt innerhalb der Playlist bzw. des Verlaufs.

**Filter der Quellen:** `list_volumes()` liefert bewusst auch feste
Laufwerke (manche USB-Gehaeuse melden sich so). Im SOURCE-Bildschirm
erscheint ein festes Laufwerk aber nur, wenn wirklich eine
rekordbox-Struktur darauf liegt - sonst waere die Systemplatte eine Quelle.

## Nicht unterstuetzt (Stand jetzt)

* `exportLibrary.db` (Device Library Plus): erkannt, nicht gelesen.
* Label, Album, Trackfarbe: werden gelesen, sind auf dem Teststick aber
  leer bzw. kaum belegt. Leer bleibt leer.
* Beatgrid, Waveform, Hot Cues, Memory Cues, Loops: Parser fertig, aber
  noch nicht an die Oberflaeche angebunden (Phase 4).
* SEARCH, TRACK FILTER, TAG LIST: es gibt dafuer **keine Oberflaeche** -
  nur die Taster. Sie muessen gebaut werden, nicht angebunden.

## USB STOP: Quelle trennen, Laufwerk nicht auswerfen

Am CDJ heisst der Taster USB STOP, und er macht genau eine Sache: der
Player gibt den Datentraeger frei. Danach darf man ihn abziehen.

Hier ist das `UsbDeviceService.disconnect(device_id)`, ausgeloest ueber
`CdjApplication.stop_media()`:

* Das Geraet verschwindet aus `UsbDeviceService.devices` und - ueber den
  ohnehin vorhandenen `on_detached`-Rueckruf - aus `MediaLibrary`. Es ist
  derselbe Weg wie beim physischen Abziehen, keine zweite Abbaulogik.
* Von dieser Quelle laesst sich nichts mehr laden: `load_by_track_id`
  findet die Track-ID nicht mehr.
* Ein **bereits geladener** Track spielt weiter. Seine Samples liegen im
  Speicher, nicht auf dem Stick. Ein Deck mitten im Set stummzuschalten
  waere das Gegenteil eines sicheren Zustands.
* Solange das Laufwerk physisch steckt, kommt es nicht von selbst zurueck
  (`_released`). Erst Abziehen und neu Einstecken macht daraus wieder eine
  Quelle.

**Kein Betriebssystem-Auswurf.** Es gibt bewusst keinen erzwungenen
Auswurf, kein `subprocess`, kein `DeviceIoControl`. Ein erzwungenes
Aushaengen mit noch offenen Schreibpuffern ist der uebliche Weg, auf dem
rekordbox-Sticks kaputtgehen - und dieses Programm schreibt ohnehin nicht
auf den Stick, hat also auch nichts zu leeren. Das physische Abziehen
bleibt Sache der Person davor. Ein Test haelt das fest
(`tests/test_hardware_controls.py::UsbStopServiceTests`).

Welcher Stick gemeint ist, entscheidet `stop_media()` in dieser
Reihenfolge: der, von dem der Track dieses Decks stammt; sonst der
einzige angeschlossene; sonst keiner. Lieber nichts trennen als den
falschen Stick.

## Offene Punkte

* **Playlist-Ordner-Tiefe:** `RekordboxLibraryReader` liefert den Baum in
  voller Tiefe, `virtual_cdj/deck/library.py`
  (`TrackListLibrary.playlists()`/`node()`) kennt aber weiterhin nur eine
  Ebene. `sources.flatten_playlists()` flacht den Baum deshalb vorlaeufig
  mit `" / "` zwischen den Ebenen ab. Das Trennzeichen hat absichtlich
  Leerzeichen: ein Playlist-Name darf selbst einen Schraegstrich enthalten
  (auf dem Teststick gibt es `Hardtechno/Industrial`). Die echte Hierarchie
  im Browse-Modell ist der naechste Schritt.
* `exportLibrary.db` ("Device Library Plus"): erkannt, nicht gelesen (siehe
  oben - unklare Schluesselherkunft, kein Testexport verfuegbar).
  `PQT2`, `PCO2`, `PWV4`, `PWV6`, `PWV7`, `PWVC`: Kopf bekannt,
  Feldinhalt nicht vollstaendig gesichert - werden in Phase 5/6 entweder
  best-effort mit klarer Kennzeichnung im Debug-Overlay ("Fallback-Analyse
  verwendet") oder gar nicht geparst, aber niemals mit erfundenen Werten
  gefuellt.
* `PSSI`-Phrasenbedeutung (`kind` je nach `mood`): Struktur bekannt,
  Bedeutung der Zahlenwerte nicht abschliessend bestaetigt - vorerst nur
  als Rohdaten fuer die Debug-Ansicht vorgesehen, nicht fuer eine
  GUI-Funktion.
* MyTags (`exportExt.pdb`): nicht Teil dieses Arbeitsschritts (siehe
  `docs/CDJ3000_BROWSE_ANALYSIS.md`, Tag-Liste ist ohnehin nicht
  umgesetzt).
* Serato-Bibliotheken, Netzwerk-Bibliothek eines anderen CDJs: nur als
  Architekturziel vorbereitet (`ExternalLibraryProvider`), nicht
  implementiert.

## Naechste Schritte

* **Phase 3:** Playlist-Hierarchie im Browse-Modell (`deck/library.py`),
  damit die Ordner echte Ebenen werden statt abgeflachter Namen.
* **Phase 4:** ANLZ je Track **nachladend** (nicht vorab - siehe die
  gemessenen 50,5 s): Beatgrid aus `PQTZ`, Cues/Loops aus `PCOB`/`PCO2`,
  Waveform aus `PWAV`/`PWV3`/`PWV5`. Ergebnis in einen lokalen Cache, der
  Stick bleibt unberuehrt.
* **Phase 5:** Track markieren und Track laden als **getrennte** Zustaende;
  `TrackLoadService` prueft den Pfad, laedt ins Deck und meldet Fehler
  verstaendlich.
* **Phase 6:** SEARCH - Dienst **und** Bildschirm (beides fehlt).
* **Phase 7:** TRACK FILTER - Dienst und Bildschirm.
* **Phase 8:** TAG LIST - nur im Speicher, ohne Schreibzugriff auf den
  Stick; Architektur auf einen spaeteren, ausdruecklich einzuschaltenden
  Export vorbereitet.
* **Phase 9:** Dateibrowser-Fallback fuer Sticks ohne rekordbox-Bibliothek;
  netzwerkfaehige Abstraktion.

## Debug-Ansicht (Phase 7 ff., vorgesehen)

Geplant als Ergaenzung zu `virtual_cdj/cdj_ui/debug_overlay.py` (F3):
Rekordbox-Track-ID, Dateipfad, BPM/Key/Rating/Farbe aus der PDB,
Beatgrid/Waveform-Vorschau/Waveform-Detail/RGB-Waveform vorhanden
ja/nein, Anzahl Hot Cues/Memory Cues/Loops, verwendete `ANLZ*`-Datei(en),
verwendeter Parser, ob eine Fallback-Analyse gegriffen hat. Noch nicht
implementiert - erst sinnvoll, sobald Phase 2-6 echte Daten liefern.
