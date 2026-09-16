# Loop-Logik

Die gesamte Loop-Logik steht in `virtual_cdj/deck/loop.py`. Sie ist eine
reine Rechnung:

```
(Loop-Zustand, Beatgrid, Position) -> neuer Loop-Zustand
```

Kein Transport, kein Audio, keine Eingabe. `deck/engine.py` versorgt sie mit
dem Beatgrid des geladenen Tracks und haengt das Ergebnis in den `DeckState`;
die sample-genaue Loop-Grenze im Ton macht `audio/engine.py`.

```
LOOP ENGINE            deck/loop.py
|
+-- Loop State         deck/state.py    LoopState, LoopAdjust
+-- Quantization       nearest_beatgrid(), seconds_for_beats(), beats_for()
+-- Manual Loop        loop_in_pressed(), loop_out_pressed()
+-- Loop Size          call_left_pressed(), call_right_pressed(), scaled()
+-- Beatloop           beat_loop_pad_pressed(), beat_loop()
+-- Jog Adjust         adjust_with_jog()
+-- Exit / Reloop      exit_reloop_pressed(), exit_loop(), reloop()
+-- Playback           check_boundary()
```

## Zustand

Alles steht in `DeckState.loop` (`LoopState`, unveraenderlich):

| Feld | Bedeutung |
| ---- | --------- |
| `active` | Der Loop laeuft. |
| `in_s` / `out_s` | Loop-Punkte in Sekunden. |
| `beats` | Laenge in Beats, falls sie sauber im Raster liegt, sonst `None`. |
| `adjust` | `NONE` / `IN` / `OUT` - welcher Punkt am Jogwheel haengt. |
| `last_in_s` / `last_out_s` / `last_beats` | zuletzt gespeicherter Loop (RELOOP). |
| `pad_index` | abgeleitet: Beatloop-Pad, dessen Laenge dem Loop entspricht. |

Dazu kommt aus dem Deck-Zustand:

| Feld | Bedeutung |
| ---- | --------- |
| `DeckState.quantize` | 0 / 1 - ob neue Loop-Punkte auf das Beatgrid rasten. |

Vier Zustaende sind bewusst getrennt und folgen **nicht** auseinander:

* `loop.active` - der Loop laeuft.
* `loop.adjust` - ein Loop-Punkt wird gerade verschoben.
* `DeckState.pad_mode is PadMode.BEAT_LOOP` - die Pads A-H loesen Beatloops
  aus. Das heisst nicht, dass ein Loop laeuft.
* `DeckState.quantize` - neue Loop-Punkte rasten auf das Beatgrid.

`Last_BeatLoop_Pad` der Bedienlogik wird nicht getrennt gefuehrt, sondern in
`LoopState.pad_index` aus der Loop-Laenge abgelesen. Damit stimmt es auch,
wenn die Laenge ueber CALL, das Touch-Panel oder LOOP IN/OUT entstanden ist.

## Quantisierung

Wo ein neuer Loop-Punkt entsteht, entscheidet der QUANTIZE-Schalter
(`DeckState.quantize`, gesetzt ueber `QUANTIZE_TOGGLE`):

```
Quantize = 1 -> Position = quantize_position(Position, Grid, quantize_beats)
Quantize = 0 -> Position = Current_Position
```

Das gilt an genau vier Stellen, alle ueber `LoopEngine.position_for()`:
manuelles LOOP IN, manuelles LOOP OUT, CALL < / CALL > und die
Beatloop-Pads A-H (samt Touch-Panel). Beim Quantisieren gewinnt genau auf
halber Strecke der spaetere Beat (12.5 -> 13).

**Gerechnet wird nicht in der Loop-Engine**, sondern in
`deck/quantize.py` - derselben Stelle, durch die auch Cue und Hotcue gehen.
Die Rasterweite steht in `DeckState.quantize_beats` (1/8, 1/4, 1/2, 1 Beat)
und wirkt damit auf alle vier Stellen gleich. Einzelheiten in
[PLAYER_MODES.md](PLAYER_MODES.md).

**Nicht** quantisiert wird das Verschieben mit dem Jogwheel - das ist der
Sinn der Feineinstellung - und die Laenge eines Beatloops: ein 4-Beat-Loop
ist auch ab einer ungerasteten Position genau 4 Beats lang.

Derselbe Schalter gilt unveraendert fuer Cue-Punkte und Hotcues.

