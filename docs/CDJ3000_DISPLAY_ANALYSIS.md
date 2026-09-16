# CDJ-3000-Display: Analyse vor der Umsetzung

## 1. Vorhandene Projektstruktur

14.369 Zeilen, 315 Tests. Vier Schichten, jede ohne Wissen ueber die andere:

```
core/     Bedienelemente (55), InputLayer, Eingabezustand
sources/  VirtualSource (Maus), HardwareSource (ESP32-Protokoll)
audio/    Decoder, Analyse (BPM/Beatgrid/Waveform/Key), Cache, Engine, Worker
deck/     DeckState, DeckCommand, InputMapper, Deck-Engine, DeckStateProvider
cdj_ui/   CDJ-Bildschirm (1024x600), ein Modul je Bereich
ui/       Hardware-Simulator (Bedienfeld) - getrennt vom CDJ-Bildschirm
app.py    Verdrahtung
```

## 2. Wiederverwendbare Komponenten

Der Auftrag ist zu ueber 70 % Erweiterung, nicht Neubau.

| Gebraucht | Vorhanden | Datei |
| --------- | --------- | ----- |
| Playbackposition | `DeckState.position_s`, sample-genau aus dem Audio-Thread | `deck/state.py`, `audio/engine.py` |
| BPM / Original-BPM / Tempo % | `current_bpm`, `original_bpm`, `tempo_percent`, `tempo_range` | `deck/state.py` |
| Beatgrid, Beat, Takt, Phase | `BeatGrid` + `bar`, `beat`, `beat_phase` | `deck/state.py` |
| Waveform-Peaks | `WaveformSet` mit `overview`/`medium`/`detailed`, RGB-Baender | `audio/analysis/waveform.py` |
| Waveform-Rendering | `WaveformRenderer` (numpy -> PIL -> `PhotoImage`) | `cdj_ui/waveform_render.py` |
| Hot Cues + Loop-Cues | `HotCue` mit `position_s`, `color`, `kind`, `loop_end_s` | `deck/state.py` |
| Memory Cues | `MemoryCue` | `deck/state.py` |
| Loop-Engine | sample-genau im Audio-Callback, 1/4 bis 32 Beats geprueft | `audio/engine.py` |
| Beat Jump | `CommandType.BEAT_JUMP`, beatgrid-basiert | `deck/engine.py` |
| Sync / Master / Quantize | echte Zustandsfelder, keine GUI-Umschalter | `deck/state.py` |
| Key | `TrackInfo.key` (Camelot) + `key_confidence` | `deck/state.py` |
| Zeit | `remaining_s`, `progress` aus Dauer und Position | `deck/state.py` |
| Master-Vergleich | `CdjScreen(master_provider=...)`, Phase Meter | `cdj_ui/master_waveform.py` |
| Track laden | `TrackLoader` + `AnalysisWorker` (eigener Thread) | `audio/loader.py`, `audio/worker.py` |
| Browse-Encoder | `CommandType.BROWSE_ROTATE` / `BROWSE_PRESS` | `deck/mapping.py` |
| Netzwerkvorbereitung | `DeckStateProvider` abstrakt, `NetworkDeckStateProvider` als Platzhalter | `deck/provider.py` |

**Konsequenz:** kein zweites Subsystem. Kein `CDJDisplayPlaybackPosition`.
Die Oberflaeche bleibt Projektion von `DeckState`.

## 3. Fehlende Komponenten

| Fehlt | Einordnung |
| ----- | ---------- |
| Waveform-Modi RGB / 3BAND / BLUE | Darstellung; Daten sind vorhanden |
| Zoomstufen der laufenden Waveform | Darstellung |
| Touch-Panels BEAT LOOP / KEY SHIFT / BEAT JUMP | Darstellung + Kommandos |
| Beat Countdown zum naechsten Cue | Berechnung aus Beatgrid + Cues |
| Zeitmodus ELAPSED/REMAIN per Touch | Anzeigezustand |
| Key Shift | **Zustand ja, DSP nein** - Kategorie B (siehe 10.) |
| Phase Meter statt Wellenform umschalten | Darstellung |
| Player-Nummer, Tracknummer, SINGLE/CONTINUE, MT, A.HOT CUE | Anzeigefelder im Deck-Zustand |
| Track-Info-Popup | Darstellung |
| Browser mit echter Liste + LOAD | braucht eine Trackquelle |
| Demo-Tracks | ausdruecklich erlaubte Ausnahme fuer synthetische Daten |
| Needle Lock | Sicherheitslogik fuer Overview-Touch |
| Master-Sicht ueber Netzwerk | Schnittstelle, kein Transport |

