# Waveform: Analyse, Cache, Renderer

Die Waveform ist keine Amplitudenlinie, sondern eine Darstellung mit zwei
getrennten Aussagen:

* **Hoehe** = Lautstaerke
* **Farbe** = spektrale Zusammensetzung

Beides wird getrennt vorberechnet und getrennt gezeichnet. Das ist der
Unterschied zwischen einer selbstgebauten Waveform und der CDJ-Darstellung.

## Kette

```text
TRACK
 └── Analysis            einmal beim Import, dann nie wieder
      ├── BPM, Beatgrid, Downbeats, Tonart
      ├── low   (bis 250 Hz)
      ├── mid   (250 - 2500 Hz)
      ├── high  (ab 2500 Hz)
      ├── peak  (breitbandiger Spitzenwert)
      └── rms   (breitbandiger Effektivwert)
             │
             ▼
      Analysecache (.npz, uint8 je Wert)
             │
             ▼
      WaveformRenderer
             │
     ┌───────┴────────┬─────────────────┐
     ▼                ▼                 ▼
Hauptwellenform   Overview         Master-Zeile
```

Im Betrieb macht der Renderer nur noch: Position lesen, Cachebereich
waehlen, auf Spaltenzahl zusammenfassen, zeichnen. **Keine Analyse im
Renderthread** - das ist die Voraussetzung fuer vier Decks.

## Was in welcher Schicht steckt

| Schritt | Ort | Bemerkung |
| ------- | --- | --------- |
| Bandtrennung, Peak, RMS | `audio/analysis/waveform.py` | Butterworth 4. Ordnung, vektorisiert |
| Ablage und Versionierung | `audio/cache.py` | `ANALYSIS_VERSION = 4` |
| Datenmodell | `deck/state.py` (`WaveformData`) | `low, mid, high, peak, rms` |
| Amplitude, Farbe, Zeichnen | `cdj_ui/waveform_render.py` | nur numpy, kein Audio |
| Zoom, Modus, Fenster | `deck/display_state.py` | reine Anzeige |

## Balkenhoehe

Je Bildschirmspalte **ein** Wert. Aus den Werten der Spalte:

```text
Peak  -> Maximum       (Transienten bleiben erhalten)
RMS   -> Wurzel des Mittels der Quadrate  (energierichtig)

Amplitude = 0.65 * RMS + 0.35 * Peak
Massstab  = 0.99 / groesste Amplitude des ganzen Tracks
Anzeige   = log1p(10 * Amplitude * Massstab) / log1p(10)
```

Nur der Peak ergaebe eine zappelige Linie, nur der RMS einen Block ohne
Kick. Die logarithmische Stauchung haelt Leises sichtbar (10 % Pegel
werden rund 32 % Hoehe), ohne dass laute Stellen dauerhaft am Rand kleben.

Liegen keine Dynamikwerte vor (aeltere Analyse, einfache Quelle), faellt die
Hoehe auf das lauteste Band zurueck. Es wird nichts erfunden.

### Der Anzeigemassstab

Die Werte der Analyse sind auf 0-1 normiert, aber die Mischung aus RMS und
Peak erreicht die 1 nie: der Peak ist bei der lautesten Stelle 1.0, der RMS
dort aber deutlich kleiner. Gemessen kam die lauteste Stelle je nach Track
nur auf **67 % bis 94 %** der verfuegbaren Hoehe - bei einem leisen Track
blieb also ein Drittel der Flaeche leer.

`display_scale()` rechnet deshalb einmal je Aufloesungsstufe den groessten
Anzeigewert des **ganzen** Tracks aus und skaliert darauf, mit 1 %
Sicherheitsabstand zum Rand.

Drei Eigenschaften, die dabei wichtig sind:

* **Ueber den ganzen Track, nicht ueber den Ausschnitt.** Eine
  abschnittsweise Normierung liesse die Waveform beim Scrollen pumpen:
  derselbe Kick waere im leisen Intro hoch und im lauten Drop niedrig.
* **Vor der Stauchung.** Danach zu skalieren wuerde die Kurve verbiegen.
* **Einmal je Stufe.** Das Ergebnis haengt am unveraenderlichen
  `WaveformData` und wird nicht in jedem Bild neu gerechnet.

Abgeschnitten wird nichts: der Massstab ist so gewaehlt, dass die lauteste
Stelle knapp unter dem Rand landet. `clip(..., 0, 1)` ist danach nur noch
eine Absicherung gegen Rundung.

Das Audio bleibt davon voellig unberuehrt - skaliert wird die Zeichnung,
nicht der Ton.

## Farbe