Ohne gueltiges Beatgrid kann nicht quantisiert werden und es entsteht kein
Beatloop; das Deck vermerkt den Fall in `Deck.unsupported`, statt ein Tempo
zu erfinden.

Bruchteile von Beats rechnet die Loop-Engine selbst
(`LoopEngine.seconds_for_beats`): `BeatGrid.seconds_for_beats` rundet auf
ganze Beats und wuerde bei 1/4 Beat 0 liefern. Ganze Beats folgen den echten
Beatzeiten und halten damit auch bei wechselndem Tempo.

## Bedienung

| Taste | kein Loop aktiv | Loop aktiv |
| ----- | --------------- | ---------- |
| LOOP IN | Anfang auf den naechsten Beat | IN-Adjust ein/aus |
| LOOP OUT | Ende setzen, Loop startet | OUT-Adjust ein/aus |
| CALL < | 4-Beat-Loop | Laenge halbieren |
| CALL > | 8-Beat-Loop | Laenge verdoppeln |
| RELOOP / EXIT | letzten Loop aufrufen | Loop verlassen |
| Pad A-H (Beatloop-Modus) | Loop dieser Laenge | andere Laenge, gleiches Pad verlaesst |

Pad-Laengen: A = 1/4, B = 1/2, C = 1, D = 2, E = 4, F = 8, G = 16, H = 32
Beats (`BEAT_LOOP_LENGTHS` in `deck/state.py`).

### Pad-Modus

Die Pads A-H haben keine eigene Logik. Sie senden nur `PAD` mit ihrem
Index; was daraus wird, entscheidet `DeckState.pad_mode`:

```
                PAD A-H
                   |
              Deck._cmd_pad
                   |
                pad_mode
             /            \
      HOT_CUE              BEAT_LOOP
         |                     |
   Hotcues am Track        LoopEngine
```

Umgeschaltet wird mit den beiden Modustastern des Bedienfelds
(`HOT_CUE` und `BEAT_JUMP`, letzterer waehlt den Beatloop-Modus). Sie
senden ausschliesslich `PAD_MODE` - keine Pad-Funktion. Es gibt genau
**einen** Modus, nicht zwei Schalter: `pad_mode` hat einen Wert, damit
schliessen sich die Modi gegenseitig aus, ohne dass es dafuer Code
braucht.

Derselbe Wert treibt die Anzeige: `CdjApplication._sync_pad_mode_leds()`
spiegelt `pad_mode` auf die LEDs der beiden Taster. Das virtuelle
Bedienfeld zeichnet sie von dort, und derselbe `set_led`-Aufruf schreibt
spaeter ueber die Hardware-Zuordnung auf einen MCP23017-Ausgang. Es gibt
keinen getrennten Zustand fuer Bildschirm und Hardware.

Die runden Taster **4 BEAT LOOP** und **8 BEAT LOOP** sind davon
unabhaengig und wirken in jedem Pad-Modus.

Dieselbe Adjust-Taste erneut beendet das Anpassen; die andere wechselt den
Punkt. Beim Beenden wird der Loop gespeichert.

### "Gleiches Pad" heisst: dieses Pad hat den Loop gesetzt

`LoopState.pad` merkt sich das Beatloop-Pad, mit dem der laufende Loop
entstanden ist (`Last_BeatLoop_Pad`). Nur ein Druck auf **dieses** Pad
verlaesst den Loop.

Frueher wurde das Pad aus der Loop-**Laenge** abgeleitet. Ein mit CALL <
erzeugter 4-Beat-Loop ist aber genauso lang wie Pad E - ein Druck auf Pad E
verliess ihn dann, statt ihn zu setzen. Der Loop war auf einen Tastendruck
weg, der ihn haette anlegen sollen. Dasselbe galt fuer jeden von Hand
gezogenen Loop, dessen Laenge zufaellig auf eine Pad-Laenge fiel.

`pad` wird geloescht, sobald der Loop nicht mehr der des Pads ist: bei
LOOP IN/OUT, CALL (die Laenge passt danach nicht mehr), Loop-Hotcue,
RELOOP und beim Verlassen.

### Ein Sprung aus dem Loop heraus verlaesst ihn

SEEK (Nadelsetzen auf der Uebersicht), CUE und Hotcues sind ausdrueckliche
Spruenge. Zielen sie ausserhalb des laufenden Loops, wird er verlassen.

Ohne das war ein aktiver Loop eine **Sackgasse**: die Loop-Grenze zog die
Position im naechsten Bild zurueck, und jeder Sprung sah aus, als reagiere
das Geraet nicht mehr. Gemessen: Sprung auf 60 s bei Loop 10-12 s, nach
80 ms wieder bei 10 s.

