# Jog-Engine

Das Jogwheel hat drei Sensoren:

```
Lichtschranke_1 = 0 / 1
Lichtschranke_2 = 0 / 1
Touch_Sensor    = Analogwert
```

Die Software ist in zwei Bereiche geteilt:

```
SENSOR / SCAN PROGRAMM                    DJ PROGRAMM
virtual_cdj/jog/                          deck/engine.py
  touch.py       kapazitiver Sensor         Trackposition, Loop-Adjust
  quadrature.py  Lichtschranken A/B       virtual_cdj/jog/reader.py
  scanner.py     Touch + Positionszaehler   neuesten Stand lesen, Differenz
```

Das DJ-Programm muss nicht wissen, wie die Lichtschranken oder der
kapazitive Sensor ausgewertet werden. Es bekommt nur:

```
Touch = 0 / 1
Jog_Position_Count
```

## Aufloesung

| Groesse | Wert |
| ------- | ---- |
| Schlitze im Encoder-Ring | 200 |
| auswertbare Flanken je Schlitz | 4 |
| Positionsschritte je Umdrehung | **800** |
| eine Umdrehung | **ein Beat** |
| ein Schritt | 1/800 Beat |

```
+1 Schritt   = +1/800 Beat        +200 Schritte = +1/4 Beat
+100 Schritte = +1/8 Beat         +800 Schritte = +1 Beat
```

Der Wert steht als `STEPS_PER_REV` in `jog/quadrature.py` und als
`ticks_per_rev` des Elements `JOG_MOVE` in `core/controls.py`; ein Test
haelt beide zusammen.

## Quadraturauswertung

Vier Zustaende, Bit 1 ist Sensor A, Bit 0 ist Sensor B:

| Zustand | A | B |
| ------- | - | - |
| `0b10` | 1 | 0 |
| `0b00` | 0 | 0 |
| `0b01` | 0 | 1 |
| `0b11` | 1 | 1 |

Jeder gueltige Wechsel ist genau ein Schritt:

| Vorher | Neu | Ergebnis |
| ------ | --- | -------: |
| `10` | `00` | +1 |
| `00` | `01` | +1 |
| `01` | `11` | +1 |
| `11` | `10` | +1 |
| `10` | `11` | -1 |
| `11` | `01` | -1 |
| `01` | `00` | -1 |
| `00` | `10` | -1 |
| gleicher Zustand | | 0 |
| ungueltiger Sprung | | 0 |

Ungueltig ist der Sprung ueber die Diagonale (`00 <-> 11`, `01 <-> 10`):
dort haetten sich beide Sensoren gleichzeitig geaendert. Solche Wechsel
bewegen nichts und werden in `errors` gezaehlt - ein steigender Wert heisst
schlechte Abtastung, Prellen oder ein defekter Sensor. Die erste Abtastung
nach dem Start setzt nur den Zustand: ohne Vorgaenger gibt es keine
Richtung.

Ob `+1` im Uhrzeigersinn liegt, haengt an der Einbaurichtung. Umdrehen mit
`QuadratureDecoder(invert=True)` bzw. `run_jog.py --invert`.

## Touch-Sensor

```
Touch == 0 und Raw > Touch_ON_Threshold   -> Touch = 1
Touch == 1 und Raw < Touch_OFF_Threshold  -> Touch = 0
```

Mit zwei Schwellen springt der Zustand bei kleinen Schwankungen um den
Grenzwert nicht hin und her. Ohne `off_threshold` gilt schlicht
`Raw > Threshold`.

`TouchSensor` fuehrt Minimum, Maximum, Durchschnitt und den Verlauf mit -
genau die Werte, die das Kalibrierungsprogramm anzeigt.

## Keine Warteschlange

Die Sensorseite verarbeitet **jeden** Schritt und rechnet ihn sofort in
`Jog_Position_Count` ein. Es geht also nichts verloren. Zum DJ-Programm hin
gibt es aber keine Ereignisliste, sondern nur den aktuellen Stand:

```
Current_Jog_Position = ReadJogPosition()
Delta_Position       = Current_Jog_Position - Last_Jog_Position
Playback_Position   += Delta_Position / 800 Beat
Last_Jog_Position    = Current_Jog_Position
```

Dreht sich das Jogwheel schneller, als die Oberflaeche verarbeitet, liest
diese beim naechsten Mal 1050 statt 1000 und bewegt den Track um 50/800
Beat - in einem Schritt. Es koennen sich keine Bewegungsbefehle stauen, die
mechanisch laengst vorbei sind.

Dasselbe gilt fuer den Touch-Zustand: es zaehlt nur, ob gerade beruehrt
wird.

Umgesetzt ist das in `sources/jog_input.py`: der Bildtakt holt den Stand
ab, `JogReader` bildet die Differenz, und daraus entsteht **ein**
`JOG_MOVE`-Ereignis mit der gesamten Bewegung. Ab da laeuft alles wie
gehabt: InputLayer -> InputMapper -> DeckCommand -> Deck.

