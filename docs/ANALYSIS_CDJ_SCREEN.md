# Analyse: was ist vorhanden, was fehlt fuer die CDJ-Bildschirmoberflaeche

Stand der Untersuchung des gesamten Projekts (4470 Zeilen Python, 21 Module).

## Ergebnis in einem Satz

Das Projekt enthaelt **ausschliesslich** den Hardware-Input-Simulator aus dem
ersten Entwicklungsschritt. Eine Deck-Engine, eine Audio-Engine, Track-,
Waveform-, BPM-, Beatgrid-, Loop-, Hotcue- oder Sync-Logik existiert **nicht**.

## Vorhanden und wiederverwendbar

| Komponente | Datei | Rolle fuer die CDJ-Oberflaeche |
| ---------- | ----- | ------------------------------ |
| `InputEvent`, `ControlType`, `EventType`, `Source`, `Direction` | `core/model.py` | Ereignistypen der Eingabekette - unveraendert weiterverwendet |
| Control-IDs (55 Stueck) | `core/ids.py` | Quelle fuer das Input-Mapping auf Deck-Kommandos |
| Zentrale Komponentenliste | `core/controls.py` | Welche Bedienelemente es gibt, inkl. LED-Flag |
| `InputLayer` | `core/input_layer.py` | Die Stufe "Input Mapping" aus der geforderten Kette. Normiert, entprellt, fuehrt Tastenkombinationen, verteilt an Abonnenten |
| `InputState` | `core/state.py` | **Hardware**-Zustand (gedrueckte Taster, Analogwerte, Jog-Ticks). Nicht zu verwechseln mit dem Deck-Zustand |
| `CdjController` | `core/controller.py` | Der bisher leere Platzhalter mit `functions`-Tabelle. Genau hier gehoert die Uebersetzung Input -> Deck-Kommando hin |
| `VirtualSource` / `HardwareSource` | `sources/` | Maus bzw. ESP32-Protokoll -> `InputLayer`. Beide Wege bleiben unveraendert |
| Bedienfeld-GUI | `ui/` | Der Hardware-Simulator. Bleibt eigenstaendig - die CDJ-Bildschirmoberflaeche ist ein **zweites**, getrenntes Fenster |
| LED-Ausgangspfad | `InputLayer.set_led()` | Kann spaeter aus dem Deck-Zustand gespeist werden |

Die geforderte Kette existiert damit bereits zur Haelfte:

```
Hardware/Maus -> Source -> InputLayer -> [HIER FEHLT ALLES] -> GUI
                                          Deck Command
                                          Deck Engine
                                          Deck State
```

## Nicht vorhanden

Die Aufgabe geht in Abschnitt 1 davon aus, dass diese Logik existiert und nur
neu angezeigt werden muss. Das ist nicht der Fall:

* Audio-Engine (Dekodieren, Abspielen, Ausgabe, Pitch/Master-Tempo)
* Deck-Engine (Transport, Position, Play/Cue-Logik)
* Track-Modell und Library/Collection
* Track-Analyse (BPM-Erkennung, Beatgrid, Key-Erkennung)
* Waveform-Analyse und Waveform-Cache (vorberechnete RGB-Peaks)
* Hotcue-, Memory-Cue- und Loop-Speicher
* Sync, Master-Clock, Quantisierung
* Track-Loading
* Multi-Deck-Architektur
* jede Form von Netzwerk

Konsequenz aus Abschnitt 30 ("Keine Fake-Funktionalitaet"): Die Anzeigen fuer
Waveform, Beatgrid, BPM, Key, Hotcues, Loops und Browser koennen **keine echten
Werte** zeigen, solange dieses Backend fehlt. Sie duerfen laut Vorgabe auch
keine erfundenen Werte zeigen. Sie zeigen deshalb einen ausdruecklichen
Leerzustand.

## Was in diesem Schritt neu entsteht

Abschnitt 30 verlangt: fehlende Backend-Funktionen klar kennzeichnen und sauber
ergaenzen. Neu angelegt wird deshalb genau das Rueckgrat, ohne das die
Oberflaeche nicht ohne Fakes gebaut werden kann:

