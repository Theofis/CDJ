# Sichtqualitaet der Oberflaeche

Ziel dieser Runde: dieselbe Oberflaeche, aber schaerfer, ruhiger und
technischer - naeher an einem CDJ-3000-Display, ohne Funktion oder Aufbau
zu aendern.

Der Auftrag war fuer Web und CSS formuliert. Dieses Projekt ist Python mit
Tkinter, PIL und numpy. Die Punkte gelten sinngemaess:

| Auftrag | Hier |
| ------- | ---- |
| `devicePixelRatio`, Canvas-Skalierung | Tk-Schriftskalierung, Pixelschriften |
| CSS-Variablen | `cdj_ui/theme.py` |
| SVG-Icons | auf dem Canvas gezeichnete Symbole |
| React-Rerender vermeiden | Redraw je Bereich an einer Signatur |
| `box-shadow`, `border-radius` | gibt es auf einem Tk-Canvas nicht |
| Browser-Controls | gibt es nicht - alles ist selbst gezeichnet |

## 1. Der grosse Fund: Schriften waren DPI-abhaengig

`tk scaling` steht auf diesem Rechner auf **1.3333**. Tk rechnet
Punktgroessen damit in Pixel um - das Layout rechnet aber selbst in Pixeln.
Jeder Text war also ein Drittel groesser als die Flaeche, fuer die er
gedacht war, **und die Groesse haette sich auf dem 7-Zoll-Panel wieder
geaendert**.

Negative Schriftgroessen bedeuten in Tk Pixel. Damit gilt jetzt

```text
1 Layoutpixel = 1 Bildpixel
```

unabhaengig von der Bildschirm-DPI. `Metrics.font()` und `Metrics.mono()`
geben negative Groessen zurueck; `FONT_PIXEL_FACTOR = 4/3` haelt die
gewohnte optische Groesse und ist die einzige Stelle zum Nachstellen.

Ziffern: Segoe UI und Consolas haben beide feste Ziffernbreiten (je 15 px
gemessen). `154.9` und `155.0` sind gleich breit - Zeit, BPM und Tempo
springen beim Zaehlen nicht. Ein Test haelt das fest.

## 2. Pixelgenaue Linien

Tk zeichnet eine 1-px-Linie **um** die Koordinate. Bei `x = 100.0` verteilt
sie sich auf zwei Pixelreihen und wirkt weich; bei `x = 100.5` deckt sie
genau eine. `Metrics.snap()` legt Koordinaten auf die Pixelmitte. Angewandt
auf Beatgrid, Playhead, Spaltentrenner, Trennlinien und die Master-Zeile.
Ein Test prueft, dass jede senkrechte 1-px-Linie der laufenden Wellenform
auf einer halben Koordinate liegt.

## 3. Flaechen und Text: Leitern statt Einheitsfarben

Nicht jede Flaeche ist dasselbe Schwarz, und nicht jeder Text ist weiss.

```text
Flaechen (dunkel -> hell)      Text (hell -> dunkel)
WAVE_BG      #050607            TEXT           #eef1f5
BG           #07080a            TEXT_SECOND    #a9b2bd
PANEL        #0e1014            TEXT_DIM       #727c88
PANEL_HI     #15181d            TEXT_MUTED     #4a525c
PANEL_ACTIVE #1d2128            TEXT_DISABLED  #333a42
```

Die Wellenformflaeche ist die dunkelste - die Wellenform selbst hebt sich
damit am staerksten ab. Die Abstufungen sind bewusst klein: sie deuten
Ebenen an, ohne hellgraue Karten zu bilden. Zwei Tests halten die Reihenfolge
fest, ein dritter, dass ausserhalb von `theme.py` keine Farbe definiert wird.

Akzente sind gedaempft statt neon (`#2f9ae8` statt `#3aa7ff`,
`#22c176` statt `#26d07c`).

## 4. Wellenform: heller Kern

Zusaetzlich zur Amplitude aus RMS und Peak bekommt der Bereich nahe der
Mittellinie (42 % der halben Balkenhoehe) 35 % mehr Helligkeit. Das ist der
Unterschied zwischen einer gleichmaessigen Flaeche und der Tiefe, die
rekordbox zeigt.

Kosten: auf echten Daten 2.89 -> 2.95 ms je Bild, weil nur die Zeilen
bearbeitet werden, die der lauteste Balken erreicht, und die Abstandskarte
zur Mittellinie einmal je Bildgroesse berechnet wird statt je Bild.

## 5. Beatgrid in drei Stufen

