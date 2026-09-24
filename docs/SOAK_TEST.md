# SOAK TEST IMPLEMENTATION REPORT

## Architektur

`python -m tools.soak_test` startet einen separaten Supervisor. Dieser startet
die echte `CdjApplication` mit echtem Tk-Display und PortAudio-Ausgabe in einem
Kindprozess. Der Runner sendet die normalen `CommandType`-Befehle durch
`CdjApplication.dispatch()`. Keine zweite Engine, keine Manipulation von
Deck-Interna. Ein unsichtbares Tk-Fenster ist der Default; `--visible` zeigt es.

Der Supervisor bleibt auch bei blockiertem GUI-Thread unabhängig. Vor jeder
Aktion bestätigt er die Speicherung des Aktions-Intents. Der Kindprozess
liefert Zustand und Fortschritt über eine getrennte Telemetrie-Verbindung.

## Neue Dateien

- `tools/soak_test/__main__.py`: CLI, Konfiguration, Replay-Einstieg.
- `supervisor.py`: Prozessüberwachung, Recovery, Checkpoints, Reports.
- `worker.py`: echter Player, GUI, Commands, Beobachtungen.
- `scenarios.py`: Seed/RNG, gewichtete Sessions, Stress-/Extremsequenzen.
- `validation.py`: Zustands-, Loop-, Cue- und Fortschrittsprüfungen.
- `safety.py`: OS-Schreibschutzprüfung, zusätzliche Python-Sperre, SHA-256-Inventar.
- `metrics.py`: Prozessmetriken und RAM-Trend pro Prozessversuch.
- `tests/test_soak_test.py`, `requirements-soak.txt`, diese Dokumentation.

## Geänderte Produktionsdateien

Nur `virtual_cdj/app.py`: zwei kleine Beobachterschnittstellen:
`on_load_requested(deck_id, path)` und `on_load_finished(result)`.
Letztere meldet auch fehlgeschlagene Ladevorgänge. Sie ändern keine
Playerentscheidungen; insbesondere werden Loop-, Slip- und Audio-Logik nicht
für den Test angepasst. `.gitignore` schließt `soak_results/` aus.

## Startbefehl

Voraussetzungen: Projektabhängigkeiten, Tk/Anzeige, funktionierendes
Audio-Ausgabegerät, zusätzlich `psutil`:

```powershell
python -m pip install -r requirements-soak.txt
python -m tools.soak_test --hours 0.5 --seed 82746129 --profile MIXED --usb-source D:\ --continue-on-failure
```

Der Pfad bezeichnet die Wurzel des exportierten USB-Sticks mit
`PIONEER/rekordbox/export.pdb`. Der Test verlangt vor dem ersten Playerstart
OS-Schreibschutz. Ohne `--continue-on-failure` gilt Fail-fast. Die Defaultdauer
ist 30 Minuten; ein 8-Stunden-Test wird nicht automatisch gestartet.

Später denselben Befehl mit `--hours 1`, `8`, `24`, `48` oder `72` verwenden.
Jeder Lauf braucht einen neuen `--output`-Ordner. Die Defaultordner enthalten
einen Zeitstempel. Logs, temporäre Einstellungen und Analysecache liegen lokal,
niemals auf der USB-Quelle. `--cache` bestimmt den lokalen Analysecache.

## Read-Only-Schutz

1. Windows: Datenträger über den Laufwerksbuchstaben auflösen und
   `Get-Disk.IsReadOnly` prüfen. Linux: Read-only-Mount über `statvfs` prüfen.
   Nicht verifizierbarer Schutz führt zum Abbruch, nicht zum Warnungs-PASS.
2. Der Runner installiert vor dem Playerstart zusätzlich einen Python-Audithook,
   der Schreiböffnungen und Dateisystemmutationen unterhalb der Quelle blockiert.
   Native Bibliotheken werden durch den OS-Schutz abgedeckt, nicht durch diesen Hook.
3. SHA-256, Größe und Änderungszeit **aller regulären Dateien** vor/nach dem Lauf.
   Neue, entfernte oder geänderte Dateien sind kritische Fehler. Lesefehler und
   Links/Junctions werden nicht still übersprungen. Zugriffszeiten werden nicht
   verglichen. Vollständiges Hashen kann vor und nach dem Test länger dauern.