## 4. Erkenntnisse aus dem CDJ-3000-Handbuch

Grundlage: offizielles Handbuch (DRI1588-A), Seiten 18-26 (Touchscreen,
Wiedergabebildschirm, Browse, Jog-Anzeige), 51-84 (Bedienung, Einstellungen).

### Wiedergabebildschirm, 26 Elemente (S. 21-23)

| Element | Inhalt | Quelle des Werts |
| ------- | ------ | ---------------- |
| Anzahl Beats fuer Loop | Beats des aktiven Loops | lokales Deck |
| Vergroesserte Wellenform | Zoom-Wellenform mit Cue/Loop/Hotcue | lokales Deck |
| Beat-Countdown | Takte und Beats bis zum naechsten **gespeicherten** Cue | lokales Deck + Beatgrid |
| Gerätesymbol | Quelle vom SOURCE-Bildschirm | Speichergeraet |
| Track-Informationen | Titel, Laenge, BPM, Tonart | geladener Track |
| ⓘ | Detailinfos des Tracks | geladener Track |
| BEAT LOOP / KEY SHIFT / BEAT JUMP | Touch oeffnet je ein Panel | Bedienung |
| Wellenform-/Phasenmesser | Takt- und Beat-**Abweichung vom Sync-Master** | lokal **und** Master |
| Zoom-/Rastermodus | Drehregler halten schaltet um, drehen zoomt bzw. verschiebt das Grid | Bedienung |
| Beats fuer Beat Jump | `BEAT JUMP BEAT VALUE` | Einstellung |
| Beats fuer Quantisierung | `QUANTIZE BEAT VALUE`, nur bei aktiver Quantisierung | Einstellung |
| Player-Nummer | eingestellte Nummer | PRO DJ LINK |
| Tracknummer | laufende Nummer | Library |
| A. HOT CUE | `HOT CUE AUTO LOAD` aktiv | Einstellung |
| AUTO CUE | Auto Cue aktiv | Einstellung |
| Zeitanzeige | Minuten, Sekunden, **Millisekunden**; `REMAIN` bei Restzeit | Dauer + Position |
| SINGLE / CONTINUE | `PLAY MODE` | Einstellung |
| Wiedergabegeschwindigkeit | Tempo % vom Fader | lokales Deck |
| Einstellbereich | ±6 / ±10 / ±16 / WIDE | lokales Deck |
| BPM | BPM des laufenden Tracks | lokales Deck |
| MASTER / SYNC | Sync-Master bzw. Beat Sync aktiv | PRO DJ LINK |
| MT | Master Tempo aktiv | lokales Deck |
| Tonart | **gruen, wenn sie zur Tonart des Master-Tracks passt** | lokal + Master |
| Gesamte Wellenform | ganzer Track mit Cue/Loop/Hotcue | lokales Deck |

### Touch-Verhalten

* **Kurzer Touch** auf ein Element hebt hervor, **zweiter Touch** bestaetigt
  (S. 25). Im Browser erscheint bei Track-Touch `LOAD`; erst Touch auf `LOAD`
  laedt.
* **Gesamte Wellenform beruehren** bei Pause bzw. im Vinyl-Modus mit
  gedrueckter Jog-Platte: Wiedergabe ab beruehrtem Punkt (S. 47). Waehrend der
  Wiedergabe: **Touch Cue** - Mithoeren ab beruehrtem Punkt ohne den Ausgang
  zu veraendern (S. 49).
* **Gesamte Wellenform beruehren** waehrend der Wiedergabe zeigt zusaetzlich
  die vergroesserte Wellenform des beruehrten Punkts und den Beat-Countdown
  bis dorthin (S. 21).
* **Wellenform-/Phasenmesser** durch Touch umschaltbar (S. 22).
* **Drehregler halten** wechselt zwischen Zoom- und Rastereinstellmodus
  (S. 22, 72).
* **BEAT LOOP** (S. 58): Touch oeffnet Beat-Auswahl 1/4, 1/2, 1, 2, 4, 8, 16,
  32 mit Seitenumschalter; Touch auf einen Wert startet den Loop.
* **BEAT JUMP** (S. 66): Touch oeffnet Beat-Auswahl paarweise mit ◀◀/▶▶.
* **KEY SHIFT** (S. 74): Touch oeffnet Panel mit `−` / `+`, je Druck ein
  Halbton, plus `RESET`.
* **Slip Beat Loop** (S. 68): Beat-Nummer im Panel **halten** loopt nur
  solange gehalten.

