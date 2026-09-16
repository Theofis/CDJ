# SOURCE, BROWSE und Drehregler nach Handbuch

Grundlage: Bedienungsanleitung CDJ-3000 (DRI1588-A), Seiten 14-16 (Tasten),
18 (SOURCE), 19-20 (Durchsuchen), 21-23 (Wiedergabebildschirm), 24-25
(Bedienung mit Drehregler und Touch), 36-46 (Track-Auswahl), 72 (Beatgrid).

Diese Analyse steht **vor** der Umsetzung. Sie listet, was das Handbuch
verlangt, was das Projekt schon hat und was sich aendert.

## 1. Vorhandene Hardware gegen Handbuch

Das Bedienfeld dieses Projekts hat oberhalb des Displays sechs Taster
(SOURCE, BROWSE, TAG LIST, PLAYLIST, SEARCH, MENU), einen druckbaren Drehgeber
(`BROWSE_ROTATE` + `BROWSE_PRESS`) und vier Taster um den Drehgeber:
BACK, TAG TRACK/REMOVE, TRACK FILTER/EDIT und SHORTCUT.

| Handbuch (S. 15-16) | Bei uns vorhanden | Folge |
| ------------------- | ----------------- | ----- |
| 22 SOURCE | ja | SOURCE-Bildschirm |
| 23 BROWSE | ja | Durchsuchen-Bildschirm |
| 24 TAG LIST | ja | Taste vorhanden; Tag-Liste noch nicht umgesetzt |
| 26 PLAYLIST | ja | Taste vorhanden; Playlist ist bereits Browser-Kategorie |
| 27 SEARCH | ja | Suchbildschirm mit Tastatur fehlt (siehe 6.) |
| 28 MENU/UTILITY | ja | kurz: zuletzt gespielte Tracks (S. 37); lang: UTILITY fehlt |
| 29 BACK | ja | eine Ebene hoeher, halten: oberste Ebene |
| 30 TAG TRACK/REMOVE | ja | Taste vorhanden; Tag-Funktion noch nicht umgesetzt |
| 31 Drehregler | ja | drehen, druecken, halten |
| 32 TRACK FILTER/EDIT | ja | Taste vorhanden; Trackfilter noch nicht umgesetzt |
| 33 SHORTCUT | ja | Taste vorhanden; SHORTCUT-Bildschirm nicht umgesetzt |

Alle physischen Eingaben sind im Simulator vorhanden. Noch fehlende
Bildschirmfunktionen bleiben ausdruecklich offen, statt eine falsche Wirkung
vorzutaeuschen.

## 2. SOURCE-Bildschirm (S. 18, 36)

| Nr. | Element | Quelle des Werts |
| --- | ------- | ---------------- |
| 1 | Gerätesymbol mit Geraetetyp und Playernummer | Quellenliste der Anwendung |
| 2 | Gerätename, Markierung "Loaded" | Quellenliste |
| 3 | Geräteinformation: Songs, Playlists, Date, Total, Available | Quellenliste |
| 3 | BACKGROUND COLOR | **nicht umgesetzt** |
| 3 | MY SETTINGS LOAD | **nicht umgesetzt** |
| - | CONTROL MODE (S. 76) | **nicht umgesetzt** |
| 9 | Wiedergabestatusanzeige am unteren Rand | `DeckState` des eigenen Decks |

Bedienung: Drehen markiert, Druecken bestaetigt (S. 24). Touch: erste
Beruehrung markiert, zweite bestaetigt (S. 25). Die bestaetigte Quelle ist
danach die Quelle des Durchsuchen-Bildschirms.

**Wichtig:** Es werden nur Quellen angezeigt, die es wirklich gibt. Ohne
Demo-Modus und ohne geladene Datei bleibt die Liste leer und sagt das. Es
werden keine USB- oder SD-Geraete erfunden.

## 3. Durchsuchen-Bildschirm (S. 19-20)

| Nr. | Element | Umsetzung |
| --- | ------- | --------- |
| 1 | Kategorien links | aus der Quelle abgeleitet, nur belegte Kategorien |
| 2 | Gerätesymbol | aktive Quelle |
| 3 | ← eine Ebene hoeher | Kopfzeile, gleiche Wirkung wie BACK |
| 4 | Name des Ordners oder der Liste | Pfad der aktuellen Ebene |
| 5 | PREVIEW ein/aus | Wellenformspalte in der Liste |
| 6 | Schriftgroesse | zwei Stufen |
| 7 | INFO ein/aus | Infospalte rechts zum markierten Track |
| 8 | Trackliste, Titelzeile sortiert | Spalten #, TRACK, ARTIST, BPM, KEY |
| 8 | ⇕ Unterspalten-Auswahl | **nicht umgesetzt** (rekordbox-Voreinstellung) |
| 9 | Wiedergabestatusanzeige | bleibt unten sichtbar |

