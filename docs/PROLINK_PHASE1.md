# PRO LINK Phase 1: interne Schnittstelle und Simulator

Diese Phase implementiert absichtlich **keine** Pioneer-/AlphaTheta-Pakete.
Sie schafft eine interne, testbare Grenze, die spaeter sowohl ein echter
Read-only-Adapter als auch der Entwicklungssimulator bedienen kann.

## Datenweg

```text
separater Simulatorprozess
  FakePlayer (monotonic_ns)
    -> versioniertes Newline-JSON auf localhost TCP
      -> SimulatorProLinkProvider (Netzwerkthread: nur Bytes in Queue)
        -> CdjApplication.tick() (Anwendung/GUI-Thread)
          -> PlayerState / BeatEvent
            -> MasterManager
              -> MasterState
                -> SyncEngine -> SyncTarget
                -> vorhandener MasterDeckView -> vorhandene CDJ-GUI
```

Der Simulator greift weder auf GUI noch Deck oder AudioEngine zu. Die
AudioEngine kennt umgekehrt weder den Provider noch das IPC-Format. Ein
`SyncTarget` beschreibt in Phase 1 nur Ziel-BPM, Phasenfehler und eine
begrenzte relative Korrektur; er wird noch nicht auf Audio angewandt.

## Vorhandene Modelle und Wiederverwendung

- `deck.state.DeckState` bleibt die Wahrheit des lokalen Players. Er besitzt
  bereits Transport, Position, Original-/aktuelle BPM, Pitch, Beat/Takt/Phase,
  Sync/Master, Key, Cue, Loop und Jog.
- `deck.state.TrackInfo` bleibt aus Kompatibilitaetsgruenden unveraendert. An
  der externen Grenze trennen `TrackMetadata` und `TrackAnalysis` Beschreibung
  und Analyse. `TrackAnalysis` verwendet die vorhandenen Typen `BeatGrid`,
  `WaveformData`, `HotCue` und `MemoryCue` statt paralleler Formate.
- `deck.display_state.MasterDeckView` war bereits eine flache Netzwerksicht.
  Es bekam nur `source_type`, damit externer Player 1 nicht mit lokalem Deck 1
  verwechselt wird.
- Der vorhandene Command-Weg fuer MASTER/SYNC bleibt bestehen. Die neue
  Sync-Schicht liest den resultierenden DeckState; aktive ProLink-Kommandos
  sind nicht Teil dieser Phase.

## Provider-Vertrag

`virtual_cdj.prolink.provider.ProLinkProvider` definiert:

- Lifecycle: `start()`, `stop()`, `poll()`
- Snapshots: `get_players()`
- Ereignisse: `subscribe_player_state()`, `subscribe_beat_events()`
- optionale Trackdaten: `get_track_metadata()`, `get_track_analysis()`

`NullProLinkProvider` ist die explizite Quelle im rein lokalen/MIDI-Betrieb.
`SimulatorProLinkProvider` reconnectet selbststaendig. Der strikt passive
`RealProLinkProvider` aus Phase 2 implementiert denselben Vertrag; SyncEngine
und GUI mussten dafuer nicht geaendert werden.

## Start

Terminal 1:

```bash
python -m virtual_cdj.simulator
```

Terminal 2:

```bash
python run_cdj.py --prolink-source simulator --decks 3
```

Der Standard-Endpunkt ist `127.0.0.1:17600`. Abweichend:

```bash
python -m virtual_cdj.simulator --port 17601
python run_cdj.py --prolink-source simulator --prolink-port 17601 --decks 3
```

Fuer einen Server ohne Entwicklerfenster gibt es `--headless`; mit
`--play --master --bpm 154` kann ein automatischer Smoke-Test sofort laufen.
Reproduzierbare Ein-Player-Abläufe stehen über `--scenario normal`,
`bpm-change`, `disconnect`, `track-change` und `sync-toggle` bereit. Ein
Masterwechsel zwischen mehreren simulierten Playern folgt erst mit der
Mehrspieler-Erweiterung; Phase 1 erfindet dafür keinen zweiten Datenweg.

Im Simulatorfenster lassen sich Online, Master, Sync, On Air, Wiedergabe,
Track, effektive BPM und Position aendern. Original-BPM bleibt getrennt;
Pitch wird daraus berechnet. Bei Wiedergabe laufen Position und BeatEvents
von einer monotonen Uhr, nicht vom Tk-Bildtakt.

## Fehlerverhalten und Grenzen

- Unvollstaendige optionale Werte (`position_ms=None`, fehlende Metadaten oder
  Analyse) sind normale Zustände.
- Bei Socketverlust werden bekannte Player lokal offline markiert. Die App
  bleibt aktiv und der Client reconnectet.
- Kaputte, zu grosse oder unbekannte Nachrichten werden protokolliert und
  verworfen; die Verbindung beziehungsweise spaetere Nachrichten bleiben
  nutzbar.
- Externe monotone Zeitstempel sind Diagnosewerte. BeatEvents werden am
  Adapter auf die monotone Zeitbasis des empfangenden Prozesses normalisiert.
- Ein Remote-Master gilt nach drei Sekunden ohne State als stale.
- Phase 1 uebertraegt noch keine Waveform-/Librarydaten und sendet keine
  aktiven PRO-DJ-LINK-Kommandos.
