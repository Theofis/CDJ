# Anwendungsmodi und Seitenstruktur

Die Oberflaeche hat mehrere Ebenen. Welche gilt, entscheidet ein einziger
Wert:

```
Application_Mode = PERFORMANCE | MENU | TEST | CALIBRATION | SETTINGS
```

Er steht in `shell/modes.py` (`ModeController`) - nicht in einer GUI-Seite.
Daran haengen zwei Dinge: welche Seite der Bildschirm zeigt, und ob eine
Hardware-Eingabe ueberhaupt bis zum Deck kommt.

Davon getrennt gibt es `OperatingMode.MIDI` und `OperatingMode.CDJ`. Diese
Auswahl wechselt nur das Backend hinter dem gemeinsamen `DeckController` und
bleibt beim Oeffnen von Test oder Kalibrierung erhalten. Siehe
[OPERATING_MODES.md](OPERATING_MODES.md).

## Schichten

```
Hardware
   |
Sensor / Input Engine     core/input_layer.py, jog/
   |
HardwareState             shell/hardware_state.py     <- zentraler Zustand
   |
Application Logic         shell/modes.py, shell/router.py, deck/
   |
GUI                       cdj_ui/pages.py und die Seiten
```

Keine Seite wertet Hardware selbst aus. Alle lesen denselben
`HardwareState`; die Kalibrierwerte stehen im `CalibrationStore`, und
angewandt werden sie in der Input-Schicht - nicht in der Anzeige.

## Die Seiten

| Modus | Seite | Inhalt |
| ----- | ----- | ------ |
| `PERFORMANCE` | `cdj_ui/screen.py` (`CdjScreen`) | die bestehende DJ-Oberflaeche: Wellenform, BPM, Beatgrid, Trackinfo, Loop, Jog-/Playback-Status, Browse mit Playlists, Trackliste und Load |
| `MENU` | `cdj_ui/menu_page.py` | Auswahl der Bereiche und Unterauswahl `OPERATING MODE` |
| `TEST` | `cdj_ui/test_page.py` | alle Ein- und Ausgaenge live |
| `CALIBRATION` | `cdj_ui/calibration_page.py` | Touch-Sensor, Jogwheel, Fader und Potis einstellen und speichern |
| `SETTINGS` | `cdj_ui/settings_page.py` | Anzeige- und Audiozustand, gespeicherte Werte |

Die Performance-Oberflaeche wurde dafuer **nicht** umgebaut. Sie haengt als
`PerformancePage` im `PageHost`; beim Verlassen haelt nur ihre Bildschleife
an. Deck und Audio takten aus der Anwendung, ein Track spielt also weiter,
waehrend jemand im Menue oder in der Pruefung ist.

## Bedienung

| Taste | Wirkung |
| ----- | ------- |
| `F1` | Menue oeffnen / zurueck |
| `F2` | direkt in die Pruefung |
| `Esc` | zurueck zur Performance (dort: Kiosk-Modus verlassen) |
| `Bild auf` / `Bild ab` | im Menue blaettern |
| Beruehrung / Klick | Menueeintrag, Schaltflaeche, LED-Zeile |
| `SHIFT + MENU` am Bedienfeld | persoenliche Einstellungen direkt oeffnen |

Jede Seite ausser der Performance-Oberflaeche hat oben eine Schaltflaeche
**MENUE**. Auf dem 7-Zoll-Touchscreen gibt es keine Tastatur - ohne sie
waere die Pruefung eine Sackgasse, denn dort loest kein Taster mehr etwas
aus.

## Eingaben je Modus

`shell/router.py` steht zwischen Input-Schicht und DJ-Logik:

```
InputLayer -> InputRouter -> InputMapper -> DeckCommand -> DeckController
                                                         |
                                                    ModeManager
                                                         |
                                                aktives DeckBackend
                  |
                  +-> Beobachter der aktuellen Seite
```

