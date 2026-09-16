# Quantize, Slip und Jog Mode

Drei Schalter, die kein eigenes Geraet im Geraet sind: sie aendern, wie die
bereits vorhandene Player-, Jog-, Cue-, Hotcue-, Loop- und Audiologik
wirkt. Es gibt dafuer **keine** zweite Audio-, Jog- oder Loop-Engine.

```
DECK STATE                  deck/state.py
|
+-- quantize                Schalter
+-- quantize_beats          Rasterweite: 1/8, 1/4, 1/2, 1 Beat
+-- slip                    Schalter
+-- slip_state              laufende Slip-Aktion + Hintergrundposition
+-- jog_mode                VINYL | CDJ
+-- jog_touch               Beruehrung, modusunabhaengig gemeldet

QUANTIZE ENGINE             deck/quantize.py     quantize_position()
SLIP ENGINE                 deck/slip.py         SlipEngine, SlipState
JOG-AUSWERTUNG              deck/engine.py       Deck._cmd_jog_move()
```

Alle drei sind zentraler Deck-Zustand. Bildschirm, LEDs, Audioausgabe und
spaeter die echte Hardware lesen dieselben Felder; gesetzt werden sie
ausschliesslich ueber Kommandos:

```
TOGGLE_QUANTIZE   CommandType.QUANTIZE_TOGGLE
                  CommandType.QUANTIZE_BEATS    (Rasterweite)
TOGGLE_SLIP       CommandType.SLIP_TOGGLE
TOGGLE_JOG_MODE   CommandType.JOG_MODE_TOGGLE
```

Die Oberflaeche seekt nicht, scratcht nicht, verschiebt keine Loop-Grenzen
und rechnet keine Slip-Position. Sie schickt ein Kommando - denselben Weg,
den ein Taster am Bedienfeld nimmt.

---

## QUANTIZE

### Was der Schalter tut

```
Quantize = 1 -> Punkt = quantize_position(Position, Beatgrid, quantize_beats)
Quantize = 0 -> Punkt = Position
```

Bei ausgeschaltetem QUANTIZE liegt der Punkt **exakt** auf der aktuellen
Wiedergabeposition. Es gibt keine versteckte Beatgrid-Korrektur.

### Was er nicht tut

Quantize rundet nicht alles. Die **Wiedergabeposition** wird nie auf das
Raster gezogen. Betroffen sind ausschliesslich die musikalischen Aktionen:

| quantisiert | nicht quantisiert |
| --- | --- |
| Cue setzen | Wiedergabeposition |
| Hotcue setzen | Pitch Bend |
| LOOP IN / LOOP OUT | Scratch |
| Beatloop-Start (Pads, CALL, Touch-Panel) | Frame Search |
| | Loop-Adjust am Jogwheel |
| | Beatloop-**Laenge** |

Quantize und Jog Mode sind voneinander unabhaengig: QUANTIZE beeinflusst
weder Pitch Bend noch Scratch noch die Jogposition.

### Eine Stelle, die rastet

`deck/quantize.py`, `quantize_position(position_s, grid, beats)`. Cue und
Hotcue gehen ueber `Deck._quantized()`, die Loop-Punkte ueber
`LoopEngine.position_for()` - beide rufen dieselbe Funktion mit derselben
Weite auf. Vorher hatten Cue/Hotcue und die Loop-Engine getrennte
Rundungen, beide fest auf ganze Beats; eine eingestellte Weite von 1/8
wirkte auf Cue und Hotcue gar nicht.

Genau auf halber Strecke gewinnt der spaetere Rasterpunkt. Ohne gueltiges
Beatgrid bleibt die Position unveraendert - ein erfundenes Raster waere
schlechter als gar keines.

### Rasterweite

`DeckState.quantize_beats`, Werte `QUANTIZE_BEAT_VALUES = (1/8, 1/4, 1/2,
1)` wie am CDJ-3000. Erreichbar ueber

* `QUANTIZE_BEATS` mit `beats=<Wert>` - setzt genau diesen Wert,
* `QUANTIZE_BEATS` ohne `beats` - schaltet weiter (1/8 -> 1/4 -> 1/2 -> 1),
* **SHIFT + QUANTIZE** am Bedienfeld,
* die Zeile *Quantize Beat Value* in den Einstellungen.

Am Geraet sitzt dieser Wert in UTILITY/SHORTCUT. Diese Seiten gibt es hier
noch nicht; der Wert ist aber bereits zentral gefuehrt, sodass eine spaetere
UTILITY-Seite nur noch dasselbe Kommando schicken muss.

### Beatloops behalten ihre Laenge

Ein 4-Beat-Loop ist immer genau vier Beats lang. Quantisiert wird der
**Anfang**; das Ende wird daraus abgeleitet:

```
loop_in  = quantize_position(Position, Grid, quantize_beats)
loop_out = loop_in + Laenge(gewaehlte Beats)
```