Das gilt seit dem Umbau fuer **beide** Quellen. Vorher erzeugte das
virtuelle Jogwheel je Mausbewegung ein eigenes Ereignis; gemessen an einer
schnellen Drehung ueber ein Bild waren das 120 gegen 1 in der Hardware. Die
Deck-Engine arbeitete also Dutzende Teilbewegungen einzeln ab, jede mit
ihrer eigenen Geschwindigkeit.

## Eine Schnittstelle, zwei Quellen

```
Maus / Touchscreen / Stift ->  VirtualJogInput  \
                                                 +-> JogScanner (Zaehler)
ESP32 / Lichtschranken     ->  HardwareJogInput /            |
                                                             | poll() je Bild
                                                             v
                                                       JogMovement
                                                             |
                                                       InputLayer
                                                             |
                                                 InputMapper -> Deck
```

`JogMovement` ist der Eingabezustand der Vorgabe - vier Werte, beide Male
mit derselben Bedeutung:

| Vorgabe | Feld | Bedeutung |
| --- | --- | --- |
| `touched` | `touch` | wird gerade beruehrt |
| `deltaPosition` | `steps` | Schritte seit dem letzten Lesen |
| `direction` | `direction` | `+1` / `-1` / `0`, aus **dieser** Bewegung |
| `speed` | `speed` | Schritte je Sekunde, mit Vorzeichen |

Die Deck-Engine sieht keinen Unterschied zwischen Maus und ESP32. Sie muss
beim Anschluss der echten Hardware **nicht** angepasst werden.

Zwei Feinheiten, die leicht zu uebersehen sind:

* **Beruehren ist nicht Bewegen.** Die Platte anzufassen und stillzuhalten
  ergibt `touch=True` bei `steps=0`, `direction=0`, `speed=0`.
* **Speed braucht eine Zeitspanne.** Zwei Aufnahmen wenige Mikrosekunden
  auseinander ergeben rechnerisch Millionen Schritte je Sekunde,
  physikalisch aber nichts. Unterhalb von `MIN_SPEED_INTERVAL_S` meldet
  `speed` deshalb `0.0` statt einer erfundenen Zahl. Im Betrieb wird im
  Bildtakt gelesen (16-40 ms), also weit darueber.

Die geglaettete Geschwindigkeit im `JOG_MOVE`-Ereignis
(`meta["velocity_ticks_per_s"]`) bleibt davon unberuehrt: sie rechnet auf
der Uhr der Input-Schicht. Zwei Zeitbasen zu mischen waere ein stiller
Fehler - eine einzige Differenz zwischen Epoche und monotoner Uhr ergibt
Milliarden Millisekunden und danach dauerhaft Geschwindigkeit null.

## Das virtuelle Jogwheel

`ui/widgets.py`, `JogWidget`. Zwei Flaechen wie am Geraet:

| Eingabe | Wirkung |
| --- | --- |
| Ziehen auf der **Platte** | Beruehrung **und** Drehung |
| Ziehen auf dem **Rand** | nur Drehung, keine Beruehrung |
| Mausrad | nur Drehung (mit Strg fuenffach, fuer Backspins) |

Der Winkel kommt aus `atan2` zwischen Jog-Mitte und Zeiger; die Differenz
zweier Zeigerereignisse wird auf +/-180 Grad gefaltet. Damit gibt es am
Uebergang 359 -> 0 Grad keinen Sprung, und beliebig viele volle
Umdrehungen sind moeglich - der Positionszaehler laeuft nicht um.

Das Widget erzeugt **kein** Ereignis. Es legt die Schritte im Zaehler ab;
weitergegeben wird im Bildtakt (`VirtualSource.poll_jog()`, gerufen aus
`CdjApplication.tick()` bzw. dem Bildtakt des Simulators).

**Die sichtbare Drehung ist nie die Quelle.** `visual_angle` laeuft bei 360
Grad um und dient nur der Darstellung; die Trackposition kommt
ausschliesslich aus der Deck-Engine. Wuerde die Anzeige zurueckgelesen,
ginge bei jeder Umdrehung Bewegung verloren. `accumulated_angle` zaehlt
ohne Umlauf weiter und ist reine Diagnose.

Verlaesst der Zeiger das Jogwheel oder wird ausserhalb losgelassen, endet
die Eingabe sicher: `on_release()` bzw. `cancel()` setzen die Beruehrung
zurueck. Richtung und Geschwindigkeit ergeben sich danach von selbst zu
null, weil ohne weitere Schritte keine Bewegung mehr gemeldet wird.

Unter dem Jogwheel steht eine Diagnosezeile mit genau den Werten der
Schnittstelle:

```
TOUCH JA   FWD  POS +799  +1240 t/s  +360°
```

## Bedeutung des Touch-Zustands

Die Eingabeseite meldet nur **ob** beruehrt wird. Was daraus wird,
entscheidet allein die Deck-Engine anhand von `DeckState.jog_mode` - es gibt
keine Vinyl-Logik im virtuellen Jogwheel und keine zweite in der Hardware:

