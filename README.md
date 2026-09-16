# Virtueller CDJ

Entwicklungsumgebung fuer ein selbstgebautes CDJ. Zwei Oberflaechen:

1. **Hardware-Simulator** (`python run.py`) - das Bedienfeld. Jedes
   Bedienelement des geplanten Geraets ist virtuell bedienbar und wird spaeter
   mit echten Eingaengen (ESP32, MCP23017, ADC, optischer Encoder) verbunden.
2. **CDJ-Bildschirmoberflaeche** (`python run_cdj.py`) - das Display des
   Players, 1024 x 600, im Aufbau an Rekordbox/CDJ angelehnt.

Vorhanden: Audio-Engine mit echter Ausgabe, Track-Analyse (BPM, Beatgrid,
RGB-Waveform, Tonart), persistenter Analyse-Cache, Cue und Hotcues -
siehe [docs/AUDIO.md](docs/AUDIO.md). Die vollstaendige Loop-Bedienung
(Loop In/Out, Adjust am Jogwheel, CALL, Beatloop-Pads, Reloop) steht in
[docs/LOOP.md](docs/LOOP.md), die Jog-Bedienung und -Sensorauswertung
(Lichtschranken, kapazitiver Sensor, 800 Schritte je Umdrehung) in
[docs/JOG.md](docs/JOG.md). Quantize (mit waehlbarer Rasterweite), Slip
(Hintergrund-Zeitachse bei Scratch, Pause, Loop, Hotcue und Reverse) und
der Jog Mode VINYL/CDJ stehen in
[docs/PLAYER_MODES.md](docs/PLAYER_MODES.md).

**Rekordbox-USB-Sticks** werden erkannt und im Nur-Lese-Modus eingelesen:
Datentraeger dynamisch (kein fester Laufwerksbuchstabe), `export.pdb`
vollstaendig gelesen, SOURCE und BROWSE arbeiten mit echten Tracks,
Interpreten, Genres, Tonarten und Playlists samt Ordnern und
Originalreihenfolge. An einem Stick mit 658 Tracks geprueft. Der Stick wird
dabei **nie** veraendert. Details, Messwerte und der Stand je Phase:
[docs/rekordbox-usb-import.md](docs/rekordbox-usb-import.md).

**PLAYLIST**, **TAG LIST** und die **Sortierung** der Tracklisten sind
umgesetzt - siehe [docs/BROWSE_LISTS.md](docs/BROWSE_LISTS.md). Es sind
keine eigenen Bildschirme, sondern Einstiege in dieselbe Bibliothek: eine
Trackliste, eine Sortierfunktion, eine Ladefunktion.

Die **Suchtasten** `SEARCH ◀◀` / `▶▶` spulen im laufenden wie im
pausierten Track; gehalten laeuft die Suche weiter, Loslassen beendet sie
sofort. Zusammen mit dem Jogwheel wird daraus die schnelle Suche.

Noch **nicht** enthalten: Beatgrid, Waveform und Cues aus den
ANLZ-Analysedateien (Parser fertig, Anbindung folgt), der
**Such-Bildschirm** mit Tastatur und TRACK FILTER (dafuer gibt es bisher
nur die Taster, keine Oberflaeche - nicht zu verwechseln mit den
Suchtasten oben), Key Lock (Master Tempo), PRO DJ LINK. Anzeigen ohne
Daten zeigen einen ausdruecklichen Leerzustand statt erfundener Werte.

## Voraussetzungen

Python 3.11 oder neuer mit Tkinter, dazu:

```bash
python -m pip install numpy scipy soundfile sounddevice librosa soxr mutagen Pillow
```

Jede Abhaengigkeit ist in [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md)
begruendet. Formate: WAV, FLAC, AIFF, MP3, OGG (AAC/M4A noch nicht).

## Starten

```bash
python run.py                    # Hardware-Simulator (Bedienfeld)
python run_jog.py                # Jog-Kalibrierung (Touch-Grenzwert einmessen)
python run_cdj.py                # CDJ-Display + Bedienfeld, Deck 1
python run_cdj.py --track song.flac        # Track laden und abspielen
python run_cdj.py --decks 1,2 --track a.mp3 --track b.mp3
python run_cdj.py --no-audio     # ohne Ausgabegeraet (Wanduhr-Transport)
python run_cdj.py --no-usb       # angeschlossene Datentraeger ignorieren
python run_cdj.py --fullscreen   # Kiosk-Modus fuer den 7-Zoll-Schirm
python run_cdj.py --debug        # Entwicklungsanzeige
python run_cdj.py --no-panel     # nur das Display
```

Tasten im CDJ-Display: `F1` Menue, `F2` Pruefung, `Esc` zurueck zur
Performance (dort: Kiosk-Modus verlassen), `F11` Fullscreen umschalten,
`F3` Entwicklungsanzeige umschalten.

Am Bedienfeld oeffnet `SHIFT + MENU` direkt die persoenlichen Einstellungen;
`MENU` ohne SHIFT zeigt weiterhin den Verlauf der zuletzt gespielten Tracks.