Hierarchie (S. 19): mit Bibliothek Kategorien, ohne Bibliothek Ordner- und
Tracklisten. Beide Faelle werden abgebildet: Quellen mit
`has_library=True` zeigen Kategorien, Dateiquellen zeigen Ordner.

Gruen dargestellte Tracks bedeuten laut S. 42 "wurde gespielt" (Verlauf,
nach etwa einer Minute Wiedergabe) - nicht "ist geladen". Der geladene
Track wird wie im Handbuch mit ▶ markiert.

## 4. Drehregler (S. 24, 22 Element 11, 38)

| Aktion | Ansicht | Wirkung |
| ------ | ------- | ------- |
| drehen | SOURCE | Quellenauswahl bewegen |
| drehen | BROWSE | Cursor bewegen |
| drehen | BROWSE, Sprungmodus | Alphabet- oder Seitensprung |
| drehen | WAVEFORM, Zoom-Modus | Wellenform vergroessern/verkleinern |
| drehen | WAVEFORM, Rastermodus | Beatgrid verschieben |
| druecken | SOURCE | Quelle bestaetigen |
| druecken | BROWSE, Ebene markiert | Ebene betreten |
| druecken | BROWSE, Track markiert | Track laden, Wellenform anzeigen |
| halten | BROWSE | Sprungmodus ein/aus (Alphabet bzw. Seite) |
| halten | WAVEFORM | Zoom- <-> Rastermodus |

Der Rastermodus verschiebt das Beatgrid wirklich: `BeatGrid.shifted()` ist
im Datenmodell vorhanden. Damit ist Element 11 keine Attrappe.

BACK (S. 25): kurz eine Ebene hoeher, halten springt auf die oberste Ebene.
Auf der obersten Ebene fuehrt BACK zurueck zum Wiedergabebildschirm.

## 5. Was aus der bisherigen Oberflaeche verschwindet

Die Reiterleiste (`CdjTouchNav` mit WAVEFORM / BROWSE / INFO / CUE/LOOP /
SETTINGS) hat am Gerät kein Gegenstueck. Drei ihrer Reiter zeigten
ausserdem denselben Wellenform-Inhalt - also eine Anzeige ohne Funktion.
Sie wird entfernt. Die 40 Pixel gehen an die Wellenform, die Umschaltung
uebernehmen die Taster des Bedienfelds.

Damit das Display auch ohne Bedienfeld bedienbar bleibt (`--no-panel`),
bekommt das Fenster Tastaturkuerzel, die genau dieselben Kommandos senden
wie die Taster: F5 SOURCE, F6 BROWSE, F7 MENU, F8 SEARCH, Escape/Backspace
BACK, Pfeil hoch/runter Drehgeber, Return Drehgeber druecken, Shift+Return
Drehgeber halten.

Auf dem SOURCE- und dem Durchsuchen-Bildschirm entfaellt die Kopfzeile mit
Track und den Panel-Tasten - am Gerät steht dort die Kopfzeile des
jeweiligen Bildschirms (S. 18, 19). Unten bleibt die
Wiedergabestatusanzeige.

## 6. Bewusst nicht umgesetzt (Kategorie C)

Suchbildschirm mit Tastatur (S. 37), Trackfilter (S. 39-40), Touch Preview
(S. 41), Tag-Liste (S. 44-46), Hot-Cue-Bank (S. 43), UTILITY- und
SHORTCUT-Bildschirm (S. 77-84), MY SETTINGS (S. 85-86), CONTROL MODE
(S. 76), LOAD LOCK (S. 36), Unterspalten-Auswahl (S. 20).

Der SEARCH-Taster oeffnet vorerst den Durchsuchen-Bildschirm; er bekommt
erst dann eine eigene Funktion, wenn der Suchbildschirm existiert.

## 7. Neue und geaenderte Dateien

Neu: `virtual_cdj/deck/library.py` (Quellen- und Browse-Modell),
`virtual_cdj/cdj_ui/source_screen.py`, `tests/test_browse.py`.

Geaendert: `deck/display_state.py` (Anzeigezustand der Navigation),
`deck/commands.py` (Navigations- und Beatgrid-Kommandos),
`deck/engine.py` (Beatgrid verschieben), `deck/mapping.py` (Taster),
`cdj_ui/browser.py` (Hierarchie, Kopfzeile, Spalten),
`cdj_ui/screen.py` (Ansichten, Halte-Erkennung, Layout),
`cdj_ui/window.py` (Tastaturkuerzel), `app.py` (Quellen, Verlauf),
`run_cdj.py` (Verdrahtung). Entfernt: `cdj_ui/touch_nav.py`.

---

# Ergebnis der Umsetzung

## Was jetzt geht

**SOURCE-Taster** oeffnet die Quellenauswahl mit den wirklich vorhandenen
Quellen: der Demo-Quelle und - sobald eine Datei geladen wurde - der Quelle
`DATEIEN`. Rechts stehen Songs, Playlists, Date, Total und Available; wo es
keinen echten Wert gibt, steht ein Gedankenstrich. `BACKGROUND COLOR` und
`MY SETTINGS LOAD` sind sichtbar abgeblendet und als "nicht umgesetzt"
beschriftet. Ohne Quelle steht dort "KEINE QUELLE VERBUNDEN".