### Wellenformfarben (S. 84)

`WAVEFORM COLOR`: `BLUE`, `RGB`, `3 BAND`.
`WAVEFORM CURRENT POSITION`: `CENTER` oder `LEFT`.

### PRO-DJ-LINK-abhaengig

Player-Nummer, MASTER/SYNC, Phasenmesser, gruene Tonart (Verwandtschaft zum
Master), Touch Preview und Touch Cue (brauchen einen kompatiblen Mixer),
Tag-Liste, Instant Doubles.

## 5. Erkenntnisse aus der VirtualDJ-Dokumentation

Ergaenzt vor allem die **Positionen** und zwei Details, die im Handbuch nicht
so deutlich stehen:

* **BPM steht in einem orangefarbenen Rahmen, wenn das Deck Master ist.** Das
  ist die praegnanteste optische Master-Kennzeichnung und wird uebernommen.
* **TIME: Tippen wechselt zwischen Restzeit und verstrichener Zeit.**
* BEAT LOOP / KEY SHIFT / BEAT JUMP liegen **rechts in der Kopfzeile**, jeweils
  als Umschalter fuer ein Panel.
* WAVEFORM-Zoom laeuft ueber den **Browse-Drehregler**.
* PROGRESS WAVE ist der **untere Streifen**, beruehrbar - mit einer
  Needle-Lock-Einstellung.
* TRACK INFO: Antippen oeffnet ein kleines Popup mit Zusatzinfos.

Widersprueche: VirtualDJ nennt `TRACK` die Automix-Nummer und `AUTOCUE` das
Laden am ersten Hotcue - das ist VirtualDJ-Verhalten, nicht CDJ-Verhalten.
Hier gilt das Handbuch.

## 6. Geplantes Displaylayout (1024 x 600)

```
+----------------------------------------------------------------------------+
| ⓘ  [Art] Titel                              | BEAT | KEY  | BEAT |  32 px  |
|          Interpret · Album · Genre           | LOOP | SHIFT| JUMP |  56 px  |
+----------------------------------------------------------------------------+
| Beat-Countdown |  Phasenmesser / Wellenform-Kopf                   28 px   |
+----------------------------------------------------------------------------+
|                                                                            |
|   MASTER-Wellenform (nur wenn ein anderes Deck Master ist)         64 px    |
+----------------------------------------------------------------------------+
|                                                                            |
|   LAUFENDE WELLENFORM   Playhead fest in der Mitte                         |
|   Beatgrid · Hotcues · Loopbereich · Zoomstufe            flexibel ~190 px |
+----------------------------------------------------------------------------+
| PLAYER 1 | TRACK 12 | MT | SINGLE | Zeit 3:12.450 | TEMPO ±10 +2.34 %      |
|          | QUANTIZE 1 | BEAT JUMP 16 |            | BPM 154.00  KEY 4A     |  96 px
+----------------------------------------------------------------------------+
|   PROGRESS / OVERVIEW  ganzer Track, Position, Cues, Loops         56 px    |
+----------------------------------------------------------------------------+
|   Pads A-H | LOOP | SYNC | MASTER | QUANTIZE                       76 px    |
+----------------------------------------------------------------------------+
```

Panels (BEAT LOOP / KEY SHIFT / BEAT JUMP) legen sich als Overlay ueber den
unteren Teil der laufenden Wellenform, wie am Geraet.

Alle Masse ueber `Metrics` skaliert - keine harten Pixelkoordinaten.

## 7. State / Data Flow

```
audio/ (Analyse, Cache)          demo/ (DemoTrackProvider)
        \                       /
         +--> TrackInfo <------+
                  |
   DeckCommand -> Deck -> DeckState  (die einzige Wahrheit)
                            |
                 LocalDeckStateProvider        (spaeter: NetworkDeckStateProvider)
                            |
                  +---------+----------+
                  v                    v
          CdjDisplayState  <-----  MasterDeckView
          (nur Aggregation)        (schlanke Master-Sicht)
                  |
            CdjScreen + Regionen
```

`CdjDisplayState` enthaelt **nur**:

* eine Referenz auf den lokalen `DeckState`
* eine `MasterDeckView` (kann lokal oder ueber Netz gefuellt werden)
* reine **Anzeige**zustaende: Zeitmodus, Zoomstufe, Waveform-Modus, aktives
  Panel, Phasenmesser ein/aus
* **abgeleitete** Werte: Beat Countdown, Zeittext, Tonart-Verwandtschaft

