# PLAYLIST, TAG LIST und Sortierung

Handbuch S. 19-20 (Durchsuchen), S. 24-25 (Drehregler), S. 40-41
(Tag List). Ergaenzt [CDJ3000_BROWSE_ANALYSIS.md](CDJ3000_BROWSE_ANALYSIS.md)
und [rekordbox-usb-import.md](rekordbox-usb-import.md).

## Grundsatz: eine Bibliothek, mehrere Einstiege

BROWSE, PLAYLIST, TAG LIST und der Verlauf sind **keine** eigenen
Bildschirme mit eigener Trackliste. Sie sind Kategorien derselben
``TrackListLibrary``:

```
        USB-Stick (nur lesend)
               |
        MediaLibrary  ->  TrackListLibrary  (eine je Datentraeger)
               |
   +-----------+-----------+-----------+-----------+
   | TRACK     | PLAYLIST  | TAG LIST  | HISTORY   |  Kategorien
   +-----------+-----------+-----------+-----------+
               |
        BrowseNode + BrowseEntry   (sortierte **Sicht**)
               |
        CdjBrowser                 (ein Bildschirm)
               |
        CommandType.LOAD           (eine Ladefunktion)
```

Daraus folgt unmittelbar, was die Aufgabe verlangt: es gibt keine zweite
Trackliste, keinen zweiten Player-Zustand, keine zweite Ladefunktion und
keine vier Bibliothekssysteme. Wer in der Tag List einen Track laedt,
durchlaeuft denselben Weg wie aus BROWSE.

Die Tasten springen deshalb nur in eine Kategorie:

| Taste | Kommando |
| --- | --- |
| BROWSE / SEARCH | ``VIEW`` ``view=BROWSE`` ``path=()`` |
| PLAYLIST | ``VIEW`` ``view=BROWSE`` ``path=("PLAYLIST",)`` |
| TAG LIST | ``VIEW`` ``view=BROWSE`` ``path=("TAG LIST",)`` |
| TAG TRACK / REMOVE | ``TAG_TRACK_TOGGLE`` |
| MENU | ``MENU`` (kontextabhaengig, siehe unten) |

Alle laufen ueber ``InputMapper`` -> ``DeckCommand`` -> ``CdjScreen``.
Die Oberflaeche kennt keinen GPIO; ein Mausklick im virtuellen Bedienfeld
und ein echter Taster erzeugen dasselbe Kommando.

## Zustand je Einstieg

``BrowseContext`` (LIBRARY / PLAYLIST / TAG_LIST) leitet sich allein aus
dem Pfad ab - eine Quelle der Wahrheit. ``CdjDisplayState.browse_memory``
haelt je Kontext den letzten ``BrowseView`` (Ebene, Markierung,
Sortierung). Wer in BROWSE Track 45 markiert, in die PLAYLIST wechselt und
zurueckkommt, steht wieder auf Track 45.

Die Schalter der Kopfzeile (PREVIEW, Schriftgroesse, INFO) sind dagegen
eine Vorliebe des Benutzers und gelten ueberall - sie werden beim Wechsel
uebernommen, nicht zurueckgesetzt.

## Sortierung

``deck/library.py`` hat **eine** Sortierfunktion, ``sort_entries()``. Jede
Liste benutzt sie.

| Spalte | Vergleich |
| --- | --- |
| ``#`` | Originalreihenfolge - Quelle, Playlist oder Reihenfolge des Vormerkens |
| ``TRACK`` / ``ARTIST`` | alphabetisch, Gross-/Kleinschreibung egal |
| ``BPM`` | **numerisch** |
| ``KEY`` | der gespeicherte Wert, **ohne** Camelot-Umrechnung |
| ``RATING`` | numerisch, 0-5 |
| ``TIME`` | numerisch, Sekunden |
| ``COLOR`` | der gespeicherte Farbname |

Drei Regeln, die nicht offensichtlich sind:

* **``#`` ist die Originalreihenfolge.** Innerhalb einer Playlist zaehlt
  ``#`` die Position *in dieser Playlist*, nicht in der Quelle - sonst
  ginge die von rekordbox vorgegebene Reihenfolge beim Anzeigen wieder
  verloren. Dasselbe gilt fuer Verlauf und Tag List. Aufsteigend nach
  ``#`` sortieren ist damit gleichbedeutend mit ``ORIGINAL ORDER``.
* **Fehlende Werte stehen immer am Ende**, in beiden Richtungen. Ein Track
  ohne BPM soll bei absteigender Sortierung nicht oben stehen und wie ein
  Messwert aussehen.
* **Sortiert wird eine Kopie.** ``sort_entries()`` liefert eine neue Folge;
  die Trackliste der Quelle und die Playlist-Reihenfolge auf dem Stick
  bleiben unberuehrt. Auf den Datentraeger wird ohnehin nie geschrieben.

Eine frisch geoeffnete Liste beginnt in ihrer gespeicherten Reihenfolge
(``BrowseView.entered()`` setzt die Sortierung zurueck). Erst ein Tippen
auf einen Spaltenkopf sortiert; dieselbe Spalte noch einmal dreht die
Richtung um. Welche Spalte gilt, zeigt ein ``▲``/``▼`` im Kopf.

## Tag List

``media_library/taglist.py``, ``TagListService``. Die Liste haengt **am
Datentraeger** (``source_id``), nicht am Player - steckt ein anderer Stick,
ist es eine andere Liste.

* hoechstens 100 Tracks (``MAX_TAG_LIST_TRACKS``)
* keine Duplikate; ein zweites Hinzufuegen verschiebt den Track auch nicht
  ans Ende, die Reihenfolge bleibt