**BROWSE-Taster** oeffnet den Durchsuchen-Bildschirm: links die Kategorien
(nur belegte), rechts der Inhalt der markierten Kategorie, oben Pfad, `←`,
`PREVIEW`, Schriftgroesse und `INFO`, unten die Wiedergabestatusanzeige.
Die Titelzeile sortiert nach `#`, `TRACK`, `ARTIST`, `BPM` und `KEY`; ein
zweiter Druck auf dieselbe Spalte dreht die Richtung um. Gespielte Tracks
(Verlauf, ab einer Minute Spielzeit) erscheinen gruen, der geladene Track
mit `▶`.

**Drehregler** bewegt in SOURCE die Quellenauswahl, in BROWSE den Cursor und
auf dem Wiedergabebildschirm den Zoom. Druecken bestaetigt: eine Ebene
betreten oder einen Track laden. Halten schaltet in BROWSE den Sprungmodus
(Alphabet bzw. Seite) und auf dem Wiedergabebildschirm zwischen Zoom- und
Rastereinstellungsmodus um; im Rastermodus verschiebt Drehen das Beatgrid
wirklich (10 ms je Rastung).

**BACK** geht eine Ebene hoeher, auf der obersten Ebene zurueck zum
Wiedergabebildschirm; gehalten springt er auf die oberste Ebene.

**MENU** zeigt die zuletzt gespielten Tracks (HISTORY), **SEARCH** oeffnet
vorerst den Durchsuchen-Bildschirm.

## Geprueft

`tests/test_browse.py`, 39 Tests: Quellenmodell und Kategorien, Hierarchie,
Sortierung samt Richtung, Verlaufskennzeichnung, Ordnerhierarchie ohne
Bibliothek, Navigationszustand, Tastenmapping, Beatgrid-Verschiebung und
-Reset, sowie die Bedienung am Bildschirm (Quelle waehlen per Drehregler und
per zwei Beruehrungen, Ebenen betreten, laden, BACK kurz und lang,
Sprungmodus, Zoom gegen Rastermodus, Statuszeile bleibt sichtbar).

Gesamtstand: 436 Tests, alle bestanden.

## Offen

Suchbildschirm mit Tastatur, Trackfilter, Tag-Liste, Playlist-Bildschirm,
Hot-Cue-Bank, UTILITY, SHORTCUT, MY SETTINGS, CONTROL MODE, LOAD LOCK,
Unterspalten-Auswahl. Fuer TAG LIST, PLAYLIST, SHORTCUT und TRACK FILTER
fehlen ausserdem die Taster am Bedienfeld.

## Nachtrag: die Taster erreichten den Bildschirm nicht

Beim Nachpruefen am laufenden Programm zeigte sich eine Luecke in der
Verdrahtung. Der ``InputMapper`` schickte **alle** Kommandos direkt an den
``DeckStateProvider``:

```text
VirtualSource -> InputLayer -> InputMapper -> Deck        (vorher)
```

Das Deck kennt aber keine Ansichten - ``VIEW``, ``BACK``,
``BROWSE_ROTATE`` und ``BROWSE_PRESS`` sind dort ausdrueckliche
Leerbefehle. Die Navigationstasten und der Drehgeber taten am Bildschirm also
nichts; nur die Beruehrungen der Oberflaeche wirkten, weil die ueber
``CdjScreen.send_command`` laufen.

Behoben mit einem austauschbaren Empfaenger je Deck:

```text
VirtualSource -> InputLayer -> InputMapper -> CdjScreen -> Deck   (jetzt)
                                                  |
                                          Anzeigebefehle
```

``CdjApplication.set_command_sink(deck_id, sink)`` setzt den Empfaenger,
``dispatch()`` verteilt. Ohne gesetzten Empfaenger geht alles wie bisher
direkt ans Deck - der kopflose Betrieb bleibt gueltig. Der Bildschirm
behandelt Anzeigebefehle selbst und reicht den Rest weiter; es gibt also
weiterhin genau einen Weg vom Bedienelement zum Deck.

Die gesamte Verdrahtung steht jetzt in ``run_cdj.wire_display()`` - eine
Funktion, die auch der Test benutzt. Damit prueft er die echte Verdrahtung
und keine Nachbildung.

Belegt durch ``PanelChainTests`` in ``tests/test_browse.py``: die bereits
umgesetzten Navigationstasten, Drehen, Druecken, Halten von Drehgeber und BACK
sowie zwei Gegenproben - Transportbefehle erreichen weiterhin das Deck, und
ohne Oberflaeche gilt die alte Verdrahtung. Die neu bestaetigten TAG-/Filter-
und Shortcut-Funktionen besitzen zunaechst vollwertige Inputs; ihre noch
fehlenden Bildschirmaktionen sind ausdruecklich dokumentiert.