Jog, Scratch und Pitch Bend gehen weiter durch die Loop-Grenze und bleiben
damit im Loop - beim Scratchen ist genau das gewollt.

CALL < und CALL > blaettern am Pioneer-Geraet durch gespeicherte Cue- und
Loop-Punkte. Hier sind sie die Loop-Groessentasten - es gibt keinen
Cue-Speicher, und die Groesse ist beim Spielen die haeufigere Aufgabe.

## Grenzen der Laenge

`MIN_LOOP_BEATS = 1/64`, `MAX_LOOP_BEATS = 64`.

* **Halbieren/Verdoppeln** (CALL, Panel): ein Schritt ueber die Grenze
  hinaus wird verworfen - halbe Schritte gibt es nicht, und der Loop bleibt,
  wie er ist.
* **Jog-Adjust**: wird **begrenzt, nicht verworfen**. Der Punkt wandert bis
  zur Grenze und bleibt dort stehen.
* **LOOP IN/OUT** kennen die Grenzen nicht - ein von Hand gezogener Loop
  darf laenger als 64 Beats sein. Beim Verschieben wird ein solcher Loop
  nicht zusammengestaucht; die Grenze verhindert nur, dass er **weiter**
  waechst (`LoopEngine._band`).

Warum das Begrenzen statt des Verwerfens: im Adjust-Modus geht die
Jog-Bewegung an den Loop-Punkt und **nicht** mehr an die Wiedergabe. Wurde
der Schritt an der Grenze komplett verworfen, bewegte sich gar nichts mehr -
das Rad drehte, und das Geraet wirkte aufgehaengt.

Die Audio-Engine weist zusaetzlich Loops ab, die kuerzer als
`MIN_LOOP_FRAMES` sind, damit kein Mikroloop entsteht.

## Jogwheel im Adjust-Modus

Eine volle Umdrehung entspricht einem Beat:

```
+360 Grad = +1 Beat      +90 Grad = +0.25 Beat
+180 Grad = +0.5 Beat    -360 Grad = -1 Beat
```

Dabei wird **nicht** quantisiert - das ist der Sinn der Feineinstellung. Die
Wiedergabeposition bleibt stehen; ohne Adjust-Modus bewegt dasselbe Jogwheel
wie bisher den Track (Scratch bzw. Pitch Bend).

Weil der Adjust-Modus das Jogwheel von der Wiedergabe abzieht, muss er
**sichtbar** sein: die laufende Wellenform zeigt neben der Loop-Laenge ein
Feld `IN ADJ` bzw. `OUT ADJ`. Ohne diese Anzeige sieht ein Geraet in diesem
Zustand aus, als haette es sich aufgehaengt.

Die Bewegung wird nur dann an den Loop-Punkt geleitet, wenn es wirklich
einen aktiven, gesetzten Loop gibt (`deck/engine.py`). Sonst faellt sie auf
Scratch bzw. Pitch Bend durch - ein Adjust-Modus ohne Loop darf das
Jogwheel nicht stillegen.

Ergibt die neue Laenge kein sauberes Vielfaches von 1/64 Beat, wird
`beats` auf `None` gesetzt: die Oberflaeche zeigt dann keine Beat-Laenge an,
statt eine falsche zu zeigen.

## Ein Loop endet nur auf Ansage

Ein laufender Loop bleibt aktiv, bis ihn jemand ausdruecklich beendet.
`LoopEngine.exit_loop()` **verlangt** einen Grund (`LoopExitReason`) - es
gibt keinen anonymen Weg aus einem Loop:

| Grund | Ausgeloest durch |
| --- | --- |
| `RELOOP_EXIT` | RELOOP/EXIT - die vorgesehene Benutzeraktion |
| `BEAT_LOOP_PAD` | dasselbe Beatloop-Pad erneut, das den Loop gesetzt hat |
| `JUMPED_OUT` | ausdruecklicher Sprung nach draussen: SEEK, CUE, Hotcue |
| `TRACK_CHANGED` | neuer Track oder Auswurf |

Slip beendet einen Loop **nicht**. Es haengt sich in `Deck._set_loop` ein
und fuehrt nur seine eigene Hintergrund-Zeitachse mit; endet der Loop aus
einem der vier Gruende, springt die Wiedergabe dorthin
([PLAYER_MODES.md](PLAYER_MODES.md)). Dasselbe gilt fuer QUANTIZE und den
Jog Mode: reine Moduswechsel tasten `loop.active` nicht an.

