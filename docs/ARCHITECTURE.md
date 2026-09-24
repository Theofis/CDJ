# Architektur

## Grundregel

Die Oberflaeche kennt keine CDJ-Funktion, und die CDJ-Logik kennt keine
Signalquelle. Zwischen beiden steht genau eine Schicht.

```
+---------------------------+        +-------------------------------------+
| Virtual CDJ               |        | ESP32 / MCP23017 / ADC / Encoder    |
| ui/panel.py, ui/widgets.py|        | (Transport noch nicht implementiert)|
+------------+--------------+        +------------------+------------------+
             |                                          |
             |                                    jog/  (Lichtschranken,
             |                                          Touch, Positionsstand)
             v                                          v
   sources/virtual.py                          sources/hardware.py
   VirtualSource                               HardwareSource
   source = VIRTUAL                            source = HARDWARE
             |                                          |
             +--------------------+---------------------+
                                  v
                    +---------------------------+
                    | core/input_layer.py       |
                    | InputLayer                |
                    |  - ID gegen Liste pruefen |
                    |  - Werte normieren        |
                    |  - Zustand fuehren        |
                    |  - InputEvent verteilen   |
                    +-------------+-------------+
                                  |
              +-------------------+-------------------+
              v                   v                   v
   shell/router.py         ui/debug_view.py    ui/monitor_view.py
   InputRouter             letztes Ereignis    Ereignisliste
   (Application_Mode)
              |
              |  nur im Modus PERFORMANCE
              v
   deck/mapping.py         InputMapper
              |
              v
   deck/commands.py        DeckCommand
              |
              v
   deck/controller.py      DeckController
              |
   deck/mode_manager.py    ModeManager (MIDI oder CDJ)
              |
   deck/backend.py         MidiBackend | CdjBackend
              |
   deck/engine.py          Deck   (Transport, Loop, Cue, Tempo, Beat)
              |
              v
   deck/state.py           DeckState
              |
              v
   deck/provider.py        DeckStateProvider
              |            (LocalDeckStateProvider | NetworkDeckStateProvider)
              v
   cdj_ui/screen.py        CdjScreen - die Bildschirmoberflaeche
```

Die Bibliothek haengt als zweiter Strang an derselben Oberflaeche. Sie
laeuft nie ueber die Eingabekette, sondern fuellt das Browse-Modell:

```
   USB-Stick (nur lesend)
        |
   media_library/volumes.py     welche Datentraeger gibt es?
        |
   media_library/rekordbox/     export.pdb, ANLZ - Formatkenntnis
        |
   media_library/devices.py     UsbDeviceService (Lesethread)
        |
   media_library/sources.py     Bruecke ins Browse-Modell
        |
   deck/library.py              MediaLibrary / SourceInfo / TrackInfo
        |
   cdj_ui/source_screen.py      SOURCE
   cdj_ui/browser.py            BROWSE / PLAYLIST / TAG LIST / HISTORY
```

PLAYLIST und TAG LIST sind keine eigenen Bildschirme, sondern Kategorien
derselben Bibliothek - eine Trackliste, eine Sortierfunktion, eine
Ladefunktion. Siehe [BROWSE_LISTS.md](BROWSE_LISTS.md).

Die Oberflaeche greift nie selbst auf eine Datei oder Datenbank des Sticks
zu. Sie kennt nur `MediaLibrary`. Geschrieben wird auf den Stick nichts:
in `media_library/` gibt es keinen Schreibpfad.

Externe Player bilden einen dritten, vom lokalen Deck getrennten Strang:

```text
Simulator oder echtes PRO DJ LINK (read-only)
        |
prolink/provider.py       ein Vertrag fuer Simulator, Real und Null
        |
PlayerState / BeatEvent   normalisierte Live-Daten
        |
sync/master.py            MasterManager -> MasterState
        |
sync/engine.py            SyncEngine -> SyncTarget (kein Audiozugriff)
        |
CdjApplication            Composition Root / GUI-Thread-Grenze
        |
MasterDeckView            flache Sicht fuer die bestehende GUI
```

Ein RemotePlayer ist kein lokales Deck: er besitzt weder AudioEngine noch
Jog-/Cue-/Loop-Transport. Netzwerkthreads stellen der GUI niemals direkt
Zustand zu; der Provider puffert und `CdjApplication.tick()` verteilt im
Anwendungsthread. Details und Startbefehle: [PROLINK_PHASE1.md](PROLINK_PHASE1.md)
und [PROLINK_PHASE2.md](PROLINK_PHASE2.md).

Noch nicht angebunden und deshalb auch nicht vorgetaeuscht: Beatgrid,
Waveform und Cues aus den ANLZ-Dateien, SEARCH, TRACK FILTER, TAG LIST.
Siehe [rekordbox-usb-import.md](rekordbox-usb-import.md).

Alle Verbraucher haengen als Abonnenten an der Input-Schicht, nicht am Panel.
Ein spaeter per Hardware erzeugtes Ereignis erscheint dadurch ohne weitere
Aenderung in Debug-Ansicht und Input-Monitor.