Vorher: Beat dezent, Taktanfang 2 px. Jetzt drei Stufen, alle 1 px und
pixelgenau:

```text
Beat        BEAT_LINE   #39434f   nur mittlere 56 % der Hoehe
Taktanfang  BAR_LINE    #6e7c8c   volle Hoehe
Phrase      PHRASE_LINE #aab6c4   volle Hoehe, alle 4 Takte
```

Damit ist das Raster lesbar statt nur gestreift, und man sieht auf einen
Blick, wo eine Phrase beginnt.

## 6. Playhead

Vorher eine 2 px weisse Linie mit grossem Dreieck - auf halber Koordinate
also drei angerissene Pixelreihen. Jetzt:

* ein Pixel `#ffffff`, pixelgenau
* links und rechts ein Pixel in Wellenformschwarz als Saum, damit die Kante
  auch auf heller Wellenform steht
* eine kleine Kerbe oben statt eines Dreiecks, das die Wellenform verdeckt

## 7. Master-Zeile anders als die eigene Wellenform

Die Master-Zeile ist die Vergleichsanzeige, nicht die Arbeitsanzeige:

| | eigene Wellenform | Master-Zeile |
| - | ----------------- | ------------ |
| Aufloesung | feinste passende Stufe | eine Stufe groeber |
| Beatgrid | Beat, Takt, Phrase | nur Takt und Phrase |
| Position | Playhead mit Kerbe | schlichter Strich in Orange |

Sie behaelt dasselbe Zeitfenster - nur so ist der Vergleich aussagekraeftig.

## 8. Symbole werden gezeichnet

Vorher standen in der Oberflaeche `←`, `▸`, `▶`, `▲`, `▼`, `♪`, `ⓘ`, `›`,
`✕`, `−`, `◀◀`, `▶▶` als Schriftzeichen. Deren Groesse, Strichstaerke und
senkrechte Lage haengen an der Schrift, sie sitzen nie auf dem Pixelraster,
und je nach installierter Schrift fehlen sie ganz.

`cdj_ui/icons.py` zeichnet 23 Symbole mit Linien und Flaechen: gemeinsame
Groessenangabe, gemeinsame Strichlogik (1 px fein, 2 px ab Groesse 18),
Koordinaten auf dem Pixelraster, Farbe aus `theme`. Ein Test prueft, dass
jedes Symbol etwas zeichnet und dieselbe optische Groesse einhaelt.

## 9. Rahmen reduziert

Rahmen sind durch Flaechenkontrast ersetzt. Beispiele:

