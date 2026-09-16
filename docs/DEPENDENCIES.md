# Externe Abhaengigkeiten

Das Projekt war bis zur Audioschicht reine Standardbibliothek. Ab jetzt nicht
mehr. Jede Abhaengigkeit ist hier begruendet, mit Angabe welche Programmteile
davon abhaengen.

## Verwendet

### numpy 2.4.6

* **wofuer**: internes Audioformat (float32-Arrays), Waveform-Vorberechnung,
  Resampling-Indexrechnung im Audio-Callback, Beatgrid-Mathematik.
* **warum keine Eigenimplementierung**: Ohne vektorisierte Arrays ist im
  Audio-Callback keine Echtzeit moeglich - eine Python-Schleife ueber 512
  Samples pro Kanal reisst jede Puffergrenze.
* **abhaengig**: `virtual_cdj/audio/*` vollstaendig.

### soundfile 0.14 (libsndfile 1.2.2)

* **wofuer**: Dekodieren von WAV, FLAC, **MP3**, AIFF, OGG.
* **warum**: libsndfile 1.2 dekodiert MP3 selbst. Damit entfaellt ffmpeg fuer
  alle praktisch relevanten DJ-Formate ausser AAC/M4A. Ein eigener
  MP3-Dekoder waere ein Projekt fuer sich.
* **abhaengig**: nur `audio/decoder.py` (`SoundFileDecoder`). Der Rest des
  Programms kennt ausschliesslich `AudioDecoder`.

### sounddevice 0.5.5 (PortAudio 19.7)

* **wofuer**: Audioausgabe ueber einen Callback-Stream.
* **warum**: PortAudio ist der Standard fuer geringe Latenz auf Windows, Mac
  und Linux. Eine Eigenimplementierung waere plattformspezifischer C-Code.
* **abhaengig**: nur `audio/engine.py`.

### librosa 1.0.0

* **wofuer**: Onset-Huellkurve, Tempo-Schaetzung, Beat-Tracking
  (`beat_track`), Chroma fuer die Tonarterkennung.
* **warum keine Eigenimplementierung**: Beat-Tracking nach Ellis
  (Onset-Novelty + dynamische Programmierung) ist bewaehrt, aber nicht
  trivial. librosa ist die etablierte, lokal nutzbare Referenz und laeuft
  offline.
* **wichtige Einschraenkung**: librosa liefert bei elektronischer Musik
  regelmaessig Oktavfehler (halbes oder doppeltes Tempo) und keine
  Downbeats. Beides wird in `audio/analysis/tempo.py` nachbehandelt, siehe
  dort.
* **abhaengig**: nur `audio/analysis/tempo.py` und `audio/analysis/key.py`.

### soxr 1.1.0

* **wofuer**: qualitativ hochwertiges Resampling beim Import, wenn die
  Dateirate von der Engine-Rate abweicht.
* **warum**: Bandbegrenztes Resampling korrekt selbst zu bauen ist
  aufwendig; soxr ist schnell und wird von librosa ohnehin mitgebracht.
* **wichtig**: Resampling laeuft **ausschliesslich beim Laden**, niemals im
  Audio-Callback.
* **abhaengig**: nur `audio/resample.py`.

### scipy 1.18.0

* **wofuer**: Butterworth-Filterbank (`sosfilt`) fuer die Bandaufteilung der
  RGB-Waveform.
* **abhaengig**: nur `audio/analysis/waveform.py`.

### mutagen

* **wofuer**: Tags (Titel, Interpret, Album, Genre, Label) und eingebettetes
  Artwork lesen.
* **warum**: Tag-Formate (ID3, Vorbis, MP4-Atome) selbst zu parsen ist reine
  Fleissarbeit ohne Erkenntnisgewinn.
* **abhaengig**: nur `audio/loader.py`.

### Pillow 12.3.0

* **wofuer**: Waveform-Bilder erzeugen und als `PhotoImage` auf das
  Tk-Canvas blitten; Artwork skalieren.
* **warum**: Ein Tk-Canvas mit tausenden Einzellinien pro Frame erreicht
  keine 60 FPS. Ein vorgerendertes Bild pro Frame erreicht sie.
* **abhaengig**: nur `virtual_cdj/cdj_ui/waveform_render.py`.

## Bewusst nicht verwendet

| Bibliothek | Grund |
| ---------- | ----- |
| `madmom` | Beste Downbeat-Erkennung, aber unmaintained, braucht Cython-Build und laeuft auf Python 3.14 nicht ohne Weiteres. |
| `essentia` | Sehr gute Rhythmusanalyse, aber keine Wheels fuer Python 3.14 unter Windows. |
| `aubio` | Keine Wheels fuer Python 3.14. |
| `ffmpeg` / `PyAV` | Erst noetig fuer AAC/M4A. Der Dekoder ist so gebaut, dass ein PyAV-Backend nachgeruestet werden kann, ohne dass sich am Rest etwas aendert - siehe `audio/decoder.py`. |
| Time-Stretching (Rubber Band, `pyrubberband`) | Erst fuer Master Tempo / Key Lock. Die Architektur sieht dafuer `TempoProcessor` vor. |

## Installation

```bash
python -m pip install numpy scipy soundfile sounddevice librosa soxr mutagen Pillow
```

`numba` und `scikit-learn` kommen als Abhaengigkeiten von librosa mit.

## Formatabdeckung

| Format | Dekoder | Status |
| ------ | ------- | ------ |
| WAV | libsndfile | vollstaendig |
| FLAC | libsndfile | vollstaendig |
| AIFF | libsndfile | vollstaendig |
| MP3 | libsndfile | vollstaendig |
| OGG/Vorbis | libsndfile | vollstaendig |
| AAC / M4A / ALAC | – | **nicht unterstuetzt**. `AudioDecoder`-Backend fuer PyAV nachruestbar. |