**Nicht** `R = low, G = mid, B = high`. Jede ausgeglichene Mischung wuerde
dabei weiss und die Aussage waere verloren. Stattdessen aus dem
*Verhaeltnis*:

```text
Anteile  L, M, H  =  Band / (low + mid + high)
Gewichte w        =  Anteil ^ 1.6, normiert
Farbe             =  w_L * Orange + w_M * Gruen-Tuerkis + w_H * Blau
Breitbandigkeit   =  1 - (max(w) - min(w))
Farbe            +=  (Weiss - Farbe) * Breitbandigkeit * 0.55
Helligkeit        =  0.62 + 0.38 * Amplitude
```

Ankerfarben: Bass `(255,106,26)`, Mitten `(74,214,138)`, Hoehen
`(72,156,255)`.

Gemessen an einem synthetischen Takt (Kick auf jedem Beat, Hi-Hat auf den
Achteln), echte Analyse:

| Stelle | Baender (L/M/H) | Farbe |
| ------ | --------------- | ----- |
| Kick   | 0.74 / 0.03 / 0.04 | R252 G109 B31 (orange) |
| Hi-Hat | 0.02 / 0.09 / 0.44 | R81 G165 B246 (blau) |

Die drei Modi des Geraets bleiben erhalten: `RGB` nutzt diese Palette,
`3BAND` zeichnet Bass, Mitten und Hoehen als getrennte Flaechen (blau,
orange, weiss), `BLUE` ist einfarbig.

## Kanten

Gefuellt wird von der Mittellinie nach oben und unten. Die Innenflaeche ist
deckend, die beiden Randzeilen je Spalte werden mit Teildeckung gezeichnet.

Bewusste Abweichung von der Vorgabe "intern in doppelter Auflösung rendern":
eine Spalte ist genau ein Pixel breit, waagerecht entsteht also gar kein
Treppenmuster. Nur die Ober- und Unterkante braucht Glaettung. Die kostet
so Aufwand in der Groessenordnung der Bildbreite statt der vierfachen
Bildflaeche - bei gleichem Ergebnis.

## Zoom

Musikalisch, nicht in Sekunden:

```text
Stufen:  2, 4, 8, 16, 32, 64 Beats     (Standard 8 Beats = 2 Takte)
Fenster: Beats * 60 / aktuelle BPM
```

Damit zeigt dieselbe Stufe bei 124 und bei 156 BPM gleich viele Beats, und
das Fenster folgt dem Temporegler. Ohne gueltiges Beatgrid gibt es keine
Beats, auf die man sich beziehen koennte - dann bleibt es ein Zeitfenster
(2 bis 64 s) und die Anzeige sagt "s" statt "BEATS".

## Schichten beim Zeichnen

```text
1 Hintergrund
2 Waveform (ein geblittetes Bild)
3 Beatgrid   - Beat dünn, Taktanfang stark
4 Loop, Cues, Hot Cues
5 Playhead (fest in der Mitte)
6 Text
```

Das Beatgrid ist **nicht** Teil des Waveformbildes. Nur so bleibt es beim
Zoomen scharf und deckungsgleich mit den Markern; alle Overlays benutzen
dieselbe Zeit-zu-Pixel-Funktion `_x_of`.

Der Playhead steht fest in der Mitte, die Waveform wird darunter
verschoben. Die Position kommt aus `DeckState.position_s`, also aus dem
Audio-Thread - es gibt keine zweite, entkoppelte Animation.

## Gemessen

1024 x 220 Pixel, Demo-Track, 40 Bilder je Modus:

| Modus | Zeit je Bild |
| ----- | ------------ |
| RGB   | 2.89 ms |
| 3BAND | 3.19 ms |
| BLUE  | 2.88 ms |

Budget fuer 60 FPS: 16.7 ms. Overview (1024 x 56) und Master-Zeile
(1024 x 64) brauchen je rund 1.3 ms. Ein Regressionstest haelt die
Hauptwellenform unter 6 ms.

3BAND fuellt eine Indexkarte und uebersetzt sie einmal in Farben, statt drei
Farbflaechen uebereinanderzulegen - das hat 6.3 ms auf 3.2 ms gebracht.

## Grenzen

* Die interne rekordbox-Farbformel ist nicht veroeffentlicht. Reproduziert
  werden die sichtbaren Eigenschaften, nicht der Originalalgorithmus.
* Die Demo-Tracks sind mittenbetont, weil das synthetische Signal zwischen
  den Kicks von den Mitten dominiert wird. Bei echten Tracks liefert die
  Analyse die uebliche Verteilung (siehe Messtabelle oben).
* Waagerechte Glaettung gibt es nicht und braucht es nicht - eine Spalte
  ist ein Pixel.
