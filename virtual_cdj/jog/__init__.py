"""Jog-Engine: Sensorauswertung des Jogwheels.

Zwei getrennte Bereiche, wie vorgegeben:

```
SENSOR / SCAN PROGRAMM              DJ PROGRAMM
  touch.py    kapazitiver Sensor      reader.py   neuesten Stand lesen,
  quadrature.py  Lichtschranken A/B               Differenz bilden
  scanner.py  Touch + Positionsstand
```

Dazwischen steht **kein** Ereignisstrom, sondern der aktuelle Stand:

    Touch = 0 / 1
    Jog_Position_Count

Das DJ-Programm muss deshalb nicht wissen, wie Lichtschranken oder
kapazitiver Sensor ausgewertet werden - und es kann keine Warteschlange
veralteter Bewegungen entstehen.

Aufloesung: 200 Schlitze x 4 Flanken = 800 Schritte je Umdrehung, und eine
Umdrehung entspricht einem Beat.

Siehe docs/JOG.md.
"""

from .quadrature import (
    EDGES_PER_SLOT,
    SLOTS_PER_REV,
    STEPS_PER_BEAT,
    STEPS_PER_REV,
    TRANSITIONS,
    QuadratureDecoder,
    is_valid,
    state_of,
    step_for,
)
from .reader import (
    JogMovement,
    JogReader,
    steps_to_beats,
    steps_to_revolutions,
)
from .scanner import JogScanner, JogState
from .touch import TouchSensor, TouchStats

__all__ = [
    "EDGES_PER_SLOT",
    "JogMovement",
    "JogReader",
    "JogScanner",
    "JogState",
    "QuadratureDecoder",
    "SLOTS_PER_REV",
    "STEPS_PER_BEAT",
    "STEPS_PER_REV",
    "TRANSITIONS",
    "TouchSensor",
    "TouchStats",
    "is_valid",
    "state_of",
    "step_for",
    "steps_to_beats",
    "steps_to_revolutions",
]
