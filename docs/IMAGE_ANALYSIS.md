# Bildanalyse des geplanten Bedienfelds

Grundlage: das Bild des geplanten CDJ-Bedienfelds (614 x 734 Pixel, Draufsicht).
Alle Koordinaten in diesem Dokument und in `virtual_cdj/core/controls.py`
beziehen sich auf diese Pixelmasse; `x`/`y` ist jeweils die Mitte des Elements.

Das Bild enthaelt bis auf `CUE`, das Play-Symbol und den Schriftzug keine
lesbaren Beschriftungen. Die Benennung stammt daher aus der Position im
Layout, abgeglichen mit der Steuerelement-Liste des CDJ-3000
(<https://virtualdj.com/manuals/hardware/pioneer/cdj3k/controls.html>), die
als Referenz mitgeliefert wurde. Wo die Position keine eindeutige Zuordnung
erlaubt, ist das Element als **ungeklaert** eingetragen, statt eine Funktion
zu erfinden.

## Erkannte Elemente

### Oberhalb / im Displaymodul

| Position | Erkannt | Zuordnung |
| -------- | ------- | --------- |
| 6 gleiche Rechtecke, y 88, x 205-405 | 6 Taster | `SOURCE`, `BROWSE`, `TAG_LIST`, `PLAYLIST`, `SEARCH`, `MENU` |
| 183-412 / 78-236 | Displayflaeche | kein Eingang, Platzhalter |
| rund, 434 / 190 | Drehknopf | `BROWSE_ROTATE` + `BROWSE_PRESS`, bestaetigt |
| oben links am Drehknopf | Rechteck-Taster | `BACK` |
| oben rechts am Drehknopf | Rechteck-Taster | `TAG_TRACK_REMOVE` |
| unten links am Drehknopf | Rechteck-Taster | `TRACK_FILTER_EDIT` |
| unten rechts am Drehknopf | Rechteck-Taster | `SHORTCUT` |

Die Belegung der sechs Tasten oberhalb des Displays und der vier Tasten um
den Browse-Drehgeber ist vom Erbauer bestaetigt. `BACK` sitzt nicht in der
oberen Reihe, sondern links oben am Drehgeber.

Der Taster `BROWSE` oberhalb des Displays und der Drehgeber `BROWSE_ROTATE`
oben rechts sind zwei verschiedene Bedienelemente. Ebenso ist der Taster
`SEARCH` oberhalb des Displays nicht dasselbe wie die geriffelten Taster
`SEARCH_BACK` / `SEARCH_FWD` links unten.

### Linke Spalte, oben

| Position | Erkannt | Zuordnung |
| -------- | ------- | --------- |
| 118-168 / 143-172 | Schacht / Buchse | kein Eingang (USB / SD) |
| rund, 140 / 181 | Taster | `USB_STOP`, **ungeklaert** |
| 2 rote Rechtecke, y 238 | 2 Taster mit LED | `SLIP`, `QUANTIZE` |
| Quadrat, 140 / 268 | 1 Taster | `SHIFT` |

### Pad-Reihe

| Position | Erkannt | Zuordnung |
| -------- | ------- | --------- |
| 8 Rechtecke, y 268, x 178-421 | 8 Pads mit LED | `PAD_A` ... `PAD_H` |
| klein rund, 452 / 270 | Drehpotentiometer | `VINYL_SPEED_ADJUST` |

Das kleine runde Element rechts neben der Pad-Reihe ist kein Anzeigeelement,
sondern ein Drehpotentiometer (vom Erbauer bestaetigt).

### Loop-Reihe (y 313)

| Position | Erkannt | Zuordnung |
| -------- | ------- | --------- |
| 2 grosse orange Kreise, 130 / 158 | Taster mit LED | `LOOP_IN`, `LOOP_OUT` |
| kleiner orange Kreis, 188 | Taster mit LED | `RELOOP_EXIT` |
| 2 orange Rechtecke, 247 / 279 | Taster mit LED | `HOT_CUE`, `BEAT_JUMP` |
| 2 kleine Kreise, 338 / 362 | Taster | `CUE_LOOP_CALL_PREV`, `CUE_LOOP_CALL_NEXT` |
| 2 Kreise, 387 / 411 | Taster | `DELETE`, `MEMORY` |
| zweigeteiltes Rechteck, 445 | Taster mit LED | `JOG_MODE` |

### Linke Spalte, Mitte

| Position | Erkannt | Zuordnung |
| -------- | ------- | --------- |
| 2 Kreise, y 363 | Taster | `BEAT_LOOP_4`, `BEAT_LOOP_8` |
| 2 Rechtecke, y 407 | Taster | `BEAT_JUMP_PREV`, `BEAT_JUMP_NEXT` |
| Schiebeschalter, 137 / 456, 2 LEDs | 3-Stufen-Schalter | `DIRECTION` (`SLIP_REV` / `FWD` / `REV`) |
| 2x2 kleine Taster, y 505 / 532 | 4 Taster | `TRACK_SEARCH_PREV/NEXT`, `SEARCH_BACK/FWD` |

Die rechteckigen Taster direkt ueber dem Richtungsschalter sind die beiden
Beat-Jump-Richtungen. Die beiden runden Taster darueber starten einen 4- bzw.
8-Beat-Loop.

### Transport

| Position | Erkannt | Zuordnung |
| -------- | ------- | --------- |
| grosser Kreis "CUE", 140 / 592 | Taster mit LED | `CUE` |
| grosser Kreis mit Play-Symbol, 140 / 643 | Taster mit LED | `PLAY` |

### Rechte Spalte

| Position | Erkannt | Zuordnung |
| -------- | ------- | --------- |
| 2 Quadrate nebeneinander, y 390 | Taster mit LED | `BEAT_SYNC`, `MASTER` |
| breites Rechteck darunter, 449 / 417 | Taster mit LED | `KEY_SYNC` |
| 2 Kreise mit LED, 450 / 461 und 485 | Taster mit LED | `TEMPO_RANGE`, `MASTER_TEMPO` |
| senkrechter Schlitz, 450 / 512-661 | Fader | `TEMPO_FADER` |
| kleiner Kreis mit LED, 392 / 582 | Taster mit LED | `TEMPO_RESET` |

### Jogwheel

| Position | Erkannt | Zuordnung |
| -------- | ------- | --------- |
| Ring mit Griffmulden, Mitte 283 / 478, r 105 | Drehbewegung | `JOG_MOVE` (Typ `JOG`) |
| Platte, r 72 | Beruehrungsflaeche | `JOG_TOUCH` (digital) |
| Zentrum, r 32 | Anzeige | kein Eingang |

## Abgleich mit den vorgegebenen Abweichungen

| Vorgabe | Umsetzung |
| ------- | --------- |
| sechs Taster ueber dem Display | `SOURCE`, `BROWSE`, `TAG_LIST`, `PLAYLIST`, `SEARCH`, `MENU` |
| vier Taster um Browse | `BACK`, `TAG_TRACK_REMOVE`, `TRACK_FILTER_EDIT`, `SHORTCUT` |
| runde Beat-Loop-Taster | `BEAT_LOOP_4`, `BEAT_LOOP_8` |
| rechteckige Beat-Jump-Taster | `BEAT_JUMP_PREV`, `BEAT_JUMP_NEXT` |
| Pads nicht fest als Hotcues | Pads heissen `PAD_A` ... `PAD_H`, Gruppe `PERFORMANCE_PADS`, ohne Funktionszuordnung |
| `AUTO CUE` ohne eigene Taste | AUTO CUE sitzt als **langer Druck** auf `TIME_MODE` ("TIME MODE / AUTO CUE"). Es gibt keine ID, die "AUTO_CUE" heisst; per Test abgesichert |
| Bild hat Vorrang vor dem Pioneer-Layout | nicht im Bild erkennbare Referenz-Bedienelemente fehlen bewusst, siehe unten |

Bewusst **nicht** uebernommen, obwohl am Referenzgeraet vorhanden:
`VINYL/CDJ`, `JOG ADJUST`, Netzschalter.

`TIME MODE / AUTO CUE` stand hier ebenfalls, weil der Taster im
Referenzbild nicht zu erkennen war. Der Erbauer hat bestaetigt, dass
das Bedienfeld ihn besitzt; seit dem Nachtrag steht er in
`controls.py`. Er ist damit das erste Bedienelement, das **nicht** aus
dem Bild stammt - deshalb hier vermerkt.

## Bilanz

* 56 Eingaenge, keine eigenstaendige Anzeige (alle LEDs sitzen in Tastern)
* davon 1 mit Status *ungeklaert*
* Typen: 51 Taster, 1 Fader, 1 Potentiometer, 1 Encoder, 1 Schalter,
  1 Jogwheel (`JOG_TOUCH` ist als Taster mitgezaehlt)
* 55 davon aus dem Bild, 1 vom Erbauer nachgetragen
  (`TIME MODE / AUTO CUE`)
* kein Joystick im Bild erkannt; `JoystickWidget` liegt bereit, falls sich
  das Bedienelement oben rechts als Joystick herausstellt

## Zu klaeren

1. Ist der runde Taster oben links tatsaechlich USB Stop?

Geklaert:

* Tastenreihe oberhalb des Displays: `SOURCE`, `BROWSE`, `TAG_LIST`,
  `PLAYLIST`, `SEARCH`, `MENU`
* Browse-Drehgeber und seine vier Umgebungstasten: `BACK`,
  `TAG_TRACK_REMOVE`, `TRACK_FILTER_EDIT`, `SHORTCUT`
* Runde Taster bei y 363: `BEAT_LOOP_4`, `BEAT_LOOP_8`
* Rechteckige Taster bei y 407: `BEAT_JUMP_PREV`, `BEAT_JUMP_NEXT`
* Quadrat links neben der Pad-Reihe: `SHIFT`
* Kleines rundes Element rechts neben der Pad-Reihe: Drehpotentiometer
  `VINYL_SPEED_ADJUST`
* Die beiden Taster bei y 313: `HOT_CUE` und `BEAT_JUMP`
* Taster rechts neben `MEMORY`: `JOG_MODE`

Bis zur Klaerung ist `USB_STOP` im virtuellen Bedienfeld rot umrandet und voll
bedienbar.