| Neu | Datei | Inhalt |
| --- | ----- | ------ |
| Deck-Datenmodell | `deck/state.py` | `DeckState`, `TrackInfo`, `HotCue`, `MemoryCue`, `LoopState`, `BeatGrid`, `WaveformData` - alle Felder aus Abschnitt 25 |
| Kommandos | `deck/commands.py` | `DeckCommand` - die Stufe "Deck Command" |
| Input-Mapping | `deck/mapping.py` | Control-ID -> Deck-Kommando. Verbindet den bestehenden `InputLayer` mit den Kommandos |
| Deck-Engine | `deck/engine.py` | Transport, Position, Tempo, Beat/Bar aus dem Beatgrid, Loop-, Hotcue- und Sync-Zustand. **Ohne Audio** - erzeugt keinen Ton und erfindet keine Trackdaten |
| Zustandsquelle | `deck/provider.py` | `DeckStateProvider` (abstrakt), `LocalDeckStateProvider`, Platzhalter fuer `NetworkDeckStateProvider` (Abschnitt 26) |
| CDJ-Bildschirm | `cdj_ui/` | Die Oberflaeche selbst, 1024 x 600, modular |

Die Deck-Engine ist bewusst **kein** Audio-Player. Sie fuehrt Zustand und
Transport. `DeckState.audio_status` meldet dauerhaft
`NO_AUDIO_BACKEND`, solange keine Audio-Engine existiert.

## Verfuegbare Laufzeitumgebung

Geprueft auf diesem Rechner:

| Paket | Status | Relevanz |
| ----- | ------ | -------- |
| Python 3.14 + Tkinter 8.6 | vorhanden | Basis, bisher einzige Abhaengigkeit |
| `numpy` 2.4.6 | vorhanden | Waveform-Peaks, Analyse |
| `Pillow` 12.3.0 | vorhanden | **Wichtig**: Tk-Canvas allein schafft keine 60-FPS-RGB-Waveform. Der Weg ist: Peaks -> PIL-Bild -> `PhotoImage` -> Canvas blitten |
| `soundfile` 0.14.0 | vorhanden | WAV/FLAC/AIFF dekodieren |
| `sounddevice` 0.5.5 | vorhanden | Audio-Ausgabe |
| `scipy` 1.18.0 | vorhanden | Filterbaenke fuer die RGB-Bandaufteilung |
| `mutagen` | vorhanden | Tags, Artwork |
| `pyserial` 3.5 | vorhanden | ESP32-Anbindung fuer `HardwareSource` |
| `librosa`, `aubio` | fehlen | BPM/Beatgrid muesste selbst implementiert werden (scipy reicht dafuer) |
| MP3/AAC-Dekodierung | offen | `soundfile` deckt WAV/FLAC/AIFF ab, nicht MP3 |

Das Projekt ist bisher **reine Standardbibliothek**. Die CDJ-Oberflaeche in
Phase 1 bleibt das ebenfalls. Fuer die Phasen 3-5 (Waveform-Darstellung mit
60 FPS) und fuer jede Audio-Engine sind `numpy` + `Pillow` bzw.
`soundfile` + `sounddevice` praktisch unvermeidbar. Das ist eine Entscheidung,
die getroffen werden muss.

## Umsetzbarkeit der Phasen

| Phase | Umsetzbar ohne Backend? |
| ----- | ----------------------- |
| 1 Grundlayout 1024 x 600 | **ja** - in diesem Schritt umgesetzt |
| 2 Trackinformationen | Anzeige ja, Inhalt erst mit Library/Analyse |
| 3 Overview-Waveform | nein - braucht Waveform-Analyse |
| 4 laufende Waveform | nein - braucht Waveform-Analyse |
| 5 Beatgrid | nein - braucht BPM-/Beatgrid-Analyse |
| 6 BPM / Tempo / Beat / Time | Tempo und Zeit ja (Engine vorhanden), BPM/Beat erst mit Analyse |
| 7 Hotcue-Marker | Zustand ja, Positionen erst mit Track-Loading |
| 8 Loops | **ja** - Engine-Logik vorhanden, Darstellung in der Waveform braucht Phase 4 |
| 9 Sync / Master / Phase Meter | Zustand ja, Phase Meter braucht Beatgrid |
| 10 Browser | nein - braucht Library |
| 11 Track Loading | nein - braucht Library + Analyse |
| 12 Touch-Bedienung | **ja** |
| 13 virtuelle Hardwaresteuerung | **ja** - bestehender Simulator wird angebunden |
| 14 Fullscreen | **ja** - in diesem Schritt umgesetzt |
| 15 mehrere CDJ-Instanzen | **ja** - in diesem Schritt vorbereitet |
| 16 Performance | erst sinnvoll messbar mit echter Waveform |
| 17 Netzwerk | Schnittstelle **ja**, Transport spaeter |

Der eigentliche kritische Pfad ist damit nicht die Oberflaeche, sondern die
fehlende Audio- und Analyse-Schicht.