Wuerden beide Punkte einzeln gerastet, waere derselbe Loop je nach
Startposition mal 3.5, mal 4.5 Beats lang.

### Ein laufender Loop bleibt unberuehrt

QUANTIZE umzuschalten - oder die Rasterweite zu aendern - beendet keinen
Loop und verschiebt weder `in_s` noch `out_s`. Der Schalter wirkt erst auf
die naechste neue Aktion.

---

## SLIP

### Grundprinzip

Bei eingeschaltetem SLIP veraendern bestimmte **voruebergehende** Aktionen
hoerbar die Wiedergabe, waehrend die normale Trackposition unsichtbar
weiterlaeuft. Endet die Aktion, springt die Wiedergabe dorthin, wo der
Track ohne den Eingriff inzwischen waere.

```
Track bei 30 s, SLIP an, 3 s scratchen

hoerbar       30.0 -> irgendwo (Scratch)
Hintergrund   30.0 -> 31.0 -> 32.0 -> 33.0
loslassen                                   -> Wiedergabe bei 33.0
```

### Zwei Positionen, ein Player

| Feld | Bedeutung |
| ---- | --------- |
| `DeckState.position_s` | die hoerbare Position |
| `DeckState.slip_position_s` | die Hintergrundposition |

Es gibt **keinen zweiten Player**. Die Hintergrundposition ist eine einzelne
Zahl, die im Takt weitergezaehlt wird (`Deck._advance_slip`) - die logische
Position, an der die normale Wiedergabe ohne den Eingriff waere. Sie laeuft
immer vorwaerts und mit dem eingestellten Tempo, auch wenn hoerbar
rueckwaerts gespielt wird.

### Drei Zustaende, die auseinandergehalten werden

| Feld | Bedeutung |
| ---- | --------- |
| `DeckState.slip` | der SLIP-Schalter (`slipEnabled`) |
| `DeckState.slip_active` | gerade laeuft eine Slip-Aktion (`slipOperationActive`) |
| `DeckState.slip_position_s` | die Zeitachse dieser Aktion |

Der Schalter kann an sein, ohne dass etwas laeuft. Die LED zeigt den
**Schalter**; die Entwicklungsanzeige und die Uebersichts-Wellenform zeigen
zusaetzlich die laufende Aktion.

### Welche Aktionen slippen

`SlipReason` in `deck/slip.py` - es gibt keinen anonymen Weg in oder aus
einer Slip-Aktion:

| Grund | beginnt bei | endet bei |
| --- | --- | --- |
| `PAUSE` | PLAY/PAUSE waehrend der Wiedergabe | erneutes PLAY |
| `SCRATCH` | Platte beruehrt, Vinyl-Modus | Loslassen, Wechsel nach CDJ |
| `LOOP` | ein Loop wird aktiv (Pad, CALL, LOOP IN/OUT, Hotcue-Loop) | der Loop endet |
| `HOT_CUE` | Hotcue-Pad gedrueckt | Pad losgelassen |
| `REVERSE` | Richtungsschalter auf REV bzw. SLIP REV | zurueck auf FWD |

Mehrere Gruende koennen sich ueberlagern - etwa Scratch waehrend eines
laufenden Loops. `SlipState.reasons` ist deshalb eine Menge, und die
Wiedergabe springt erst zurueck, wenn der **letzte** Grund endet. Sonst
risse das Loslassen des Jogwheels den noch laufenden Loop auseinander.

Die Stellung **SLIP REV** des Richtungsschalters ist am Geraet die
Slip-Stellung des Hebels; sie wirkt deshalb auch ohne den SLIP-Taster.

### Was Slip nicht betrifft

Slip nimmt nicht jeden Sprung zurueck. Ausdrueckliche Bedienschritte
verwerfen die Zeitachse, **ohne** zu springen:

* SEEK (Nadelsetzen auf der Uebersicht)
* CUE
* TRACK SEARCH
* neuer Track, Auswurf (Abschnitt 39)
* SLIP ausschalten

Erhalten bleiben dabei die Einstellungen `slip`, `quantize`,
`quantize_beats` und `jog_mode` - zurueckgesetzt wird nur der temporaere
Zustand.

### Slip und Loop

Der Loop selbst laeuft waehrend Slip unveraendert: `in_s`, `out_s` und
`active` bleiben, wie sie sind, und der Loop endet weiterhin nur aus den
vier Gruenden in [LOOP.md](LOOP.md). Slip haengt sich in `Deck._set_loop`
ein - die einzige Stelle, die `DeckState.loop` schreibt. Damit kann kein
Loop an Slip vorbeigehen, egal woher er kam.

Ein normaler Hotcue verlaesst weiterhin einen laufenden Loop
(`JUMPED_OUT`). Die temporaere Slip-Wiedergabe liegt daneben und haelt ihre
eigene Zeitachse.