## Module

| Modul | Aufgabe |
| ----- | ------- |
| `core/model.py` | Typen: `Control`, `InputEvent`, `ControlType`, `EventType`, `Source`, `Direction` |
| `core/ids.py` | alle Control-IDs als Konstanten, Namenskonvention |
| `core/controls.py` | zentrale Komponentenliste: ID, Beschriftung, Typ, Gruppe, Position, LED, Status |
| `core/state.py` | aktueller Zustand: gedrueckte Tasten, Analogwerte, Encoder-Summen, Schalter, Jog |
| `core/input_layer.py` | einziger Eingang fuer alle Signale |
| `core/hardware_map.py` | Zuordnung Control-ID -> physikalische Quelle |
| `core/controller.py` | Platzhalter fuer die spaetere CDJ-Logik |
| `sources/virtual.py` | Maus -> Input-Schicht |
| `sources/hardware.py` | Geraeteprotokoll -> Input-Schicht, dazu `poll_jog()` |
| `sources/jog_input.py` | `VirtualJogInput` / `HardwareJogInput` - eine Jog-Schnittstelle fuer Maus, Touchscreen und ESP32 |
| `jog/` | Sensorauswertung des Jogwheels: Quadratur, Touch, Positionsstand, siehe [JOG.md](JOG.md) |
| `ui/jog_calibration.py` | Kalibrierung des Touch-Sensors (`python run_jog.py`) |
| `shell/modes.py` | `ApplicationMode`, `ModeController` - welche Ebene gilt |
| `shell/router.py` | `InputRouter`: Eingaben je Modus verteilen oder sperren |
| `shell/hardware_state.py` | `HardwareState` - zentrale Sicht auf alle Ein-/Ausgaenge |
| `shell/calibration.py` | Kalibrierwerte laden, aendern, speichern |
| `cdj_ui/pages.py` | `PageHost`: zeigt die Seite zum aktuellen Modus |
| `cdj_ui/menu_page.py`, `test_page.py`, `calibration_page.py`, `settings_page.py` | die Seiten neben der Performance-Oberflaeche, siehe [MODES.md](MODES.md) |
| `ui/panel.py` | baut das Bedienfeld aus der Komponentenliste |
| `ui/widgets.py` | Taster, Fader, Poti, Encoder, Schalter, Jog, Joystick, LED |
| `ui/debug_view.py` | letztes Ereignis mit allen Feldern |
| `ui/monitor_view.py` | Liste der letzten Ereignisse, Filter, Export |
| `deck/state.py` | `DeckState`, `TrackInfo`, `BeatGrid`, `WaveformData`, `HotCue`, `LoopState` |
| `deck/backend.py` | gemeinsamer Backend-Vertrag sowie MIDI-/CDJ-Mockadapter |
| `deck/mode_manager.py` | persistente Auswahl und kontrollierter Backend-Lebenszyklus |
| `deck/controller.py` | einzige Command-/State-Fassade fuer Controls und GUI |
| `deck/commands.py` | `DeckCommand` - Absicht statt Eingabeereignis |
| `deck/mapping.py` | Control-ID -> Deck-Kommando |
| `deck/loop.py` | `LoopEngine`: die gesamte Loop-Logik als reine Rechnung, siehe [LOOP.md](LOOP.md) |
| `deck/quantize.py` | `quantize_position()`: die **eine** Stelle, an der auf das Beatgrid gerastet wird, siehe [PLAYER_MODES.md](PLAYER_MODES.md) |
| `deck/slip.py` | `SlipEngine`/`SlipState`: die Hintergrund-Zeitachse von SLIP als reine Rechnung, siehe [PLAYER_MODES.md](PLAYER_MODES.md) |
| `deck/engine.py` | `Deck`: Transport, Position, Tempo, Beat/Takt, Loop, Cue, Hotcues. **Kein Audio** |
| `deck/provider.py` | Zustandsquelle, lokal oder spaeter ueber Netzwerk |
| `deck/library.py` | `MediaLibrary`, `TrackListLibrary`, `SourceInfo` - das Modell hinter SOURCE, BROWSE, PLAYLIST und TAG LIST, dazu `sort_entries()` |
| `media_library/taglist.py` | `TagListService` - Tag List je Datentraeger, nur im Speicher |
| `media_library/volumes.py` | angeschlossene Datentraeger, dynamisch abgefragt |
| `media_library/devices.py` | `UsbDeviceService`: anstecken, lesen (eigener Thread), abziehen |
| `media_library/model.py` | `DeviceLibrary`, `LibraryTrack`, `PlaylistNode` - internes Bibliotheksmodell |
| `media_library/sources.py` | Bruecke: Datentraeger -> `SourceInfo`/`TrackInfo` |
| `media_library/rekordbox/` | `export.pdb` und die ANLZ-Dateien, **nur lesend** |
| `cdj_ui/` | CDJ-Bildschirmoberflaeche, ein Modul je Bereich |
| `prolink/models.py` | `PlayerState`, `BeatEvent`, getrennte TrackMetadata/TrackAnalysis und `RemotePlayer` |
| `prolink/provider.py` | quellenunabhaengiger Provider-Vertrag und Nullquelle |
| `prolink/simulator_provider.py` | reconnectender TCP-Adapter; Zustellung erst in `poll()` |
| `prolink/packets.py` | belegte, reine Decoder fuer passive UDP-Pakete; kein Encoder/Senden |
| `prolink/real_provider.py` | passiver UDP-Empfaenger, stale/reconnect und Raw-Diagnose; Zustellung in `poll()` |
| `sync/` | `MasterManager`, `MasterState`, `SyncEngine`, `SyncTarget`; kein Audio und keine GUI |
| `simulator/` | separat startbarer FakePlayer, localhost-Server und Entwicklerfenster |