4. Schutz und Datenträgeridentität während des Laufs alle 30 Sekunden prüfen.
5. Commands für Cue-Erstellung/Löschung, Beatgrid-Schreiben, Tag-Änderungen,
   Einstellungen und USB-Auswerfen sind nicht freigegeben. PAD darf nur
   bereits belegte Hot-Cue-Slots im HOT_CUE-Modus aufrufen, auch im Replay.

Der Runner ändert keine Datenträgerattribute automatisch und probiert den
Schreibschutz nicht durch einen Schreibversuch auf dem echten Stick aus.
Für Windows kann der Benutzer in einer administrativen PowerShell zuerst
`Get-Partition -DriveLetter D | Get-Disk` prüfen und anschließend **nur den
eindeutig identifizierten USB-Datenträger** mittels
`Set-Disk -Number <Nummer> -IsReadOnly $true` schützen. Nummern können sich nach
erneutem Anschließen ändern. Der eigentliche Player braucht keine Adminrechte.
Ein Hardware-Schreibblocker ist ebenfalls möglich.

## Watchdog

GUI-Heartbeat und Runner-Heartbeat kommen aus unterschiedlichen Rückrufen im
GUI-Thread, Audiofortschritt aus dem echten Callback-Zähler. Der unabhängige
Supervisor unterscheidet `GUI_HANG`, `RUNNER_HANG`, `AUDIO_HANG`,
`PLAYBACK_STALL`, Prozessabsturz, Start-/Ladefehler und ungültigen Zustand.
Default: 10 Sekunden Fortschritts-Timeout, 120 Sekunden Start-Timeout,
600 Sekunden Track-Lade-Timeout. Zeitstempel sind monoton.

Nach Fehler: Beweispaket, kooperatives Beenden, nötigenfalls terminate/kill,
mit `--continue-on-failure` neuer Prozess. Jeder Restart bleibt ein Fehler;
ein nachfolgend gesunder Betrieb macht daraus keinen PASS. Default höchstens
20 Fehlerneustarts, konfigurierbar mit `--max-restarts`.

Gezielte Selbsttests, **ohne echten USB**, aber mit echter GUI/Audioausgabe:

```powershell
python -m tools.soak_test --self-test --hours 0.02 --profile STRESS --seed 42017 --inject gui-freeze --inject-after 4 --watchdog-seconds 3 --continue-on-failure
```

Weitere Injektionen: `audio-freeze`, `runner-freeze`, `crash`. Der absichtlich
fehlerhafte Playerlauf muss FAIL/Exitcode 1 liefern, obwohl damit der
Watchdog-Selbsttest erfolgreich nachgewiesen wird. Injektion nur im ersten
Prozessversuch. `--self-test` kann nicht mit `--usb-source` kombiniert werden
und gilt niemals als bestandener USB-Test.

## Validator

Nach jedem ausgeführten Command und zusätzlich in jedem Runner-Tick:
endliche Position/Tempo/BPM, positive Trackdauer, gültige Loop-Grenzen,
Slip-Hintergrund und konsistente Gründe. STAY-Commands folgen der bestehenden
Matrix aus `docs/LOOP.md`; SEEK und vorhandene Hot Cues berücksichtigen
Sprünge innerhalb/außerhalb des Loops. Track Load muss alten Loop/Slip löschen
und die neuen Metadaten samt Waveform/Grid/Cues übernehmen. Bereits geladene
Hot Cues, Memory Cues und Analysemetadaten dürfen durch Commands nicht verändert
werden. Ein erkannter Fehler stoppt weitere Aktionen sofort.

Playback-Stalls verwenden die tatsächliche Voice-Position und den Loop-Wrap-
Zähler. So wird ein kurzer Loop, der zufällig immer an derselben Position
abgetastet wird, nicht als Stillstand eingestuft. Pause, Vinyl-Hold,
Tempo -100 %, Ladevorgänge und richtungsabhängige Trackgrenzen werden
berücksichtigt. Ein stehengebliebener Vorwärts-Player am Trackanfang ist
ausdrücklich kein legitimer Endzustand.

## Actions

Play/Pause, Cue press/release, vorhandene Hot Cues, Beatloops 1/4–32,
Loop In/Out, Half/Double, Reloop/Exit, Beat Jump, Search, Track Search,
Jog/Scratch/Backspin, Slip, Quantize samt Raster, Vinyl/CDJ, Reverse,
Tempo/Range/Reset, Waveform-Zoom und sichere Source/Browse-Navigation.