* Panels: eine Akzentlinie oben statt eines 2-px-Rahmens um alles
* Werttasten: aktiv gefuellt, inaktiv ruhige Flaeche - kein Rahmen je Taste
* Listenauswahl: Flaeche plus 2-px-Balken links statt Rahmen um die Zeile
* Trennlinien: `LINE_SUBTLE` (#1b1f25) statt `BORDER`, 1 px, pixelgenau

## 10. Redraw je Bereich statt alles in jedem Bild

Vorher machte `set_display()` jeden Bereich in jedem Bild ungueltig - die
ganze Oberflaeche wurde 60-mal je Sekunde neu gezeichnet, auch der
Tracktitel.

Jetzt hat jeder Bereich eine `signature()`: die Werte, die er sichtbar
macht. Neu gezeichnet wird nur, wenn sich diese Signatur aendert.

Gemessen an 240 Bildern mit laufender Wiedergabe (154 BPM, Master aktiv):

| Bereich | Neuzeichnungen | Anteil |
| ------- | -------------- | ------ |
| laufende Wellenform | 240 | 100 % |
| Master-Zeile | 240 | 100 % |
| Statuszeile | 48 | 20 % |
| Overview | 24 | 10 % |
| Wellenform-Kopfzeile | 7 | 3 % |
| Kopfzeile | 1 | 0.4 % |
| Pads und Status | 1 | 0.4 % |
| **gesamt** | **561 statt 1680** | **33 %** |

Die Raten sind kein Ratespiel: die Wellenform zeichnet neu, sobald sich das
Bild um **ein Pixel** verschieben wuerde; die Statuszeile mit 20 Hz, weil
mehr bei einer Millisekundenanzeige nicht lesbar ist; die Overview mit
10 Hz, weil ihr Positionsstrich rund 3 px je Sekunde wandert.

Ergebnis: **9.87 ms je Bild statt vorher 10.79** bei gleichzeitig besserer
Darstellung - rechnerisch 101 Bilder je Sekunde bei einem Ziel von 60.

## 11. Referenzvergleich (nur Entwicklung)

`python run_cdj.py --reference bild.png`, dann **F4** wechselt zwischen dem
Referenzbild und der eigenen Oberflaeche.

**Kein halbtransparentes Uebereinanderlegen.** Tk kann Canvas-Widgets nicht
miteinander verrechnen; ein echtes Ueberblenden braeuchte eine
Bildschirmaufnahme des sichtbaren Fensters und waere damit von
Fenstersichtbarkeit und Betriebssystem abhaengig. Der direkte Wechsel zeigt
Unterschiede in Position, Schriftgewicht und Kontrast mindestens so
deutlich. Ein Differenzmodus ist aus demselben Grund nicht umgesetzt.

Ohne `--reference` existiert die Ansicht im laufenden Programm nicht.

## 12. Sichtpruefung als Test

`tests/test_visual_quality.py`, 14 Tests. Sie halten fest:

* Schriftgroessen sind Pixel, nicht Punkte
* Ziffern haben feste Breite - keine springenden Zahlen
* `snap()` legt Linien auf die Pixelmitte
* die Flaechenleiter und die Textleiter stimmen in der Reihenfolge
* kein reines Weiss fuer Text, keine hellgraue Flaeche
* ausserhalb von `theme.py` wird keine Farbe definiert
* kein Schriftzeichen wird als Symbol benutzt
* jedes Symbol zeichnet etwas und haelt seine Groesse
* **kein Text laeuft aus seiner Flaeche** - geprueft in Wiedergabe, allen
  vier Panels, SOURCE, BROWSE, Trackliste mit Infospalte und grosser Schrift
* **kein Text ueberdeckt anderen Text** in denselben Ansichten
* jede senkrechte 1-px-Linie der Wellenform liegt auf einer halben Koordinate
* Bereiche zeichnen nicht ohne sichtbare Aenderung neu

Die Ueberdeckungspruefung zieht die Zeilenkaesten senkrecht um ein Fuenftel
je Seite ein: Tk liefert den Kasten samt Ober- und Unterlaenge, den die
Glyphen nicht ausfuellen. Ohne diesen Abzug meldet jede zweite Zeile einen
Treffer, den man nicht sieht.

## 13. Was bewusst nicht geaendert wurde

Aufteilung, Reihenfolge und Funktion der Anzeigen bleiben, wie sie waren -
Trackinformationen, BPM, Zeit, Wellenformen, Beatgrid, Cue- und
Hotcue-Marken, Master- und Deckanzeige, Bedienlogik. Geaendert wurde nur,
**wie** gezeichnet wird.

## 14. Stand

495 Tests, alle bestanden. Wellenform 2.95 ms (RGB), 3.14 ms (3BAND),
2.96 ms (BLUE) je Bild bei 1024x220. Gesamtbild 9.87 ms.


---

# Unterer Bereich nach Handbuch S. 21-23

## Was falsch war

Der untere Bereich hatte drei Zeilen: Statuswerte, Uebersichtswellenform und
eine **Pad-Reihe mit A bis H** samt LOOP-, SYNC-, MASTER- und
QUANTIZE-Schaltflaechen. Diese Reihe gibt es am CDJ-3000 nicht. Dort sind die
Hot Cues Tasten **unter** dem Display; auf dem Schirm erscheinen sie als
Marken auf beiden Wellenformen. Der Bildschirm zeigte also eine Leiste, die
die vorhandene Hardware doppelt bedient - und verbrauchte dafuer 72 Pixel.

Ausserdem lagen Werte an Stellen, die das Handbuch anders vorsieht: Tonart
und MT standen oben rechts statt in der rechten Spalte, QUANTIZE und BEAT
JUMP mittig statt in der linken Spalte.

## Was das Handbuch vorgibt

Die Wiedergabestatusanzeige ist **L-foermig**: zwei Seitenspalten, die ueber
beide Zeilen laufen, dazwischen oben die Werte und unten die gesamte
Wellenform.

```text
┌────────┬──────┬──────────┬─────────────┬────────┬────────┬────────┐
│ PLAYER │TRACK │A.HOT CUE │   REMAIN    │ TEMPO  │  BPM   │ MASTER │
│  (( 2 ))│  02 │ AUTO CUE │ 03:10.780   │  ±10   │ 128.00 │        │  14-23
│        │      │          │      SINGLE │+3.20 % │        │        │
├────────┼──────┴──────────┴─────────────┴────────┴────────┼────────┤
│QUANTIZE│  A      C          E    F                       │   MT   │  13, 24
│   1    │ ▁▂▅█▇▅▂▁▃▅███▇▅▃▂▁▅███▇▅▃▂▁▂▅█▇▅▂▁▃▅███▇▅▃▂▁▂  │        │  26
│BEATJUMP│ 0:00     1:00      2:00     3:00     4:00  5:00 │  KEY   │  12, 25
│   16   │                                                 │   4A   │
└────────┴─────────────────────────────────────────────────┴────────┘
```

Zugeordnet: 12 Beat Jump, 13 Quantisierung, 14 Playernummer, 15 Tracknummer,
16 A. HOT CUE, 17 AUTO CUE, 18 Zeit, 19 SINGLE/CONTINUE, 20 Tempo,
21 Einstellbereich, 22 BPM, 23 MASTER/SYNC, 24 MT, 25 Tonart,
26 gesamte Wellenform.

## Was jetzt anders ist

| | vorher | jetzt |
| - | ------ | ----- |
| Zeilen unten | 3 (68 + 56 + 72 = 196 px) | 2 (52 + 74 = 126 px) |
| Pad-Reihe | vorhanden | entfaellt - am Geraet Hardware |
| Seitenspalten | keine | links 88 px, rechts 96 px, durchlaufend |
| Tonart, MT | oben rechts | rechte Spalte (Elemente 24, 25) |
| QUANTIZE, BEAT JUMP | mittig oben | linke Spalte (Elemente 13, 12) |
| Loop-Beatzahl | in der Pad-Reihe | an der Wellenform (Element 1) |
| Gesamte Wellenform | volle Breite, 56 px | zwischen den Spalten, 74 px |
| Cue-Marken | Striche mit Buchstaben daneben | farbige Fahnen darueber |
| Zeitskala | nur Anfang und Ende | Minutenmarken, bis 15 min (S. 90) |
| Gespielter Teil | gleich hell | abgedunkelt |
| **Laufende Wellenform** | **322 px** | **392 px** |

Die Anordnung von rechts nach links ist jetzt gerechnet, nicht gesetzt: BPM
steht am Rand, davor Tempo, davor der Wiedergabestatus, davor die Zeit. Jeder
Block gibt seine gemessene linke Kante an den naechsten weiter. Damit kann
sich nichts ueberlappen, egal wie breit ein Wert wird - vorher stiessen bei
+2.34 % und -2:30.000 vier Werte aneinander.

## Bedienung bleibt erreichbar

Die Pad-Reihe war die einzige Beruehrflaeche fuer Hot Cues. Ersatz:

* die acht Pads am Bedienfeld (unveraendert)
* die Zifferntasten **1 bis 8** am Displayfenster - sie senden dieselben
  ``PAD``-Kommandos

SYNC, MASTER und QUANTIZE waren als Schaltflaechen ohnehin doppelt: es gibt
sie als Taster am Bedienfeld. Ihr **Zustand** steht jetzt dort, wo das
Handbuch ihn zeigt.

## Die Wellenform wird verschoben, nicht neu gerechnet

Die groessere Wellenform kostete zunaechst Leistung: ``render()`` brauchte
**12.8 ms**, davon rund 6 ms allein fuer das Uebertragen des
1024x392-Bildes nach Tk - in **jedem** Bild. Das Gesamtbild lag bei 21 ms,
also unter 60 FPS.

Zwischen zwei Bildern verschiebt sich die Wellenform aber nur um wenige
Pixel; ihr Inhalt bleibt derselbe. Jetzt wird ein breiteres Bild mit
``MARGIN_PX = 256`` Vorrat je Seite gerechnet und danach nur noch
verschoben. Neu gerechnet wird erst, wenn der Vorrat aufgebraucht ist oder
sich Track, Zoom oder Farbmodus aendern.

Gemessen: **7 Neuberechnungen fuer 120 Bilder** ueber 6 Sekunden Trackzeit
statt 120. Der Zeitfehler durch das ganzzahlige Verschieben liegt unter
einem halben Pixel, also unter 1.5 ms - unsichtbar, und zwei Tests halten
beides fest.

## Messung

| | vor dem Umbau | nach dem Umbau |
| - | ------------- | -------------- |
| Bildzeit | 9.87 ms | **8.62 ms** |
| moegliche Bildrate | 101 /s | **116 /s** |
| laufende Wellenform | 322 px | 392 px |
| ``scrolling.render()`` | 4.6 ms | 0.27 ms |
| Neuzeichnungen | 33 % | 38 % |

Der Anteil der Neuzeichnungen steigt leicht, weil die Statuszeile und die
Uebersicht ihre Raten behalten, das Bild aber schneller fertig ist.

497 Tests, alle bestanden.
