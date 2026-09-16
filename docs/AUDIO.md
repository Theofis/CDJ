# Audio- und Analyseschicht

## Signalweg

```
Track File
    |  audio/decoder.py      AudioDecoder -> SoundFileDecoder (libsndfile)
    v
AudioBuffer                  float32, stereo, -1..+1   (audio/format.py)
    |  audio/resample.py     soxr, nur beim Laden
    v
AudioBuffer @ 44100 Hz
    |
    +-- audio/analysis/       Waveform, Tempo/Beatgrid, Tonart
    |        |
    |        v
    |   TrackAnalysis  <-->  audio/cache.py   .npz je Track
    |
    v
audio/loader.py  TrackLoader
    |
    +-------------------------------+
    v                               v
DeckVoice (audio/engine.py)     TrackInfo (deck/state.py)
    |  PortAudio-Callback            |
    v                               v
Ausgabe                          Deck  ->  DeckState  ->  CDJ-Oberflaeche
```

## Threads

| Thread | Aufgabe | Darf nicht |
| ------ | ------- | ---------- |
| GUI | Tkinter, Bildschleife, Worker-Ergebnisse abholen | analysieren, dekodieren |
| Audio (PortAudio) | Position fuehren, Tempo, Loops, Puffer fuellen | Dateien lesen, allozieren, GUI anfassen, blockieren |
| Analyse-Worker | Dekodieren, Resampeln, Analysieren, Cache | GUI direkt aufrufen |

Der Audio-Callback holt Steuerbefehle mit `queue.SimpleQueue.get_nowait()` -
das blockiert nie. Die Position schreibt er in ein einfaches Attribut, das der
GUI-Thread liest.

## Interne Repraesentation

* `float32`, Wertebereich -1.0 bis +1.0
* immer **stereo**, Form `(frames, 2)`, C-kontinuierlich
* feste Engine-Rate 44100 Hz; alles andere wird beim Laden mit soxr umgesetzt
* Mono wird auf beide Kanaele gelegt, Mehrkanal auf Stereo reduziert

Speicherbedarf: ein Stereo-Track liegt vollstaendig im RAM. Das macht Seek und
Scratch ohne Plattenzugriff im Callback moeglich.

| Tracklaenge | Speicher |
| ----------- | -------- |
| 5 min | 105 MB |
| 7 min | 148 MB |
| 4 Decks x 7 min | 593 MB |

Der aktuelle Wert steht im Debug-Overlay unter `Speicher`.

## Analyse

### BPM und Beatgrid

Grundlage ist librosa. Drei Nachbehandlungen sind noetig, sonst ist das
Ergebnis fuer DJ-Material unbrauchbar:

1. **Oktavfaltung** in das Fenster 82-164 BPM. Gemessen an einem
   124-BPM-Testsignal liefert librosa 62.26 BPM.
2. **Ausgleichsgerade** durch alle Beatzeiten statt Median der Abstaende. Die
   Beatzeiten sind auf 11.6 ms gequantelt; der Fit mittelt das heraus.
   Ergebnis: 123.985 BPM bei einem Sollwert von 124.000, Residuum 7.4 ms.
3. **Downbeat mit Konfidenz.** librosa liefert keine Downbeats. Geschaetzt
   wird ueber die Onset-Energie im Bassband. Unterhalb einer Konfidenz von
   1.6 gilt der Downbeat als unbestimmt und es wird Phase 0 gemeldet statt
   eine Behauptung aufzustellen. Der Wert steht in
   `TrackInfo.downbeat_confidence`.

Die Deck-Engine schaetzt **nie** BPM. Es gilt
`current_bpm = original_bpm * tempo_factor`, und Beat und Takt kommen aus
`BeatGrid` plus Abspielposition.

### Manuelle Korrektur

Automatische Analyse ist nie perfekt. `BeatGrid` ist unveraenderlich und
liefert Korrekturen als neue Objekte:

| Methode | Wirkung |
| ------- | ------- |
| `shifted(delta_s)` | ganzes Raster verschieben |
| `with_bpm(bpm, anchor_s=...)` | Tempo aendern, Ankerpunkt festhalten |
| `with_downbeat_at(position_s)` | Taktanfang setzen |
| `resegmented_from(position_s, bpm, until_s)` | ab einem Punkt neues Tempo; ergibt eine explizite Beatliste und damit variables Tempo |

Eine Bedienoberflaeche dafuer gibt es noch nicht.

### Waveform

Drei Aufloesungsstufen, einmalig vorberechnet:

| Stufe | Peaks/s | fuer |
| ----- | ------- | ---- |
| `overview` | 20 | Uebersicht des ganzen Tracks |
| `medium` | 80 | mittlerer Zoom |
| `detailed` | 320 | laufende Waveform |

