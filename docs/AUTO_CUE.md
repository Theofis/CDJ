# AUTO CUE und die Taste TIME MODE / AUTO CUE

## Was AUTO CUE tut

Ist AUTO CUE aktiv, bleibt ein neu geladener Track nicht bei 0:00 stehen,
sondern an der Stelle, an der es losgeht. Der Cue-Punkt liegt dort, und
das Deck wartet dort (Handbuch S. 44).

**Welche Stelle das ist, entscheidet AUTO CUE LEVEL** (siehe unten):

* `MEMORY` - der fruehste gespeicherte **Memory Cue**. Werkseinstellung,
  hier wie am Geraet.
* eine Pegelstufe - der erste **Audioeinsatz**, gesucht im Signal.

Ein Hot Cue ist in **keinem** Fall die Antwort. Er ist von Hand gesetzt
worden und sagt nichts darueber, wo der Ton anfaengt; auch die Stufe
`MEMORY` nimmt nur Memory Cues. Ein Test haelt das fest.

## Wie der Einsatz gefunden wird

`virtual_cdj/deck/auto_cue.py`, Funktion `detect_audio_start()`.

Es entsteht **keine zweite Analysepipeline**. Gelesen werden die
Waveform-Daten, die die Trackanalyse ohnehin einmal berechnet und cacht
(`audio/analysis/waveform.py`). Die Audiodatei wird dabei nicht geoeffnet,
nichts dekodiert und nichts gefiltert.

Ablauf:

1. feinste vorhandene Aufloesungsstufe nehmen (`WaveformSet.finest`,
   ueblich `detailed` mit 320 Fenstern je Sekunde, also ~3 ms je Fenster),
2. je Fenster die Lautstaerke bestimmen - der Breitband-Spitzenwert
   `peak`, weil der am Einsatz zuerst anspricht; aeltere Analysen ohne
   Dynamikfelder haben nur die drei Baender, dann gilt das lauteste,
3. das erste Fenster ueber der Schwelle suchen,
4. den **Anfang** dieses Fensters zurueckgeben. Damit liegt die Stelle
   immer davor, nie mitten im Einsatz.

Gibt es keine Waveform, oder bleibt der ganze Track unter der Schwelle,
kommt `None` zurueck - "nicht feststellbar". Das Deck bleibt dann beim
Trackanfang. Geraten wird nichts.

### Die dB-Werte sind hier **keine** dBFS

Am CDJ-3000 sind `-78` bis `-36 dB` absolute Pegel. Hier sind sie das
noch nicht.

Die Analyse normiert `peak` und `rms` auf den **lautesten Punkt des
Tracks** (`full_scale` in `audio/analysis/waveform.py`), nicht auf
digitale Vollaussteuerung. Eine Schwelle von -60 dB heisst hier also
"60 dB unter dem lautesten Punkt dieses Tracks".

Darum heissen die Dinge, wie sie heissen: `AutoCueLevel.threshold_db_below_peak`
und `detect_audio_start(threshold_db_below_peak=...)` - nicht
`threshold_dbfs`. Die Beschriftungen in der Aufzaehlung sind die des
Geraets, die Wirkung ist es nicht.

Bei fertig gemasterten Tracks ist der Unterschied klein, weil die nahe
Vollaussteuerung liegen. Bei einer leisen Aufnahme ist er es nicht.

#### Was fuer absolute Schwellen fehlt

Genau eine Zahl: der **unnormierte** Spitzenwert des Tracks.

1. `audio/analysis/waveform.py`, `compute_levels()` - dort wird
   `full_scale = max(abs(mono))` berechnet und danach weggeworfen. Es
   muesste in `BandPeaks` mitgefuehrt werden. Ein `TODO` steht an der
   Stelle.
2. `audio/cache.py` - der Wert gehoert in die Cache-Metadaten, sonst ist
   er nach dem ersten Lauf wieder weg. Das aendert das Cache-Format und
   braucht eine Formatversion.