* der Dienst fuehrt **nur Kennungen**, keine Tracks - aufgeloest wird ueber
  dieselbe Bibliothek
* eine Kennung, die es auf diesem Datentraeger nicht (mehr) gibt, faellt
  beim Aufloesen still weg, statt eine ungueltige Zeile zu erzeugen

Bedienung:

| Wo | Was | Wirkung |
| --- | --- | --- |
| BROWSE / PLAYLIST | TAG TRACK / REMOVE | markierten Track vormerken oder entfernen |
| Wellenform | TAG TRACK / REMOVE | **geladenen** Track vormerken oder entfernen |
| TAG LIST | Load-Regler lange druecken | markierten Track aus der Liste nehmen |
| TAG LIST | MENU | Tag-List-Menue |

Vorgemerkte Tracks tragen in **jeder** Liste einen gezeichneten Haken.

**Bewusste Abweichung:** in der Tag List entfernt langes Druecken des
Load-Reglers den markierten Track; in allen anderen Listen bleibt es der
Sprungmodus (S. 38). Zwei Bedeutungen fuer dieselbe Geste sind nicht
schoen, aber der Alphabet-Sprung ist in einer hoechstens 100 Eintraege
langen, selbst zusammengestellten Liste ohne Nutzen, und die Aufgabe
verlangt das lange Druecken ausdruecklich.

Entfernen entfernt **nur den Eintrag**: keine Audiodatei, keine Playlist,
keinen Datensatz in der rekordbox-Datenbank. Der Dienst kann den
Datentraeger gar nicht erreichen.

## Tag-List-Menue und CREATE PLAYLIST

MENU ist kontextabhaengig: in der Tag List oeffnet es das Tag-List-Menue
(S. 41), sonst den Verlauf (S. 37). Deshalb erzeugt die Taste ein eigenes
``CommandType.MENU`` statt eines festen ``VIEW`` - die Zuordnungsschicht
kennt die Ansicht nicht und darf das nicht entscheiden.

* ``REMOVE ALL TRACKS`` leert die Liste des aktiven Datentraegers.
* ``CREATE PLAYLIST`` ist **abgeblendet und wirkungslos**. Es braucht einen
  Schreibzugriff auf den Stick, und der ist nicht implementiert.

Der Platz dafuer ist vorbereitet, aber leer: ``TagListService`` fragt einen
``PlaylistWriter`` (Protokoll in ``taglist.py``). Ohne Writer meldet
``create_playlist()`` "nicht verfuegbar - die Bibliothek ist nur lesend
geoeffnet" und ruehrt nichts an. Die Namensvergabe nach dem Schema
``TAG LIST 001`` ist schon da und getestet, damit eine spaetere
Implementierung nur noch das Schreiben selbst beisteuern muss. In
``virtual_cdj/media_library/`` gibt es **keinen einzigen Schreibpfad** -
kein ``write_bytes``, kein ``open(..., "w")``, kein ``rename``, kein
``unlink``.

## Leere Zustaende

Es werden keine Playlists erfunden. Was eine leere Liste sagt, haengt
davon ab, warum sie leer ist:

| Lage | Anzeige |
| --- | --- |
| Datentraeger ohne rekordbox-Playlists | ``NO PLAYLISTS`` |
| Playlist ohne Tracks | ``PLAYLIST IST LEER`` |
| Tag List noch nicht gefuellt | ``TAG LIST IST LEER`` |
| sonst | ``KEINE EINTRAEGE`` |

Ein Stick ohne rekordbox-Bibliothek hat gar keine PLAYLIST-Kategorie; dort
gilt weiter die Ordner-/Dateistruktur (S. 19).

## Datentraeger entfernt

Verschwindet die Quelle, entfernt ``CdjApplication`` sie aus der
``MediaLibrary`` und der Bildschirm schaltet auf die naechste vorhandene
Quelle um. Die Trackliste ist dann leer, ``target_track_id()`` liefert
nichts, und TAG TRACK meldet "kein Track markiert" statt zu stuerzen. Die
Vormerkliste des Sticks bleibt im Speicher erhalten - ein versehentliches
Herausziehen soll sie nicht kosten; ``forget_device()`` verwirft sie auf
ausdruecklichen Wunsch.

Ein bereits **geladener** Track laeuft weiter: seine Samples liegen im
Speicher, nicht auf dem Stick.

## Farben und Sterne

Die Trackfarbe steht in ``export.pdb`` nur als **Name** (Tabelle
``colors``: Pink, Red, Orange, Yellow, Green, Aqua, Blue, Purple), nicht
als Farbwert. ``theme.TRACK_COLORS`` ist die Darstellung dieses Namens,
kein aus den Daten gelesener Wert. Ein unbekannter Name wird nicht
geraten, sondern nicht gezeichnet.

Haken und Sterne sind gezeichnete Symbole (``icons.check``,
``icons.star``), keine Schriftzeichen - so verlangt es
[VISUAL_QUALITY.md](VISUAL_QUALITY.md), und so bleiben sie unabhaengig von
den installierten Schriften.

Eine Bewertung von 0 zeigt einen Gedankenstrich, nicht fuenf leere Sterne:
"nicht bewertet" ist etwas anderes als "mit null bewertet".

## Was hier nicht ist

* **SEARCH** und **TRACK FILTER**: es gibt nur die Taster. SEARCH oeffnet
  bis auf Weiteres den Durchsuchen-Bildschirm. Beide Bildschirme muessen
  gebaut werden; die Sortier- und Listenschicht ist dafuer schon da.
* **Schreiben auf den Stick**: siehe oben.
