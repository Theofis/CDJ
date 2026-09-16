"""Gemeinsame Basis aller Eingabequellen.

Eine Quelle uebersetzt eine konkrete Signalherkunft (Maus, MCP23017, ADC,
optischer Encoder) in Aufrufe der Input-Schicht. Sie kennt die CDJ-Logik
nicht.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.input_layer import InputLayer
from ..core.model import Source


class InputSource(ABC):
    """Adapter von einer Signalherkunft auf die zentrale Input-Schicht."""

    #: Wird in jedes erzeugte Ereignis eingetragen.
    source: Source = Source.VIRTUAL

    def __init__(self, input_layer: InputLayer) -> None:
        self.input_layer = input_layer

    @abstractmethod
    def start(self) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...