## Das Ereignis

```python
InputEvent(
    control_id="PLAY",
    control_type=ControlType.DIGITAL_BUTTON,
    event=EventType.PRESS,
    source=Source.VIRTUAL,
    timestamp=1756633671477.0,   # ms
    value=1,
    delta=None,
    direction=None,
    position=None,
    raw=None,
    meta={},
)
```

Welche Felder gefuellt sind, haengt am Typ:

| Control-Typ | Ereignis | gefuellte Felder |
| ----------- | -------- | ---------------- |
| `DIGITAL_BUTTON` | `PRESS` / `RELEASE` | `value` = 1 / 0 |
| `ANALOG_FADER`, `ANALOG_POT` | `VALUE` | `value` 0.0 - 1.0, `raw` falls von Hardware |
| `ENCODER` | `ROTATE` | `delta` relativ, `direction` |
| `JOG` | `MOVE` | `delta`, `direction`, `meta` mit Geschwindigkeit |
| `SWITCH` | `POSITION` | `position`, `value` = Index |
| `JOYSTICK` | `AXIS` | `value` -1.0 - 1.0, `meta["axis"]` |

## Zusicherungen der Input-Schicht

* **Kein Toggle.** Ein Taster ist gedrueckt oder nicht. Mehrere Taster koennen
  gleichzeitig gedrueckt sein; `state.pressed_ids()` liefert die vollstaendige
  Kombination.
* **Genau ein Ereignis pro Flanke.** Ein zweites `press()` auf einen bereits
  gedrueckten Taster erzeugt nichts. Ein `release()` ohne vorheriges `press()`
  erzeugt nichts. Das entprellt gepollte Hardware ohne Zusatzcode.
* **Analog immer normiert.** Rohwerte (z. B. ADC 0 - 4095) werden in
  `set_analog_raw()` sofort auf 0.0 - 1.0 umgerechnet. Der Rohwert wird nur im
  Feld `raw` zur Diagnose mitgefuehrt und darf in der CDJ-Logik nicht benutzt
  werden.
* **Encoder relativ.** `rotate()` liefert `+1`, `-2` usw., keinen Absolutwert.
* **Jog ist kein Poti.** Eigener Typ `JOG`, eigene Methode `jog_move()` mit
  `delta`, `direction`, `timestamp` und geglaetteter Geschwindigkeit in
  `meta["velocity_ticks_per_s"]` / `meta["rev_per_s"]`. Damit sind schnelle
  Bewegungen und Backspins auswertbar, ohne dass jeder Verbraucher selbst
  mitzaehlt.
* **Jog-Touch ist eigenstaendig.** `JOG_TOUCH` ist ein normaler digitaler
  Zustand und unabhaengig von `JOG_MOVE`. Beide Zustaende existieren
  gleichzeitig; `JOG_MOVE`-Ereignisse fuehren in `meta["touched"]` mit, ob
  gerade beruehrt wird.
* **Zustand vor Benachrichtigung.** Der Zustand ist aktualisiert, bevor die
  Abonnenten aufgerufen werden. Ein Listener sieht bei einer Kombination
  daher immer den vollstaendigen Zustand.
* **Unbekannte IDs werden abgewiesen.** `UnknownControlError` bei IDs, die
  nicht in `controls.py` stehen; `ControlTypeMismatch`, wenn die Eingabeart
  nicht zum Typ passt (z. B. `set_analog("JOG_MOVE", ...)`).

## LED-Ausgang

Die Gegenrichtung nutzt dieselbe Abstraktion: `InputLayer.set_led(id, on)`
setzt den Zustand, die Oberflaeche spiegelt ihn. Spaeter schreibt derselbe
Aufruf ueber die Hardware-Zuordnung auf einen MCP23017-Ausgang. Der Knopf
"LED-Test" im Statusbereich schaltet alle LEDs um.

## Was bewusst fehlt

* CDJ-Funktionen. `CdjController.functions` ist vollstaendig `None`.
* Die Display-GUI. Der Displaybereich im Panel ist ein Platzhalter.
* Der Transport der Hardware-Quelle. `HardwareSource.start()` ist leer; der
  Protokoll-Dekoder `feed_line()` ist dagegen fertig und getestet.