Alle Parameterentscheidungen stammen aus einem lokalen `random.Random(seed)`.
Die Gewichtstabelle steht in `scenarios.WEIGHTS`; `--weights weights.json`
ersetzt sie durch eine JSON-Tabelle mit denselben bekannten Aktionsnamen und
nichtnegativen Gewichten. Nullgewichte sind erlaubt, die Summe muss positiv sein.
Trackauswahl über einen gemischten Katalogbeutel: jeder Track einmal vor
Wiederholung, keine dauerhafte Bevorzugung der ersten Dateien. Unterschiede
in BPM, Dauer, Key und Genre werden im Katalog protokolliert; keine künstlich
erfundenen Metadaten zur Erzwingung von Abdeckung.

REALISTIC enthält lange Wiedergabeabschnitte und variable Pausen bis 120 s.
MIXED verwendet Zeitslots mit 7× REALISTIC, 2× STRESS und 1× EXTREME pro
gemischtem Block; geplante Wartezeit etwa 70/20/10. Ladezeit und Ausführung
kommen hinzu, daher keine Garantie dieser Quote für die reale Wanduhrzeit
oder einen einzelnen kurzen Lauf. Wiederaufnahme erhält RNG und Trackbeutel.

## Stress-Szenarien

Unter anderem Loop → mehrfach Half → Backspin → Double → Search → Scratch
→ Search zurück → Exit; Slip+Loop+Scratch+Pause; Wechsel Vinyl/CDJ/Quantize
während Loop; Track Search während Loop; viele aufeinanderfolgende Loads.
EXTREME zielt auf Trackanfang/-ende, Loop-In, 99,9 % Loopposition, sehr kurze
Loops, schnelle Größenwechsel, Reverse/Jog und wiederholte Trackwechsel.
Ladefehler werden separat protokolliert und der nächste Track wird gewählt.

Separater Startup/Shutdown-Test, beispielsweise 100 Zyklen mit ausreichender
Zeitobergrenze (nicht gleichzeitig mit dem ersten 30-Minuten-Test starten):

```powershell
python -m tools.soak_test --hours 8 --usb-source D:\ --seed 77 --lifecycle-cycles 100 --cycle-seconds 5
```

Jeder Zyklus startet einen frischen Prozess, lädt, spielt kurz und schließt
Player/Audio/Worker. Nicht erreichte Zielzyklen führen zu FAIL. Prozessende
gibt die OS-Handles frei; das ist kein Beweis für beliebig häufiges
In-Process-Neuöffnen innerhalb derselben Python-Instanz.

## Metrics

Alle 5 Sekunden CSV: RSS/Peak, CPU (kann bei mehreren Kernen über 100 % liegen),
Thread- und Handlezahl, vorhandene GUI-FPS, Audio-Speicher, Actions/Tracks, Underruns und Callback-
Mittel/Maximum. Audio-Überläufe werden ebenfalls gezählt. Unter Linux zusätzlich
Load und Temperatur, sofern das Standard-Thermal-Interface vorhanden ist.

RAM-Trend pro Prozess, ohne Neustarts zu vermischen; Regression nach 60 s
Warm-up. Warnung bei mindestens 5 Minuten, >100 MB Wachstum und >50 MB/h
Steigung. Trackgrößen und Decoder-Caches können Wachstum erklären: eine
Steigung allein ist kein bewiesenes Leak. RSS über `--max-rss-mb` (Default
4096 MB) ist ein kritischer Fehler. Pi-Throttling wird noch nicht erfasst.

Der Bericht trennt summierte Prozesslaufzeit von beobachtet gesundem Betrieb.
Startup, Loading und Recovery werden nicht als gesund gezählt. Die Abtastung
macht diese Zeiten zu konservativen Beobachtungswerten, nicht samplegenauen
Audio-Zeitmessungen. Audioartefakte ohne Underrun sind ohne externen Loopback
nicht beweisbar.

## Failure Package

Je Fehler eigener Ordner mit `summary.txt`, `seed.txt`, `actions.log`,
`state.json`, `checkpoint.json`, `audio_metrics.json`, `system_metrics.csv`,
`stacktraces.txt`, `exception.txt`. Letzte 2000 Aktionen im Rolling Buffer,
letzte Systemmessungen, alle erreichbaren Threadstacks. Bei hartem Prozessabsturz
kann naturgemäß kein frischer Python-Stack mehr abgerufen werden. Nichtfinite
Fehlerwerte werden als `"nan"`/`"inf"` erhalten und verhindern das Paket nicht.