Bandaufteilung mit einer Butterworth-Filterbank: rot bis 200 Hz, gruen
200-2000 Hz, blau darueber. Alle Baender werden gemeinsam normiert, damit die
Farbanteile untereinander vergleichbar bleiben.

`WaveformSet.level_for(seconds_per_pixel)` waehlt die feinste Stufe, die noch
mindestens einen Peak je Pixel liefert. Die Oberflaeche rechnet nichts - sie
zeichnet mit numpy ein Bild und blittet es als `PhotoImage`.

### Tonart

Chroma-Profil gegen die Krumhansl-Schmuckler-Profile in allen zwoelf
Transpositionen, Ausgabe als Camelot (`8A`). Unterhalb einer Konfidenz von
1.08 wird keine Tonart behauptet.

## Cache

* Schluessel: Pfad, Dateigroesse, Aenderungszeit, `ANALYSIS_VERSION`
* Ablage: `cache/analysis/<name>-<hash>.npz`
* Skalare als JSON-Block, Arrays als numpy; Waveform-Peaks als `uint8`
  (1/255 Auflösung ist bei wenigen hundert Pixeln nicht sichtbar und
  viertelt die Dateigroesse)
* beschaedigte Eintraege werden verworfen und neu analysiert
* Aenderung eines Analyseverfahrens: `ANALYSIS_VERSION` in
  `audio/analysis/analyzer.py` erhoehen, alte Eintraege verfallen automatisch

Gemessen an einem 20-Sekunden-Track: erste Analyse 4.7 s, danach 0.013 s aus
dem Cache.

## Temporegelung

`TempoProcessor` ist die Schnittstelle im Callback.

| Verfahren | Zustand | Tonhoehe |
| --------- | ------- | -------- |
| `SimpleResamplerTempoProcessor` | in Betrieb | folgt dem Tempo (wie Vinyl) |
| `KeyLockTempoProcessor` | Platzhalter, `NotImplementedError` | bliebe konstant |

Der Resampler arbeitet mit linearer Interpolation und vorbelegten Puffern,
also ohne Allokation pro Callback.

## Loops

Loops werden **im Audio-Callback** geschlossen, nicht per GUI-Tick. Beim
Umlauf wird der Ueberhang exakt uebertragen:

```
position = loop_in + (position - loop_out) % loop_length
```

Messung mit einem Rampensignal, Loopl'aenge 1 Beat, 23 s Ausgabe:
46 Umlaeufe bei 46.4 erwarteten, kein Sample ausserhalb der Grenzen, kein
Ueberschreiten der Loopgrenze. Geprueft fuer 1/4, 1/2, 1, 2, 4, 8, 16 und 32
Beats sowie rueckwaerts.

Kuerzeste zulaessige Loopl'aenge: 32 Samples. Kuerzere Loops werden abgelehnt,
statt die Wiedergabe in einem Mikroloop haengen zu lassen.

## Messwerte

Im Debug-Overlay (`F3`):

| Feld | Bedeutung |
| ---- | --------- |
| `Geraet` | Rate und Blockgroesse, oder der Fehler |
| `Buffer` | Frames je Callback |
| `Underruns` | von PortAudio gemeldete Aussetzer |
| `Callback` | gleitender Mittelwert der Callback-Dauer |
| `Peak cb` | groesster gemessener Wert |
| `Last` | Anteil der verfuegbaren Zeit |
| `Voices` | aktive Stimmen |
| `Speicher` | geladene Tracks |
| `ANALYSE` | analysiert / aus Cache / Trefferrate / Warteschlange / Fehler |
| `FPS` | Bildrate der Oberflaeche |

Gemessen mit einem Deck bei 512 Frames und 44100 Hz: 0.086 ms mittlere
Callback-Dauer bei 11.6 ms Budget, also unter 1 % Last, 0 Underruns.
Mit vier Decks bleibt die Last unter 50 % - das prueft ein Test.

## Ohne Ausgabegeraet

Laesst sich kein Stream oeffnen, bleibt der `NullPlaybackPort` aktiv: die
Decks laufen ueber die Wanduhr weiter, damit die Oberflaeche bedienbar ist.
`DeckState.audio_status` meldet dann `NO_BACKEND`. Es wird kein Ton
vorgetaeuscht.

## Formatabdeckung

WAV, FLAC, AIFF, MP3, OGG ueber libsndfile. AAC/M4A fehlt; ein Backend dafuer
wird ueber `register_backend` ergaenzt, ohne dass sich an Analyse, Engine oder
Oberflaeche etwas aendert. Details in [DEPENDENCIES.md](DEPENDENCIES.md).

## PRO DJ LINK

Nicht implementiert und bewusst getrennt gehalten. Die Oberflaeche kennt nur
`DeckStateProvider`; neben `LocalDeckStateProvider` kann spaeter ein
`ProDjLinkStateProvider` treten, ohne dass die Audio-Engine davon beruehrt
wird.