**Nichts anderes** beendet einen Loop. Nicht das Erreichen von `out_s`,
nicht das Loslassen einer Taste, nicht Jog, Scratch, Search, Quantize,
Tempo, ein Beatgrid-Update, ein Bildaufbau oder ein Ansichtswechsel. Das
ist mit Tests festgehalten (`tests/test_loop_stability.py`), unter anderem
mit 16 Bedienschritten, die den Loop nachweislich **nicht** antasten.

Der Grund bleibt in `LoopState.exit_reason` stehen, bis ein neuer Loop
entsteht. Die Entwicklungsanzeige (F3) zeigt ihn, und `deck/engine.py`
protokolliert jeden Wechsel:

```
LOOP CREATED deck=1 in=20.0000 out=21.5484 beats=4.0
LOOP RESIZED deck=1 in=20.0000 out=23.0968 beats=8.0 (vorher 20.0000-21.5484, beats=4.0)
LOOP EXIT    deck=1 in=20.0000 out=21.5484 beats=4.0 ACTIVE: true -> false  reason=RELOOP_EXIT
```

Protokolliert wird in `Deck._set_loop()` - der **einzigen** Stelle, die
`DeckState.loop` schreibt. Deshalb kann kein Zustandswechsel daran
vorbeigehen.

### Die Ausgabe entscheidet nicht mit

`audio/engine.py` fuehrt den Loop aus, besitzt ihn aber nicht. Frueher
schaltete die Ausgabe einen Loop unterhalb von `MIN_LOOP_FRAMES` still ab:
`DeckState.loop.active` blieb `True`, die Anzeige zeigte weiter einen
Loop, und die Wiedergabe lief einfach hindurch - **das** war der Loop, der
scheinbar von selbst aufhoerte. Heute wird ein zu kurzer Loop auf die
kuerzeste umlauffaehige Laenge gestreckt statt verworfen; die Ausgabe
setzt `_loop_active` nur noch dort auf `False`, wo ein neuer Track geladen
wird.

## Letzten Loop speichern

`last_*` wird gesetzt beim Erzeugen eines Loops, beim Aendern der Laenge, am
**Ende** eines Adjusts und beim Verlassen - nicht bei jedem Jog-Schritt.
RELOOP arbeitet ausschliesslich mit diesen Werten:

* Wiedergabe innerhalb des alten Loops -> Position bleibt stehen.
* Wiedergabe ausserhalb -> Sprung an `last_in_s`.

Ohne gespeicherten Loop passiert nichts. Ein neu geladener Track beginnt mit
leerem Loop-Zustand.

## Wiedergabe

Mit Audioausgabe faltet `audio/engine.py` die Position sample-genau am
Loop-Ende zurueck, samt Ueberhang - so bleibt das Timing ueber viele
Durchlaeufe stehen. Ohne Ausgabe macht `LoopEngine.check_boundary` dasselbe
ueber die Wanduhr. Beide Wege nutzen dieselben Loop-Punkte aus `DeckState`.

## Tests

`tests/test_loop.py` prueft die Loop-Engine allein (ein Test je Regel),
`tests/test_deck.py` die Verdrahtung ueber Kommandos und Input-Mapping,
`tests/test_vertical_path.py` und `tests/test_audio.py` den Weg bis in die
Audioausgabe.

Zusaetzlich abgesichert werden die Zustaende, in denen die Bedienung frueher
stehenblieb:

* Pad, das den Loop nicht gesetzt hat, verlaesst ihn nicht.
* Jog-Adjust an der Grenze begrenzt statt abzuweisen und laeuft danach in
  der Gegenrichtung wieder.
* Ueberlanger manueller Loop wird beim Verschieben nicht zusammengestaucht.
* Sprung aus dem Loop heraus verlaesst ihn; Sprung hinein nicht.
* Adjust-Modus ohne Loop legt das Jogwheel nicht still.

`tests/test_loop_stability.py` prueft, dass ein Loop nicht von selbst
endet: 100 Wiederholungen ohne Drift, Laengenwechsel im Betrieb,
Beatloop-Pads samt Loslassen, Pause/Play, 16 unbeteiligte Bedienschritte,
acht Tempi von 60 bis 200 BPM, Loop-Enden abseits der Blockgrenze und die
vier erlaubten Abschaltgruende.
