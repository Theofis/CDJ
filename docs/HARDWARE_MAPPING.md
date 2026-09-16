# Hardware-Zuordnung

`config/hardware_mapping.json` verbindet jede Control-ID mit einer
physikalischen Quelle. Solange dort `null` steht, ist das Element nur
virtuell bedienbar.

Die CDJ-Logik liest diese Datei nicht. Ein geaenderter Pin aendert deshalb
nichts an der Logik.

## Format

```json
{
  "version": 1,
  "note": "...",
  "mapping": {
    "PLAY": {
      "input_type": "digital",
      "hardware": null
    },
    "TEMPO_FADER": {
      "input_type": "analog",
      "hardware": null
    },
    "JOG_MOVE": {
      "input_type": "encoder",
      "hardware": null
    }
  }
}
```

`input_type` ergibt sich aus dem Control-Typ und wird beim Laden geprueft:

| Control-Typ | `input_type` |
| ----------- | ------------ |
| `DIGITAL_BUTTON` | `digital` |
| `ANALOG_FADER`, `ANALOG_POT` | `analog` |
| `ENCODER`, `JOG` | `encoder` |
| `SWITCH` | `switch` |
| `JOYSTICK` | `joystick` |
| `LED` | `led` |

## Erzeugen und aktualisieren

```bash
python tools/gen_hardware_mapping.py
```

Neue Bedienelemente werden mit `null` ergaenzt, entfallene entfernt, bereits
eingetragene Zuordnungen bleiben erhalten. Abweichungen werden als Warnung
gemeldet.

## Beispiele fuer belegte Eintraege

Das Feld `hardware` ist ein freies Objekt. Sinnvoll ist ein `driver`-Schluessel
plus die Angaben, die der jeweilige Treiber braucht.

Taster an einem Portexpander:

```json
"PLAY": {
  "input_type": "digital",
  "hardware": {
    "driver": "mcp23017",
    "device": 1,
    "i2c_address": "0x20",
    "pin": "GPA0",
    "active_low": true
  }
}
```

Fader am ADC des ESP32:

```json
"TEMPO_FADER": {
  "input_type": "analog",
  "hardware": {
    "driver": "esp32_adc",
    "gpio": 34,
    "raw_min": 0,
    "raw_max": 4095,
    "invert": false
  }
}
```

Jogwheel an zwei Lichtschranken (200 Schlitze, vier Flanken je Schlitz):

```json
"JOG_MOVE": {
  "input_type": "encoder",
  "hardware": {
    "driver": "quadrature_encoder",
    "channel_a": "GPIO25",
    "channel_b": "GPIO26",
    "slots_per_rev": 200,
    "ticks_per_rev": 800
  }
}
```

Jog-Beruehrung an einem kapazitiven Sensor:

```json
"JOG_TOUCH": {
  "input_type": "digital",
  "hardware": {
    "driver": "esp32_touch",
    "gpio": 4,
    "touch_on_threshold": 700,
    "touch_off_threshold": 650
  }
}
```

Die beiden Grenzwerte werden mit `python run_jog.py` eingemessen, nicht
geraten - siehe [JOG.md](JOG.md).

Die Angaben `raw_min` / `raw_max` und `ticks_per_rev` stehen zusaetzlich in
`virtual_cdj/core/controls.py`. Die Werte dort gelten fuer die Normierung in
der Input-Schicht; die Werte hier beschreiben die Hardware.

## Geraeteprotokoll

`sources/hardware.py` erwartet pro Ereignis eine Zeile. Der Dekoder ist
fertig, der Transport (serielle Schnittstelle, WebSocket, UDP) noch nicht.

```
D <CONTROL_ID> <0|1>        digitaler Pegel
A <CONTROL_ID> <rohwert>    analoger Rohwert, z. B. ADC 0..4095
E <CONTROL_ID> <delta>      Encoder, relativ
J <CONTROL_ID> <delta>      Jogwheel, relativ
T <CONTROL_ID> <0|1>        Jog-Beruehrung
S <CONTROL_ID> <POSITION>   Schalterposition
```

Rohdaten des Jogwheels. Sie gehen in das Scan-Programm und erzeugen
**kein** Ereignis - die Bewegung holt der Bildtakt mit `poll_jog()`
gebuendelt ab (siehe [JOG.md](JOG.md)):

```
Q <CONTROL_ID> <AB>         Pegel beider Lichtschranken, A zuerst
C <CONTROL_ID> <rohwert>    kapazitiver Sensor, Rohwert
P <CONTROL_ID> <zaehler>    fertiger Positionszaehler des Geraets
```

Zeilen, die mit `#` beginnen, und Leerzeilen werden ignoriert.

Beispiel:

```
D PLAY 1
D PLAY 0
A TEMPO_FADER 2048
E BROWSE_ROTATE -1
T JOG_TOUCH 1
J JOG_MOVE -12
T JOG_TOUCH 0
S DIRECTION REV
Q JOG_MOVE 10
C JOG_TOUCH 812
```

Zum Ausprobieren ohne Geraet:

```python
from virtual_cdj.core.input_layer import InputLayer
from virtual_cdj.sources.hardware import HardwareSource

layer = InputLayer()
layer.subscribe(print)
HardwareSource(layer).feed("D PLAY 1\nD PLAY 0\n")
```

## Naechster Schritt

1. Pins im Schaltplan festlegen.
2. Eintraege in `config/hardware_mapping.json` fuellen.
3. Transport in `HardwareSource.start()` implementieren (z. B. `pyserial`,
   dann ist `pyserial` die erste externe Abhaengigkeit des Projekts).
4. Zeilen des Geraets an `feed_line()` weitergeben - alles weitere ist
   unveraendert.