---

## JOG MODE

`DeckState.jog_mode` ist `VINYL` oder `CDJ`. Der Taster schaltet um; die
LED zeigt VINYL.

### Die Eingabeseite kennt den Modus nicht

Virtuelles und spaeter echtes Jogwheel melden immer dasselbe: `jog_touch`
und die Schrittdifferenz. Erst `Deck._cmd_jog_move()` entscheidet anhand von
`jog_mode`, was daraus wird. Es gibt keine Vinyl-Logik im virtuellen
Jogwheel und keine zweite in der Hardware.

|  | spielt | pausiert |
| --- | --- | --- |
| **VINYL**, Platte beruehrt | Vinyl-Stop bzw. Scratch | Frame Search |
| **VINYL**, Rand | Pitch Bend | Pitch Bend |
| **CDJ** (Rand und Platte) | Pitch Bend | Frame Search |

Im CDJ-Modus wird die Beruehrung bewusst nicht ausgewertet: dort gibt es
weder Scratch noch Vinyl-Stop. Ein Druck auf die Platte haelt die Wiedergabe
nicht an.

Massstaebe: eine Umdrehung ist ein Beat bei direktem Zugriff (Scratch,
Frame Search) und `JOG_BEND_SECONDS_PER_REV` beim Pitch Bend. Ohne gueltiges
Beatgrid gilt beim Scratchen die Plattenlaenge `JOG_SECONDS_PER_REV`.

### Vinyl-Stop

Die Platte im Vinyl-Modus anzufassen haelt die Wiedergabe an
(`Deck._vinyl_hold()`): `_transport_speed()` wird 0, und `_sync_playback()`
sagt der Ausgabe, dass sie stehen soll. Der Transportzustand bleibt
`PLAYING` - so wie am Geraet, wo die PLAY-Anzeige beim Anfassen nicht
ausgeht. Loslassen nimmt die Wiedergabe wieder auf.

Das ist die Vinyl-Bremse in ihrer einfachsten ehrlichen Form: **ohne**
Auslauf- und Anlauframpe. Die Rampe haengt am `VINYL SPEED ADJUST`
(`DeckState.vinyl_speed_adjust` wird gefuehrt) und fehlt weiterhin - siehe
"Was noch fehlt" in [JOG.md](JOG.md). Der Halt selbst fehlte vorher ganz;
ohne ihn liefen Scratch und Wiedergabe gleichzeitig.

### Moduswechsel

Ein Wechsel VINYL <-> CDJ ist ein reiner Moduswechsel. Er haelt die
Wiedergabe nicht an, aendert weder Tempo noch BPM, und `loop.active` bleibt
unangetastet. Das Einzige, was er zusaetzlich tut: eine laufende
Scratch-Slip-Aktion endet beim Wechsel nach CDJ, weil es dort kein
Scratchen gibt.

---

## Entwicklungsanzeige (F3)

```
Quantize        ON  1/4
Slip            ON
Slip Operation  ACTIVE LOOP+SCRATCH
Slip BG Pos       33.128 s
Jog             VINYL TOUCH
Position          30.417 s
Loop            ON  4
```

Alle Werte kommen aus dem `DeckState`; die Anzeige rechnet nichts.

Waehrend einer Slip-Aktion zeigt die Uebersichts-Wellenform die
Hintergrundposition als gestrichelten Strich neben dem normalen
Positionsstrich - im vorhandenen Renderer, ohne zweiten Zeichenweg. Ohne
diese zweite Marke sieht der Ruecksprung beim Loslassen wie ein Fehler aus.

---

## Tests

`tests/test_player_modes.py` - die Testfaelle der Abschnitte 41-50:

* **Q1-Q6** Quantize aus/an, Loop In/Out, Beatloop-Laenge, Toggle waehrend
  eines laufenden Loops, alle vier Rasterweiten, SHIFT + QUANTIZE.
* **V1-V5** Rand, Vinyl-Stop, Scratch, Loslassen, Frame Search.
* **C1-C4** Pitch Bend, kein Scratch, kein Stop beim Beruehren, Frame
  Search.
* **Slip** Scratch ueber 3 s, Loop ueber mehrere Durchlaeufe, Pause ueber
  5 s, Reverse ueber 3 s, Hotcue, Beatloop-Pads, SLIP REV, Grenzfaelle
  (SEEK, CUE, TRACK SEARCH, Trackwechsel).
* **Kombinationen** alle fuenf geforderten Schalterstellungen.
* **Loop** bleibt bei jedem reinen Moduswechsel aktiv, auch ueber 100
  Durchlaeufe mit eingeschaltetem Slip.

Dazu die Rechnungen einzeln, ohne Deck: `quantize_position()` und
`SlipEngine`. Die Loop-Stabilitaet insgesamt steht weiterhin in
`tests/test_loop_stability.py`.
