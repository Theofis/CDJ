# Metadaten: getrennt von der Audioanalyse

## Der Fehlerpfad, der dazu gefuehrt hat

Das Auslesen der Datei-Tags ueber `mutagen` hat den **ganzen
Python-Prozess** mitgenommen:

```
Windows fatal exception: code 0x80000003

Current thread 0x000031e4 [analysis-worker] (most recent call first):
  Garbage-collecting
  File ".../mutagen/wave.py", line 117 in _pre_load_header
  ...
  File "virtual_cdj/audio/loader.py", line 65 in _read_tags
  File "virtual_cdj/audio/worker.py", line 139 in _handle
```

Gemessen auf diesem Rechner (Python 3.14, Windows 11):

| Zustand | Gesamtlaeufe der Testsuite |
| ------- | -------------------------- |
| Tag-Lesen aktiv, im Analyse-Thread | 5 von 6 brachen ab |
| Tag-Lesen uebersprungen (Experiment) | 0 von 2 brachen ab |

Das beweist noch keine Ursache in `mutagen` - der Absturz passiert
waehrend der Garbage Collection, und die laeuft nicht deterministisch.
Es zeigt aber eindeutig, **wo** es weh tut, und dass Tag-Parsing dort
nichts zu suchen hat.

Wichtig fuer das Verstaendnis: `0x80000003` ist ein Fehler auf
Interpreter-Ebene. **Ein `try/except` faengt das nicht** - der
Interpreter kommt nie bis zum `except`. Den Aufruf abzusichern reicht
also nicht; er darf nicht im selben Prozess laufen wie die Analyse.

## Die Trennung

```
Track laden
  |
  +-- Audioanalyse    Waveform, Peaks, BPM, Tonart     muss klappen
  |                   virtual_cdj/audio/loader.py
  |
  +-- Metadaten       Titel, Interpret, Genre, Label   darf ausfallen
                      virtual_cdj/audio/metadata.py
```

Die Reihenfolge ist Absicht: `TrackLoader.load()` analysiert zuerst
vollstaendig und holt die Metadaten **danach**. Alles, was im zweiten
Schritt schiefgeht, kann am ersten nichts mehr kaputt machen.

## Verhalten bei einem Fehler im Metadatenpfad

| Was passiert | Folge |
| ------------ | ----- |
| Leseprozess stirbt | nur das Tag-Lesen gilt als gescheitert |
| Leseprozess haengt | Abbruch nach `READ_TIMEOUT_S` (10 s) |
| Datei fehlt oder ist unlesbar | Fehlermeldung, kein Ladefehler |
| Antwort ist kein gueltiges JSON | Fehlermeldung, kein Ladefehler |
| `mutagen` nicht installiert | leere Tags, kein Ladefehler |
| Ausnahme im Leser selbst | Stapel ins Log, kein Ladefehler |

In jedem dieser Faelle gilt:

* die **Audioanalyse ist vollstaendig** - Waveform, Beatgrid, BPM stehen,
* bereits bekannte Metadaten **bleiben erhalten**,
* fehlende Felder bleiben **leer** - der Titel faellt auf den Dateinamen
  zurueck, weil das der einzige Wert ist, der immer da ist und nichts
  erfindet,
* der Grund steht in `LoadedTrack.metadata_error` und im Log
  (`logging.WARNING`),
* gezaehlt wird er in `AnalysisMetrics.metadata_failed` - **getrennt** von
  `failed`, denn ein Ladefehler ist es nicht.

`LoadResult.ok` bleibt `True`. Ein Deck mit fehlendem Genre ist
spielbereit; ein Deck ohne Waveform waere es nicht.

## Prozessisolation: ja

`metadata.py` startet einen kurzlebigen eigenen Prozess:

```
subprocess.run([sys.executable, "-c", _READER_SOURCE, pfad])
```

`_READER_SOURCE` ist ein kleines Skript, das **nur** `json`, `sys` und
`mutagen` importiert - nicht dieses Projekt. Damit bleibt der
Prozessstart billig, und ein Absturz kann nichts anderes beschaedigen.
Ergebnis kommt als JSON auf `stdout`; `returncode != 0` heisst
"Tag-Lesen gescheitert".

Bewusst **nicht** gebaut: Prozess-Pool, Auftragsverwaltung, dauerhafter
Hilfsprozess. Ein `subprocess.run` und ein `dataclass` reichen.

Bewusst **nicht** in den Haupt- oder GUI-Thread verlegt: dort waere
derselbe Absturz schlimmer - er wuerde die Oberflaeche und die laufende
Wiedergabe mitnehmen.

### Was das kostet

Rund 135 ms je Aufruf, gemessen an den Testdateien (WAV, FLAC, MP3). Das
faellt an:

* **nie**, wenn die Metadaten schon bekannt sind (siehe unten) - der
  Normalfall bei rekordbox-Sticks,
* **einmal je Datei und Sitzung**, sonst: `TrackLoader` merkt sich das
  Ergebnis je Analyse-Digest (`_tag_cache`, nur im Speicher).

Er faellt immer im Analyse-Worker an, nie in der Oberflaeche und nie im
Audio-Thread.

## rekordbox hat Vorrang

`TrackLoader.load(..., known=TrackTags(...))`:

1. sind die bekannten Felder **vollstaendig**, wird die Datei fuer die
   Tags ueberhaupt nicht angefasst,
2. sonst werden **nur die leeren** Felder aus den Dateitags ergaenzt
   (`TrackTags.filled_with`) - ein vorhandener rekordbox-Wert wird nie
   ersetzt,
3. faellt das Lesen aus, bleibt das Bekannte unveraendert stehen.

Den Weg dorthin baut `CdjApplication.load_by_track_id()`: es hat den
`TrackInfo` der Bibliothek in der Hand und gibt ihn als `known_tags`
mit. Bei einem rekordbox-Stick stammt der aus `export.pdb` und ist
besser als jeder Dateitag.

Durchgereicht wird das ueber `LoadRequest.known_tags`, damit der
Vorrang auch auf dem Weg durch den Worker nicht verloren geht.

## Verbleibendes Risiko

Die Ursache in `mutagen` ist damit **nicht behoben**, nur eingesperrt.
Konkret bleibt offen:

* Ein Absturz im Leseprozess bleibt ein Absturz - er kostet jetzt
  Metadaten statt des Players. Mehrfach fuer dieselbe Datei kostet er
  jedes Mal die Prozesslaufzeit.
* Warum `mutagen` unter Python 3.14 waehrend der GC faellt, ist nicht
  untersucht. Ein Versionswechsel von `mutagen` oder von Python koennte
  es beheben - oder verschieben.
* Der Tag-Cache ist nur im Speicher. Nach einem Neustart wird wieder
  gelesen.
