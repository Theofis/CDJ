"""Normalisierte PRO-DJ-LINK-Grenze und austauschbare Datenquellen.

Simulator, strikt passiver Real-Adapter und Nullquelle liefern dieselben
internen Zustände. Master-/Sync-Logik und GUI kennen ihre Herkunft nicht.
"""

from .models import BeatEvent, PlayerState, RemotePlayer, TrackAnalysis, TrackMetadata
from .provider import NullProLinkProvider, ProLinkProvider
from .real_provider import RawPacketDiagnostic, RealProLinkProvider
from .simulator_provider import SimulatorProLinkProvider

__all__ = [
    "BeatEvent",
    "NullProLinkProvider",
    "PlayerState",
    "ProLinkProvider",
    "RawPacketDiagnostic",
    "RealProLinkProvider",
    "RemotePlayer",
    "SimulatorProLinkProvider",
    "TrackAnalysis",
    "TrackMetadata",
]