3. `deck/state.py`, `WaveformData` - Feld `full_scale` durchreichen.
4. `deck/auto_cue.py` - `detect_audio_start()` bekommt daneben ein
   `threshold_dbfs` und rechnet
   `threshold = db_to_amplitude(dbfs) / full_scale`.

Solange das nicht da ist, gilt die relative Bedeutung, und sie steht in
jedem Namen.

## AUTO CUE LEVEL

`DeckState.auto_cue_level`, Werte in `AutoCueLevel` (`deck/state.py`):

| Stufe | Bedeutung |
| ----- | --------- |
| `MEMORY` | statt der Signalsuche den fruehesten **Memory Cue** nehmen |
| `-78dB` bis `-36dB` | Schwelle der Signalsuche, in 6-dB-Schritten |

Voreinstellung ist **`MEMORY`** - so ist es am Geraet die Werkseinstellung
(Handbuch S. 78). Ein frisch gestartetes Deck springt also beim Laden an
den fruehesten Memory Cue und sucht **nicht** im Signal. Die Signalsuche
gilt erst, wenn eine Pegelstufe gewaehlt ist.

Die Reihe steht vollstaendig in `AUTO_CUE_LEVELS` - sie ist die Reihe des
Geraets, nicht eine Auswahl davon.

**Noch nicht umschaltbar.** Es gibt bis jetzt kein Bedienelement, das die
Stufe wechselt; am CDJ-3000 sitzt sie in UTILITY, und diese Seite ist
nicht gebaut. Der Wert wird aber schon ausgewertet, und die Stufe
`MEMORY` funktioniert. Was fehlt, ist allein die Tuer dorthin.

## Die Taste

`ids.TIME_MODE`, in `controls.py` als `TIME MODE / AUTO CUE`. Ein Taster
mit zwei Bedeutungen, wie am Geraet:

| Druck | Kommando | Wirkung |
| ----- | -------- | ------- |
| kurz | `TIME_MODE` | Zeitanzeige ELAPSED ↔ REMAIN (Anzeige) |
| lang (ab `LONG_PRESS_S`) | `AUTO_CUE` | AUTO CUE ein/aus (Deckzustand) |

Unterschieden wird in `deck/mapping.py` (`InputMapper._time_mode`), und
zwar **beim Loslassen**, aus der Differenz der Ereigniszeitstempel
(`InputEvent.timestamp`, in Millisekunden). Daraus folgt zweierlei:

* Es entsteht genau **ein** Kommando je Druck. Ein langer Druck kann
  deshalb nicht zusaetzlich die Zeitanzeige umschalten.
* Es laeuft kein Timer und kein Nebenlaeufer. Ein Test setzt die
  Zeitstempel ein, statt zu warten.

`LONG_PRESS_S` steht in `core/model.py` - **eine** Zahl fuer alle, die
lange Druecke auswerten (Bildschirm fuer BACK und Drehregler,
Zuordnungsschicht fuer diese Taste).

Der Weg der beiden Kommandos ist verschieden, und das ist Absicht:
`TIME_MODE` steht in `DISPLAY_COMMANDS`, der Bildschirm behandelt es
selbst. `AUTO_CUE` steht dort nicht und erreicht deshalb die Deck-Engine.

## Herkunft der Taste

Die uebrigen Bedienelemente stammen aus dem Referenzbild des Bedienfelds.
Dieser Taster war dort nicht zu erkennen, weshalb frueher ausdruecklich
festgelegt war, dass es ihn nicht gibt (ein Test hat das abgesichert).
Der Erbauer hat bestaetigt, dass das Geraet ihn besitzt. Er ist damit das
erste Bedienelement, das nicht aus dem Bild kommt - vermerkt in
`docs/IMAGE_ANALYSIS.md`.

## Keine LED

Dieser Taster hat in `controls.py` **keine** LED, und die Zustaende
`auto_cue` und `time_mode` erzeugen keine Leuchtanzeige. Am Geraet zeigt
das Display "A. CUE"; diese Anzeige ist hier noch nicht gebaut
(`docs/CDJ3000_DISPLAY_ANALYSIS.md`, Abschnitt 11).
