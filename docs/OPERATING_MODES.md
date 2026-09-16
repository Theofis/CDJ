# MIDI- und CDJ-Betriebsmodus

Die Anwendung hat zwei voneinander unabhaengige Arten von Modus:

- `ApplicationMode` bestimmt die sichtbare Ebene: Performance, Menue,
  Hardware-Test, Kalibrierung oder Einstellungen.
- `OperatingMode` bestimmt ausschliesslich das Deck-Backend: `MIDI` oder
  `CDJ`.

Test und Kalibrierung veraendern den gewaehlten Betriebsmodus nicht. Ausserhalb
der Performance sperrt der vorhandene `InputRouter` weiterhin DJ-Kommandos.

## Signalweg

```text
Hardware / virtuelles Panel / GUI
                |
           InputMapper
                |
           DeckCommand
                |
         DeckController       <- gemeinsame State-/Command-Fassade
                |
           ModeManager        <- einzige Backend-Auswahl
          /           \
 MidiBackend       CdjBackend
          \           /
             DeckState        <- einzige Datenquelle der GUI
```

`DeckController` implementiert weiterhin `DeckStateProvider`. Deshalb musste
die Performance-GUI nicht auf konkrete Backends umgebaut werden. Auch Laden,
Play/Cue, Pads, Loops, Beat Jump, Tempo und der komplette Jog-Payload laufen
ueber diese Fassade.

## Kontrollierter Wechsel

`ModeManager.set_mode()` fuehrt einen Wechsel in dieser Reihenfolge aus:

1. Pruefen, ob eines der aktiven Decks spielt. In diesem Fall wird der Wechsel
   blockiert und die Operating-Mode-Seite zeigt den Grund an.
2. Bisheriges Backend mit `stop()` anhalten.
3. Ziel-Backend mit `start()` starten. Schlaegt das fehl, wird das alte Backend
   wieder gestartet.
4. Den neuen Modus atomar in `config/settings.json` speichern. Andere
   Settings-Schluessel bleiben erhalten.
5. Listener und damit die `DeckController` auf das neue Backend umhaengen.

Jedes Backend hat seinen eigenen Deck-State. Beim Wechsel sieht die GUI sofort
den State des Zielsystems; lokaler und entfernter Transport werden nicht
unbemerkt vermischt.

## Aktueller Mock-Stand

- `MidiBackend` ist ein Mock fuer den spaeteren Ethernet-Client zur
  `RekordboxBridge`. Es hat noch keinen Netzwerktransport.
- `CdjBackend` ist der Mock-Routingadapter fuer die lokale Seite. Er verwendet
  die bereits vorhandene `Deck`-/Loop-/Hotcue-Logik und darf die bestehende
  AudioEngine nutzen; es wurde keine zweite AudioEngine gebaut.
- Beide Backends geben Befehle lesbar aus, zum Beispiel `[MIDI] PLAY` oder
  `[CDJ] JOG delta=4 velocity=0.72 direction=CW touched=true`.
- Der `DeckState.connection_state` meldet derzeit `MOCK_READY`. Spaetere
  Implementierungen koennen dort `CONNECTING`, `CONNECTED` oder Fehlerzustaende
  abbilden.

Der naechste sinnvolle Schritt ist ein versioniertes Nachrichtenprotokoll fuer
`MidiBackend <-> RekordboxBridge` (Command-Envelope, State-Snapshots,
Reconnect/Heartbeat). Danach kann der CDJ-Adapter schrittweise um USB-Erkennung
und lokale Library-Anbindung erweitert werden, ohne GUI oder Controls erneut zu
aendern.