Keine Position, kein Tempo, keine BPM eigener Rechnung. Was im `DeckState`
steht, wird gelesen, nicht nachgerechnet.

`MasterDeckView` ist absichtlich schlank und serialisierbar - genau die Felder,
die spaeter aus dem Netz kommen wuerden: `player_id`, `track_id`, `title`,
`bpm`, `key`, `position_s`, `beat_phase`, `bar`, `beat`, Beatgrid-Parameter,
eine Waveform-Vorschau, `is_master`.

## 8. Demo-Track-Modell

Synthetische Daten sind laut Auftrag nur hier erlaubt. Deshalb ein eigenes
Modul `virtual_cdj/demo/`, das nichts in den Produktionspfad schreibt.

`DemoTrackProvider.tracks()` liefert `TrackInfo`-Objekte **desselben Typs**
wie der echte `TrackLoader`. Der Unterschied steckt nur in der Herkunft der
Zahlen; `TrackInfo.source` ist `"DEMO"`, damit es im Display sichtbar bleibt.

| | Track 1 | Track 2 |
| --- | --- | --- |
| Titel | SHØRDY – Broken Window | SHØRDY – Steel Pressure |
| BPM | 154.0 | 156.0 |
| Key | 4A | 5A |
| Laenge | 5:30 | 5:00 |
| Genre | Industrial / Schranz | Hard Techno / Industrial |
| Struktur | Intro, Build, Drop 1, Break, Drop 2, Outro | anderer Aufbau, mehr Breaks |
| Hot Cues | A-F, davon einer als Loop | mehrere, davon einer als Loop |

Waveform: aus der Struktur erzeugte Baender (Bass/Mitten/Hoehen je Abschnitt),
in allen drei Aufloesungsstufen - dasselbe `WaveformSet`, das die echte Analyse
liefert. Beatgrid: konstantes Tempo aus der BPM.

Optional erzeugt der Provider **echte Audiosamples** aus derselben Struktur,
damit auch Wiedergabe, Loops und Cues hoerbar testbar sind. Das ist keine
Fake-Anzeige, sondern ein synthetischer Track.

## 9. Geplante Dateien

**Neu**

| Datei | Inhalt |
| ----- | ------ |
| `deck/display_state.py` | `CdjDisplayState`, `MasterDeckView`, `TimeMode`, `WaveformMode`, `TouchPanel` |
| `demo/__init__.py`, `demo/tracks.py` | `DemoTrackProvider`, zwei Tracks, synthetische Waveform + Audio |
| `cdj_ui/panels.py` | `BeatLoopPanel`, `BeatJumpPanel`, `KeyShiftPanel` |
| `cdj_ui/status_bar.py` | Player, Track, MT, SINGLE, Quantize, Beat-Jump-Wert, Zeit, Tempo, BPM, Key |
| `cdj_ui/track_info_popup.py` | Popup der Trackdetails |
| `docs/CDJ3000_DISPLAY_ANALYSIS.md` | dieses Dokument |

**Geaendert**

| Datei | Aenderung |
| ----- | --------- |
| `deck/state.py` | `key_shift`, `player_number`, `track_number`, `play_mode`, `quantize_beats`, `hot_cue_auto_load` |
| `deck/commands.py` | `KEY_SHIFT`, `KEY_SHIFT_RESET`, `WAVEFORM_ZOOM`, `TIME_MODE`, `WAVEFORM_MODE`, `PHASE_METER`, `TRACK_INFO` |
| `deck/engine.py` | Handler dafuer; Key Shift als Zustand ohne DSP |
| `deck/mapping.py` | Browse-Drehgeber zoomt in der Waveform-Ansicht |
| `cdj_ui/waveform_render.py` | Modi RGB / 3BAND / BLUE |
| `cdj_ui/scrolling_waveform.py` | Zoomstufen, Loop-/Cue-Overlays, Panel-Bereich |
| `cdj_ui/overview_waveform.py` | Needle Lock |
| `cdj_ui/track_info.py` | Beat Countdown, Zeitmodus per Touch, Master-Rahmen um BPM |
| `cdj_ui/master_waveform.py` | `MasterDeckView` statt kompletter `DeckStateProvider` |
| `cdj_ui/browser.py` | echte Liste, Auswahl, `LOAD` |
| `cdj_ui/screen.py` | Panel-Verwaltung, `CdjDisplayState` |
| `app.py` | Demo-Modus, Master-Zuweisung |
| `run_cdj.py` | `--demo` |

## 10. Klassifizierung nach Reifegrad (Abschnitt 31)