Checkpoints alle 30 Sekunden: RNG, Aktionsnummer, ausstehende Commands,
Wartezeit, vollständiger kompakter Deckzustand, Audio und Systemmetriken.
Normale Playerlogs rotieren bei 1 MB mit zwei Backups. Für Replay wird nur
der komprimierte Aktionsstrom dauerhaft gespeichert, nicht jeder GUI-Tick.

## Reproduzierbarkeit

```powershell
python -m tools.soak_test --hours 0.5 --usb-source D:\ --replay soak_results/LAUF/replay.jsonl.gz --until-action 18346
python -m tools.soak_test --hours 0.5 --usb-source D:\ --replay soak_results/LAUF/replay.jsonl.gz --until-action 18346 --fast-replay
```

`--until-action` ist inklusiv. Gleicher Seed erzeugt denselben Plan, solange
Katalog und Bedingungen gleich bleiben. Der aufgezeichnete Commandstrom ist
die genauere Reproduktion zustandsabhängiger Entscheidungen. Echtzeit-Audio,
Load-Dauer und Scheduler bleiben nichtdeterministisch; Fast Replay lässt
Wartezeiten weg, ersetzt aber weder Callback noch Decoder durch eine Fake-Engine.
Ein zu knappes Zeitbudget für Replay führt zu FAIL. Recovery stellt nicht
einen beschädigten Audio-/GUI-State wieder her, sondern beginnt mit frischem
Player und fortgesetztem RNG. Replay über Prozessneustartgrenzen ist nicht
samplegenau und bildet diese Grenzen derzeit nicht automatisch nach.

## Tests des Testsystems

```powershell
python -m unittest tests.test_soak_test -v
python -m unittest discover -s tests -q
```

Automatisierte Verträge: deterministischer Seed/Checkpoint, Trackfairness,
Profilanteile, Validator gegen reale Deck-Commands und gezielt defekte
Zustände, Cue-Sperren, getrennte Heartbeats/Stalls, kurze Loop-Aliasing-Fälle,
SHA-Inventar, Schreibsperre im isolierten Prozess und ungültige JSON-Zustände.
USB-Schreibschutztests verwenden ausschließlich temporäre Testdateien oder
Mocks; kein absichtlicher Schreibversuch auf dem echten Medium.

Die konkreten Integrationsläufe und der Stand des echten USB-Laufs werden
separat in `SOAK_TEST_VERIFICATION.md` festgehalten.

## Bekannte Grenzen

- Die bestehende Produktionspipeline lädt `export.pdb`, übernimmt beim
  Dateiladen aber derzeit keine USB-ANLZ-Hot-/Memory-Cues. Analyse und Beatgrid
  stammen dann aus dem normalen Dateilader. Die Cue-Abdeckung ist daher nicht
  vollständig und wird im Report ausdrücklich markiert. Keine Playerlogik
  wurde für den Test ersetzt.
- Der Dateilader vergibt dateibasierte Track-IDs und nimmt geladene Dateien
  in die FILES-Quelle auf. TRACK SEARCH kann deshalb deren Reihenfolge statt
  der ursprünglichen USB-Katalogreihenfolge verwenden. Der Harness verschleiert
  das nicht durch eigene Track-Search-Logik.
- Erster Schritt ist Windows/Legacy-export.pdb, ein Deck, lokale Bibliothek.
  Device Library Plus, Link-Netzwerk und Mehrdeck-Synchronisation sind nicht
  abgedeckt. Linux/Raspberry-Pi-Ausführung ist hier nicht praktisch verifiziert.
- Vollständige Dateidekodierung kann kurzzeitig viel RAM benötigen.
- Source/Browse-Commands laufen in der echten Oberfläche; eine unsichtbare
  Oberfläche beweist keine sichtbare Bildqualität. Dafür `--visible` verwenden.
- Kein genereller Beweis, dass jeder native fehlgeschlagene Schreibversuch
  gezählt wird: der OS-Schreibschutz verhindert Änderungen; Python-Schreibversuche
  werden zusätzlich erkannt. Schutz darf während des Tests nicht extern
  entfernt werden.
- PASS ist kein Vollabdeckungsbeweis. Fehlende Cues, Underruns oder RAM-Trends
  liefern Warnungen; Crash/Hang/Invalid State/USB-Verletzung liefern immer FAIL.