```
Application_Mode == PERFORMANCE   Eingaben -> DJ-Funktionen
Application_Mode == TEST          Eingaben -> nur Testanzeige
Application_Mode == CALIBRATION   Eingaben -> nur Kalibrierung
Application_Mode == MENU/SETTINGS keine DJ-Funktion
```

Ein Hotcue-Taster in der Pruefung faerbt also seine Zeile ein, aber der
Track springt nicht. Der **Zustand** wird trotzdem in jedem Modus
fortgeschrieben - das macht die Input-Schicht; der Router sperrt nur den
Weg zur Deck-Engine. `InputRouter.blocked` zaehlt, was gesperrt wurde.

## Pruefung / Test

Drei Spalten, alles live aus `HardwareState`:

* **Taster** - `Pressed = 0 / 1` fuer jedes Element der Komponentenliste.
* **LEDs** - Klick auf eine Zeile schaltet die einzelne LED, die beiden
  Schaltflaechen schalten alle. Dazu Schalter, Encoder und ein ehrlicher
  Hinweis, dass es kein Joystick-Element gibt.
* **Analog und Jogwheel** - Rohwert und normierter Wert je Fader/Poti,
  darunter Touchsensor, Touch-Rohwert, Lichtschranke A und B,
  Quadraturzustand, `Jog_Position_Count`, `Jog_Delta`, Richtung,
  Umdrehungen und `Error_Count`.

In der Kopfzeile steht der Kommunikationsstatus: welche Quelle zuletzt
gesendet hat, wie viele Ereignisse ankamen und ob ueberhaupt ein Geraet
angeschlossen ist.

## Kalibrierung

Zwei Reiter:

* **TOUCH / JOG** - die Ansicht aus `ui/jog_calibration.py`, mit der
  Palette des CDJ-Bildschirms: Rohwert, Minimum, Maximum, Durchschnitt,
  beide Grenzwerte, Touch-Zustand, Live-Diagramm und die Quadraturwerte.
  Siehe [JOG.md](JOG.md).
* **FADER / POTI** - je Element: Rohwert, eingemessenes Minimum und
  Maximum, Mittelstellung, Totzone und der normierte Wert.
  `MESSEN` startet die Messung, das Element einmal von Anschlag zu Anschlag
  bewegen, `FERTIG` uebernimmt. Ohne Bewegung wird nichts uebernommen.
  `MITTE` legt die aktuelle Stellung auf 0.5 (Fader mit Rastung),
  `TOTZONE +` erhoeht in Schritten von 1 % bis 5 % und faengt dann wieder
  bei 0 an, `ZURUECK` verwirft die Messung.

`SPEICHERN` schreibt alles nach `config/calibration.json` - auch die
Grenzwerte des Touch-Sensors. Beim naechsten Start liest `app.py` die Datei
und haengt sie an die Input-Schicht; ab dann rechnet diese mit den
eingemessenen Werten. Fehlt die Datei oder ist sie kaputt, gelten die
Vorgaben aus `core/controls.py`, und der Fehler steht in der Oberflaeche.

## Eine Seite ergaenzen

1. Eintrag in `ApplicationMode` und in `MENU_ENTRIES` (`shell/modes.py`).
2. Seite als `Page`-Unterklasse anlegen (`cdj_ui/`), mit `mode`, `title`
   und `refresh()`.
3. In `CdjDisplayWindow._register_pages` anmelden.

Die Performance-Oberflaeche wird dabei nicht angefasst.

`OPERATING MODE` ist bewusst keine weitere `ApplicationMode`-Seite: Die
Unterauswahl bleibt innerhalb des Menues und steuert den unabhaengigen
`ModeManager`. Damit werden Diagnoseebenen und Backendwahl nicht vermischt.

## Was bewusst fehlt

Das virtuelle Bedienfeld (`run.py`) kennt die Modi nicht - es ist die
Simulation der Hardware, nicht die Oberflaeche des Geraets. Fuer die
Kalibrierung ohne CDJ-Fenster gibt es weiterhin `run_jog.py`.