| Kategorie | Funktionen |
| --------- | ---------- |
| **A - vollstaendig** | Playback, Position, Seek, Tempo, BPM, Beat/Takt, Loops (sample-genau), Hot Cues, Beat Jump, Cue, Sync-/Master-/Quantize-Zustand, Waveform-Darstellung, Overview, Zeitanzeige, Beat Countdown, Panels, Browser mit Demo-Quelle, Zoom |
| **B - UI + Zustand, DSP fehlt** | **Key Shift** (Halbtonwert wird gefuehrt und angezeigt, kein Pitch-Shifting im Audio), **Master Tempo / Key Lock** (`KeyLockTempoProcessor` wirft `NotImplementedError`), **Touch Cue / Touch Preview** (brauchen einen Mixer-Kanal) |
| **C - nicht implementiert** | PRO DJ LINK (Netzwerk), Library/Collection ausserhalb des Demo-Providers, Tag-Liste, Instant Doubles, Slip, Beatgrid-Bearbeitung per Drehregler, Automix/CONTINUE-Wiedergabe |

Diese Einordnung wird im Display selbst sichtbar gemacht: Was zu Kategorie B
gehoert, wird als Zustand angezeigt und im Debug-Overlay als `DSP fehlt`
gekennzeichnet.

## 11. Dokumentierte Abweichungen vom Original

| Abweichung | Begruendung |
| ---------- | ----------- |
| **Kein AUTO CUE** | Das Eigenbau-Bedienfeld hat keinen AUTO-CUE-Taster (frueher ausdruecklich festgelegt). Die Anzeige entfaellt konsequent mit. |
| **Master-Wellenform nur bei fremdem Master** | Ist dieses Deck selbst Master, waere die zweite Wellenform identisch. Statt Dopplung wird die Zeile ausgeblendet und die eigene Wellenform bekommt die Master-Kennzeichnung. Entspricht dem Wunsch "lokal gegen Master" statt vier Wellenformen. |
| **Zoom auch per Touch** | Am Geraet nur ueber den Drehregler. Auf einem 7"-Touchscreen ohne Drehregler-Hardware sind zwei Touch-Ziele fuer Zoom die bessere Bedienbarkeit. Der Drehregler bleibt gleichwertig. |
| **Beat Countdown auch zu Hot Cues** | Das Handbuch nennt nur gespeicherte Cues. Hot Cues sind auf diesem Geraet die praktisch genutzten Marker, deshalb werden beide beruecksichtigt. |
| **BPM-Rahmen orange bei Master** | Aus der VirtualDJ-Dokumentation; deutlichste Master-Kennzeichnung. |
| **`TRACK` = Position in der Ladeliste** | VirtualDJ meint die Automix-Nummer, das Handbuch die Tracknummer. Ohne Automix ist die Position in der Browse-Liste der sinnvolle Wert. |

## 12. Risiken

| Risiko | Gegenmassnahme |
| ------ | -------------- |
| Zoom verschiebt Beatgrid oder Marker | Alle Marker werden aus **derselben** Zeit-zu-Pixel-Funktion abgeleitet wie die Wellenform. Ein Test prueft, dass Beat- und Cue-Positionen ueber alle Zoomstufen stabil bleiben. |
| Panels verdecken die Wellenform | Overlay nur ueber dem unteren Teil, mit klarer Aktiv-Kennzeichnung; ein zweiter Touch schliesst. |
| 60 FPS auf Pi-Klasse-Hardware | Waveform bleibt vorberechnet; pro Bild wird nur der sichtbare Ausschnitt in ein wiederverwendetes Bild gerendert. Redraw wird ueber `DeckState.generation` uebersprungen. |
| Overview-Touch versetzt versehentlich den Track | Needle Lock: waehrend laufender Wiedergabe ignoriert der Overview Beruehrungen, sofern nicht ausdruecklich freigegeben. |
| Demo-Daten wandern in den Produktionspfad | Eigenes Paket `demo/`, `TrackInfo.source == "DEMO"`, kein Import aus `audio/` oder `deck/` heraus. |
| Key Shift wirkt hoerbar nicht | Kategorie B, im Debug-Overlay gekennzeichnet, Schnittstelle `TempoProcessor` steht bereit. |
| Master-State kommt spaeter aus dem Netz | `MasterDeckView` ist ein flaches, serialisierbares Datenobjekt ohne Verweis auf ein lokales `Deck`. |

---

Der Abschlussbericht zur Umsetzung steht in `docs/CDJ3000_DISPLAY_REPORT.md`.
