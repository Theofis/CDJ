"""Virtueller CDJ - Hardware-Simulator fuer ein Eigenbau-CDJ.

Schichten:

    virtual_cdj.ui        virtuelles Bedienfeld, Debug-Ansicht, Input-Monitor
    virtual_cdj.sources   Eingabequellen (virtuell, Hardware)
    virtual_cdj.core      Komponentenliste, Input-Schicht, Controller

Der Datenweg ist immer derselbe:

    Quelle -> InputLayer -> CdjController -> (spaeter) CDJ-Funktionen

In diesem Entwicklungsschritt sind bewusst keine CDJ-Funktionen und keine
Display-GUI implementiert.
"""

__version__ = "0.1.0"
