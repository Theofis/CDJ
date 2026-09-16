"""Anwendungsrahmen: Modus, zentraler Hardware-Zustand, Kalibrierung.

Diese Schicht liegt zwischen Hardware und Oberflaeche:

```
Hardware
   |
Sensor / Input Engine        core/input_layer.py, jog/
   |
HardwareState                shell/hardware_state.py
   |
Application Logic            shell/modes.py, shell/router.py, deck/
   |
GUI                          cdj_ui/, ui/
```

Sie kennt kein Tkinter. Jede Seite der Oberflaeche liest denselben
``HardwareState`` und denselben ``ApplicationMode``; keine Seite wertet
Hardware selbst aus.
"""

from .calibration import (
    AnalogCalibration,
    CalibrationStore,
    JogCalibration,
    TouchCalibration,
    analog_control_ids,
)
from .hardware_state import (
    AnalogReading,
    HardwareState,
    JogReading,
    LinkStatus,
)
from .modes import (
    MENU_ENTRIES,
    ApplicationMode,
    MenuEntry,
    ModeController,
)
from .router import InputRouter
from ..deck.mode_manager import ModeManager, OperatingMode

__all__ = [
    "MENU_ENTRIES",
    "AnalogCalibration",
    "AnalogReading",
    "ApplicationMode",
    "CalibrationStore",
    "HardwareState",
    "InputRouter",
    "JogCalibration",
    "JogReading",
    "LinkStatus",
    "MenuEntry",
    "ModeController",
    "ModeManager",
    "OperatingMode",
    "TouchCalibration",
    "analog_control_ids",
]
