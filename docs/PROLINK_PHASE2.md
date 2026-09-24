# PRO LINK Phase 2: echter Read-only-Provider

Phase 2 ergänzt die Phase-1-Grenze um einen strikt passiven
`RealProLinkProvider`. Oberhalb des Providers bleibt der Datenweg unverändert:

```text
PRO DJ LINK UDP
  -> RealProLinkProvider
    -> PlayerState / BeatEvent
      -> bestehender MasterManager
        -> bestehende SyncEngine
          -> MasterState
            -> bestehende GUI
```

Der Provider sendet keine Datagramme. Er kündigt keine eigene Playernummer an,
fordert weder MASTER noch SYNC an und implementiert weder Remote Load noch
Library-, dbserver-, NFS- oder Waveform-Zugriffe. Die AudioEngine ist
unverändert.

## Belegte Protokollbasis

Es werden ausschließlich Felder dekodiert, die in mehreren belastbaren Quellen
belegt sind:

- [Deep Symmetry DJ Link Packet Analysis](https://djl-analysis.deepsymmetry.org/)
- [Deep Symmetry beat-link](https://github.com/Deep-Symmetry/beat-link),
  insbesondere `DeviceAnnouncement`, `Beat`, `CdjStatus` und
  `PrecisePosition`
- [usr-ein/prolink](https://github.com/usr-ein/prolink), dessen UDP-Codecs und
  Tests auf 37 Aufzeichnungen echter CDJ-2000NXS-Hardware beruhen

Unbekannte Bytes werden nicht interpretiert. Jeder empfangene Datensatz bleibt
mit vollständigen Rohbytes in einem begrenzten Diagnosejournal erhalten;
`get_raw_diagnostics()` liefert standardmäßig die letzten 256 Einträge.

## Gelesene Daten

| Quelle | Port/Typ | Sicher gelesene Daten |
|---|---|---|
| Keepalive | UDP 50000 / `0x06` | Playernummer, Gerätename, CDJ/Mixer-Rolle, IP, MAC, Peerzahl, Online/Lebenszeichen |
| Beat | UDP 50001 / `0x28` | Original-BPM, Pitch, effektive BPM, Beat-Flanke, Beat-in-Bar |
| Precise Position | UDP 50001 / `0x0b` | CDJ-3000-Familie: Position in ms, Tracklänge, Pitch und effektive BPM |
| CDJ Status | UDP 50002 / `0x0a` | Play/Pause/Reverse, MASTER, SYNC, On-Air, Trackreferenz, BPM, Pitch, Track-Beat und Beat-in-Bar |

Ein klassisches Beat- oder Statuspaket enthält keine belastbare absolute
Zeitposition. Deshalb bleibt `position_valid=False`, bis ein belegtes
`0x0b`-Precise-Position-Paket empfangen wurde. Aus BPM oder Beatnummer wird
keine Position geraten.

Typ `0x28` enthält ebenfalls keine absolute Track-Beatnummer. Wenn ein zuletzt
empfangener Status eine Track-Beatnummer liefert, wird sie als Anker verwendet.
Ohne Status vergibt der Provider ausschließlich für den internen
`BeatEvent`-Vertrag eine monoton steigende, sitzungslokale Ereignisnummer. Sie
wird nicht als vom Netzwerk dekodierter Wert ausgegeben; Beat-in-Bar stammt
weiterhin direkt aus dem Paket.

## Passive Statusgrenze

Keepalives und Beats sind Broadcasts und können ohne Teilnahme am Netzwerk
gelesen werden. CDJ-Statuspakete sind dagegen normalerweise Unicasts an Peers,
die sich zuvor als Gerät angekündigt haben. Ein gewöhnlich angeschlossener,
rein passiver Rechner erhält daher in der Regel **keine** Transport-, MASTER-
oder SYNC-Statuspakete.

Der Decoder und der komplette Providerpfad dafür sind vorhanden und mit einem
aus echtem Hardwareverkehr stammenden Status-Skeleton getestet. Der Provider
wertet Status aus, wenn die Netzwerktopologie ihn tatsächlich zustellt. Er
sendet aber absichtlich keine Ankündigung, denn das wäre bereits ein eigener
virtueller PRO-DJ-LINK-Teilnehmer und liegt außerhalb von Phase 2.

## Start und Interface

```bash
# Automatisch: genau eine 169.254.x.x-Adresse wird bevorzugt,
# bei uneindeutiger Lage wird passiv auf allen lokalen IPv4-Interfaces gehört.
python run_cdj.py --prolink-source real --no-audio

# Empfohlen bei mehreren Netzwerkkarten: lokale IPv4 explizit angeben.
python run_cdj.py --prolink-source real \
  --prolink-interface 169.254.10.20 --no-audio
```

`--prolink-interface` nimmt eine lokale IPv4-Adresse oder `auto`. Der Provider
bindet UDP 50000, 50001 und 50002. Ein Bind-/Interfacefehler wird strukturiert
protokolliert und in das Diagnosejournal geschrieben; die Hauptanwendung läuft
weiter und der Empfänger versucht die Bindung erneut.

Keepalives werden von echter Hardware ungefähr alle zwei Sekunden gesendet.
Nach zehn Sekunden ohne irgendein gültiges Paket wird ein Player offline/stale
gemeldet, nach dreißig Sekunden aus dem Snapshot entfernt. Ein späteres Paket
legt ihn ohne Neustart wieder online an.

## Automatisierte Tests

`tests/test_prolink_real.py` prüft:

- ein bytegenaues, real aufgezeichnetes CDJ-Keepalive;
- ein bytegenaues, real aufgezeichnetes Beatpaket;
- einen aus einem Real-Capture stammenden Status-Skeleton, bei dem nur belegte
  Felder verändert werden;
- Device erscheint, Status-/MASTER-/BPM-Update und BeatEvent;
- präzise Position nur über den dokumentierten CDJ-3000-Pakettyp;
- stale, Entfernen und Wiedererscheinen;
- unbekannte und kaputte Pakete ohne Prozessabbruch;
- Socket-/Provider-Ausfall ohne Abbruch der `CdjApplication`;
- Rohpaket -> Provider -> `MasterManager` -> `MasterState` -> vorhandener
  `MasterDeckView`.

## HARDWARE TEST REQUIRED

### Broadcast-Discovery und Beat

Benötigt werden ein PRO-DJ-LINK-fähiger Pioneer-/AlphaTheta-Player, ein
Ethernet-Switch oder eine Direktverbindung und die für dieses Netz bestimmte
IPv4-Adresse des Rechners. Eingehendes UDP 50000 bis 50002 muss in der lokalen
Firewall erlaubt sein.

1. Anwendung mit `python run_cdj.py --prolink-source real
   --prolink-interface <lokale-ip> --no-audio` starten.
2. Player einschalten und eine in rekordbox analysierte Datei laden.
3. Wiedergabe starten und Pitch verändern.

Erwartet: Der Player erscheint spätestens nach dem nächsten Keepalive mit
Nummer und Modellname. Während der Wiedergabe folgen BeatEvents; Original-BPM,
Pitch, effektive BPM und Beat-in-Bar ändern sich plausibel. Nach Trennen des
Netzwerkkabels wird er nach zehn Sekunden offline, nach Wiederanschluss ohne
Anwendungsneustart wieder online.

### CDJ-3000-Position

Benötigt wird ein CDJ-3000 oder ein anderes Gerät, das nachweislich Typ `0x0b`
sendet. Erwartet: Mit geladenem Track wird `position_valid=True`, Position und
Tracklänge folgen dem Display des Players; Pause und Seek erzeugen keine aus
BPM geschätzten Sprünge.

### Transport, MASTER und SYNC

Benötigt werden mindestens zwei reale Player und eine **rein passive**
Aufzeichnungsmöglichkeit, die deren gerichteten UDP-50002-Verkehr tatsächlich
sieht, etwa ein korrekt konfigurierter Mirror-Port. Broadcastsichtbarkeit
allein reicht als Nachweis nicht. Die aufgezeichneten unveränderten
Statusdatagramme sind über `ingest_datagram()` zu replayen.

Erwartet: Play/Pause, MASTER-Wechsel, SYNC, BPM/Pitch, Track-Beat und
Beat-in-Bar werden ohne Ausnahme oder unbekannte Feldannahme in `PlayerState`,
`MasterState` und der vorhandenen Masteranzeige sichtbar. Auf einem normalen
Switch ohne Mirror-Port ist das Ausbleiben dieser Unicasts im strikt passiven
Modus das erwartete Ergebnis.

## Noch nicht implementiert

- irgendein Senden auf PRO-DJ-LINK-Ports;
- eigener virtueller CDJ und Device-Number-Claiming;
- MASTER-/SYNC-Kommandos, Remote Load und Track laden;
- Library, NFS, dbserver, Metadaten und Waveforms;
- Positionsrekonstruktion für Geräte ohne Precise-Position-Paket;
- Audio-Sync oder Änderungen an der AudioEngine.

## Sinnvolle Phase 3

Nach den Hardwaretests sollte Phase 3 zuerst die Kompatibilitätsmatrix und
Capture-Replay-Sammlung erweitern. Falls vollständiger Live-Transport und
MASTER/SYNC auf einem normalen Switch benötigt werden, wäre danach eine
separat aktivierbare, minimal aktive Presence-/Device-Claim-Schicht die
nächste Architekturentscheidung. Sie müsste ausdrücklich freigegeben und vom
read-only Provider getrennt werden. Erst anschließend wären Trackmetadaten und
Waveforms über die bestehenden `TrackMetadata`-/`TrackAnalysis`-Haken sinnvoll;
Audio-Sync bleibt davon unabhängig.