Der Bildschirm hat mehrere Ebenen - Performance, Menue, Pruefung,
Kalibrierung und Einstellungen. Welche gilt, sagt ein einziger
Anwendungsmodus; ausserhalb der Performance loesen Hardware-Eingaben keine
DJ-Funktionen aus, sondern gehen nur in die jeweilige Seite. Details:
[docs/MODES.md](docs/MODES.md).

Unabhaengig davon waehlt `OPERATING MODE` im Menue zwischen dem Mock-
`MidiBackend` und dem lokalen `CdjBackend`. Controls und GUI laufen in beiden
Faellen ueber denselben `DeckController` und `DeckState`; der zuletzt gewaehlte
Modus steht in `config/settings.json`. Architektur und aktueller Mock-Stand:
[docs/OPERATING_MODES.md](docs/OPERATING_MODES.md).

## Tests

```bash
python -m unittest discover -s tests -t .
```

843 Tests. Darunter Dekoder, Analyse, Cache, Tempo, Seek, Cue, Loop-Logik,
Hotcues, Jog-Sensorauswertung, Anwendungsmodi und Kalibrierung, vier Decks
gleichzeitig, MIDI-/CDJ-Backendwechsel und die vollstaendige Kette bis in die
Oberflaeche. Die GUI-Tests bauen ihre Fenster unsichtbar auf - beim Testen
oeffnet sich also kein sichtbares Fenster.

Die Tests zur Datentraegererkennung fassen **kein** echtes Laufwerk an: die
Datentraeger sind eingesetzte Listen, die Datenbanken synthetische
`export.pdb`-Dateien aus `tests/pdb_fixtures.py`, gebaut nach derselben
Spezifikation, die die Parser dokumentieren. Ein Testergebnis haengt damit
nicht davon ab, welcher Stick gerade steckt.

Loops werden mit einem Rampensignal geprueft, dessen Samplewert die Position
ist. Damit ist die Sample-Genauigkeit messbar statt nur behauptet.

## Bedienung der virtuellen Oberflaeche

| Eingabe | Wirkung |
| ------- | ------- |
| Linksklick auf Taster | `PRESS` beim Druecken, `RELEASE` beim Loslassen |
| Rechtsklick auf Taster | rastet den Taster gedrueckt (fuer Tastenkombinationen) |
| Linksklick auf gerasteten Taster | loest die Rastung |
| Ziehen auf dem Fader | fortlaufende Werte 0.000 - 1.000 |
| Mausrad ueber Fader | Feinverstellung in 0.01-Schritten |
| Senkrecht ziehen / Mausrad auf dem Poti | Werte 0.000 - 1.000 |
| Ziehen auf dem **Rand** des Load-Reglers | nur Drehung - auswaehlen, ohne auszuloesen |
| Mausrad ueber dem Load-Regler | nur Drehung |
| Klicken / Ziehen auf der **Nabe** des Load-Reglers | `BROWSE_PRESS` (Halten = lange Aktion) |
| Ziehen auf der Jog-Platte | Beruehrung **und** Drehung - im Vinyl-Modus Scratch |
| Ziehen auf dem Jog-Rand | nur Drehung, keine Beruehrung - Pitch Bend |
| Mausrad ueber dem Jog | schnelle Stoesse, mit Strg fuenffach (Backspin-Test) |
| Klick auf ein Segment des Direction-Schalters | Position `SLIP_REV` / `FWD` / `REV` |
| Maus ueber ein Element | Zeile mit ID, Typ, Gruppe, Hardware und Notizen |
| `F12` oder Schalter `Bearbeitungsmodus` | Bearbeitungsmodus ein-/ausschalten |
| Klick auf einen Taster im Bearbeitungsmodus | Name, Konsolenausgabe und LED bearbeiten |

Da eine Maus nur eine Taste gleichzeitig halten kann, ist das Rasten per
Rechtsklick der Weg, Tastenkombinationen zu testen. Der Knopf
"Rastungen loesen" setzt alles zurueck.

Im Bearbeitungsmodus fuehrt ein Klick keine DJ-Funktion aus. Der Dialog
aendert den sichtbaren Namen, die optionale Konsolenausgabe und die Button-LED.
Als LED stehen **keine LED**, **Blau**, **Orange** und **Grün** zur Wahl. Der
Konsolentext erscheint beim Druecken exakt einmal; ein leeres Feld aktiviert
wieder die normale MIDI-/CDJ-Mock-Ausgabe. Die technische Control-ID bleibt
unveraendert, damit Hardware- und Funktionsmapping stabil bleiben. Die
Anpassungen werden in `config/button_customizations.json` gespeichert und beim
naechsten Start wieder geladen.

Rot umrandete Elemente sind **ungeklaert**: im Bild erkannt, Beschriftung noch
nicht gesichert. Sie erzeugen trotzdem schon Ereignisse.

## Aufbau