|  | spielt | pausiert |
| --- | --- | --- |
| **VINYL**, Platte beruehrt | Vinyl-Stop bzw. Scratch | Frame Search |
| **VINYL**, Rand (Touch 0) | Pitch Bend | Pitch Bend |
| **CDJ** (Rand und Platte) | Pitch Bend | Frame Search |

Direkter Zugriff auf die Trackposition (Scratch / Frame Search) heisst: eine
Umdrehung = ein Beat, folgt damit dem Tempo des Tracks. Pitch Bend ist ein
kleiner Positionsversatz, unabhaengig vom Tempo.

Ohne gueltiges Beatgrid gibt es keine Beats - dann bleibt es beim
Scratchen bei der Plattenlaenge eines Vinyl-Tellers
(`JOG_SECONDS_PER_REV`).

Die Platte im Vinyl-Modus anzufassen haelt die Wiedergabe an; mit
eingeschaltetem SLIP laeuft die Zeitachse dabei im Hintergrund weiter.
Beides steht in [PLAYER_MODES.md](PLAYER_MODES.md).

Unabhaengig davon gilt: ist ein Loop im Adjust-Modus, verschiebt dieselbe
Bewegung den Loop-Punkt statt der Wiedergabe, ebenfalls mit einem Beat je
Umdrehung (siehe [LOOP.md](LOOP.md)).

## Geraeteprotokoll

Zusaetzlich zu den bisherigen Zeilen (siehe
[HARDWARE_MAPPING.md](HARDWARE_MAPPING.md)):

```
Q JOG_MOVE 10       Pegel beider Lichtschranken, A zuerst
C JOG_TOUCH 812     Rohwert des kapazitiven Sensors
P JOG_MOVE 15427    fertiger Positionszaehler eines Geraets, das selbst zaehlt
```

Diese drei Zeilen erzeugen **kein** Ereignis; sie gehen in das
Scan-Programm. Ein Geraet, das die Quadratur selbst auswertet, meldet nur
`P` und `T`/`C` - die Auswertung hier bleibt dieselbe.

## Kalibrierung

```bash
python run_jog.py                      # Demo-Signal, kein Geraet noetig
python run_jog.py --stdin              # Protokollzeilen von der Standardeingabe
python run_jog.py --on 820 --off 780   # Grenzwerte vorbelegen
python run_jog.py --invert             # Drehrichtung umkehren
```

Das Fenster zeigt Rohwert, Minimum, Maximum, Durchschnitt, Grenzwert,
Touch-Zustand, Positionszaehler und Fehlerzahl, dazu den Verlauf des
Rohwerts mit beiden Grenzlinien. Der Grenzwert laesst sich eintippen oder
aus der Messung vorschlagen: einmal beruehren und loslassen, dann
*Vorschlag*. Ohne Spanne zwischen Ruhe und Beruehrung gibt es keinen
Vorschlag - ein geratener Grenzwert waere nur eine Behauptung.

Mit `--stdin` liest ein Hintergrundfaden dasselbe Geraeteprotokoll; die
Oberflaeche zeigt davon unabhaengig den jeweils aktuellen Stand. Damit
laesst sich ein angeschlossenes Geraet einmessen, obwohl der Transport in
`HardwareSource.start()` noch fehlt:

```bash
<geraet> | python run_jog.py --stdin
```

## Was noch fehlt

**Der Transport zum ESP32.** `HardwareSource.feed_line()` und `poll_jog()`
sind fertig und getestet, aber niemand ruft sie im laufenden CDJ-Fenster
auf - es gibt noch keine offene serielle Verbindung. Sobald es sie gibt,
gehoert `poll_jog()` neben den bereits vorhandenen Aufruf der virtuellen
Quelle in `CdjApplication.tick()`.

**Die Rampe der Vinyl-Bremse.** Der Halt selbst ist da: die Platte im
Vinyl-Modus anzufassen stoppt die Wiedergabe (`Deck._vinyl_hold()`),
Loslassen nimmt sie wieder auf. Was fehlt, ist die **Rampe** - am Geraet
laeuft der Ton aus und wieder an, und die Dauer haengt an `VINYL SPEED
ADJUST`. `DeckState.vinyl_speed_adjust` ist vorhanden und wird gefuehrt, die
Rampe selbst ist es nicht; heute ist der Uebergang hart. Das ist eine
Aenderung an der Deck-/Audioschicht, nicht an der Jog-Eingabe - Scratch,
Pitch Bend, Frame Search und Slip funktionieren ohne sie vollstaendig.

## Tests

`tests/test_jog.py` - Quadraturtabelle, Decoder, Touch-Sensor, Scanner,
Leser, Demo-Signal, Protokoll und der Weg bis in die Deck-Engine.
`tests/test_jog_input.py` - der Eingabezustand (`JogMovement`), die
Gleichheit beider Quellen und die Bedienszenarien: Rand gegen Platte,
Scratch, Pitch Bend, CDJ-Modus, Frame Search, mehrere Umdrehungen,
Backspin, Loslassen ausserhalb, schnelle Bewegung ohne Warteschlange.
`tests/test_ui.py` - das Jog-Widget und die Kalibrierungsoberflaeche.