```
Bedienfeld (Maus)                  ESP32 / MCP23017 / ADC / Encoder
        |                                        |
        v                                        v
VirtualSource                            HardwareSource
        \                                      /
         +---------------> InputLayer <-------+
                               |
                          InputMapper
                               |
                          DeckCommand
                               |
                        DeckController
                               |
                          ModeManager
                         /           \
                  MidiBackend     CdjBackend -> DeckVoice -> Ausgabe
                         \           /
   Track File -> Decoder -> Analyse -> Cache -> DeckState
                                                 |
                                        DeckStateProvider
                                        (lokal / Netzwerk)
                                                 |
                                    CDJ-Bildschirmoberflaeche
```

Die Oberflaechen rufen keine Funktion direkt auf. Das Bedienfeld kennt nur
`VirtualSource`, das CDJ-Display nur `DeckStateProvider`. Ein Mausklick und
ein echter Taster erzeugen dasselbe `InputEvent` und dasselbe `DeckCommand`;
nur das Feld `source` bzw. `origin` unterscheidet sich.

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Verzeichnisse

```
virtual_cdj/core/       Komponentenliste, Input-Schicht, Eingabezustand
virtual_cdj/sources/    Eingabequellen (virtuell, Hardware)
virtual_cdj/jog/        Jog-Sensorauswertung: Quadratur, Touch, Positionsstand
virtual_cdj/shell/      Anwendungsmodus, Hardware-Zustand, Kalibrierwerte
virtual_cdj/audio/      Dekoder, Analyse, Cache, Audio-Engine, Worker
virtual_cdj/deck/       Deck-Zustand, Kommandos, Input-Mapping, Engine
virtual_cdj/media_library/  Datentraeger-Erkennung, Rekordbox-Leser (nur
                        lesend), Bruecke zu SOURCE und BROWSE
virtual_cdj/ui/         Hardware-Simulator: Bedienfeld, Debug, Input-Monitor
virtual_cdj/cdj_ui/     CDJ-Bildschirmoberflaeche (1024 x 600)
virtual_cdj/app.py      Verdrahtung aller Schichten
cache/analysis/         persistenter Analyse-Cache (wird angelegt)
config/                 hardware_mapping.json (Element -> Pin),
                        calibration.json (eingemessene Werte, wird angelegt),
                        settings.json (persistenter Betriebsmodus),
                        button_customizations.json (Namen/Konsolentexte)
docs/                   Analyse, Referenztabelle, Architektur, Audio
tools/                  Generatoren fuer Mapping-Datei und Referenztabelle
tests/                  Tests ohne und mit GUI
```

## CDJ-Bildschirmoberflaeche

Der Bildschirm zeigt immer die Seite zum aktuellen Anwendungsmodus. Unten
steht der Aufbau der Performance-Seite; die uebrigen Seiten (Menue,
Pruefung, Kalibrierung, Einstellungen) stehen in
[docs/MODES.md](docs/MODES.md).

Aufbau von oben nach unten:

| Bereich | Klasse | Inhalt |
| ------- | ------ | ------ |
| Reiterleiste | `CdjTouchNav` | WAVEFORM / BROWSE / INFO / CUE-LOOP / SETTINGS |
| Kopfzeile | `CdjTopBar` | Artwork, Titel, Interpret, Genre, Decknummer, Quelle |
| Trackinfo | `CdjTrackInfo` | Restzeit, BPM gross, Tempo in %, Original-BPM, Tonart, Beat 1-4, Takt |
| Master-Zeile | `CdjMasterWaveform` | nur wenn ein **anderes** Deck Master ist, plus Phase Meter |
| Uebersicht | `CdjOverviewWaveform` | ganzer Track, Position, Loop, Hotcues, Memory Cues; Beruehrung springt |
| laufende Waveform | `CdjScrollingWaveform` | Playhead fest in der Mitte, Beatgrid, Loop, Hotcues |
| Fusszeile | `CdjPerformanceBar` | Pads A-H, Loop-Laenge, SYNC / MASTER / QUANTIZE |
| Einblendung | `CdjDebugOverlay` | Deck-Zustand, FPS, letzte Kommandos (`F3`) |

Alle Werte kommen aus `DeckState`. Ein Klick auf SYNC, MASTER oder QUANTIZE
ist kein optischer Umschalter - er sendet ein Kommando, und die Anzeige folgt
erst dem geaenderten Deck-Zustand.

## Referenz der Bedienelemente

[docs/CONTROLS.md](docs/CONTROLS.md) - erzeugt aus
`virtual_cdj/core/controls.py`:

```bash
python tools/gen_controls_doc.py
```

## Hardware zuordnen

```bash
python tools/gen_hardware_mapping.py
```

Erzeugt bzw. ergaenzt `config/hardware_mapping.json` und laesst bereits
eingetragene Zuordnungen unangetastet. Format und Beispiele:
[docs/HARDWARE_MAPPING.md](docs/HARDWARE_MAPPING.md)

## Offene Punkte

Drei Elemente sind mit Status *ungeklaert* eingetragen, weil ihre
Beschriftung im Bild nicht sicher lesbar ist. Sie sind in
[docs/CONTROLS.md](docs/CONTROLS.md) unter "Offene Punkte" und in
[docs/IMAGE_ANALYSIS.md](docs/IMAGE_ANALYSIS.md) unter "Zu klaeren"
aufgelistet.
