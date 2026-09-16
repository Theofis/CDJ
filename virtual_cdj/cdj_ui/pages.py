"""Seitenverwaltung des Bildschirms.

Der Bildschirm zeigt immer genau eine Seite. Welche, sagt der
``ApplicationMode`` - nicht die Seite selbst und nicht ein Taster.

    ModeController  ->  PageHost  ->  aktive Seite

Die Performance-Seite ist die bestehende ``CdjScreen``; sie wird
unveraendert eingehaengt. Weitere Seiten kommen dazu, indem sie hier
angemeldet werden - die Performance-Oberflaeche muss dafuer nicht angefasst
werden.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from ..shell.modes import ApplicationMode, ModeController
from . import theme


class Page(tk.Frame):
    """Basis einer Bildschirmseite.

    Eine Seite zeichnet sich aus zentralem Zustand (``HardwareState``,
    ``DeckState``, Kalibrierung). Sie wertet keine Hardware aus und ruft
    keine Deck-Funktion direkt auf.
    """

    #: Modus, zu dem diese Seite gehoert.
    mode: ApplicationMode = ApplicationMode.PERFORMANCE
    #: Ueberschrift, die der Rahmen anzeigen kann.
    title: str = ""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=theme.BG)
        self.active = False

    # -- Lebenszyklus ------------------------------------------------------

    def on_enter(self) -> None:
        """Wird beim Wechsel **auf** diese Seite gerufen."""

    def on_leave(self) -> None:
        """Wird beim Wechsel **von** dieser Seite gerufen."""

    def refresh(self) -> None:
        """Bildtakt der aktiven Seite. Nur die aktive Seite wird gerufen."""


class PerformancePage(Page):
    """Die bestehende DJ-Oberflaeche als Seite.

    Ein duenner Rahmen um ``CdjScreen`` - die Performance-Oberflaeche selbst
    bleibt unveraendert. Beim Verlassen haelt nur ihre Bildschleife an; Deck
    und Audio laufen weiter, denn die takten aus der Anwendung, nicht aus
    der Anzeige. Ein Track spielt also weiter, waehrend jemand im Menue
    oder in der Pruefung ist.
    """

    mode = ApplicationMode.PERFORMANCE
    title = "PERFORMANCE"

    def __init__(self, master: tk.Misc, screen_factory) -> None:
        super().__init__(master)
        self.screen = screen_factory(self)
        self.screen.pack(fill="both", expand=True)

    def on_enter(self) -> None:
        self.screen.start()

    def on_leave(self) -> None:
        self.screen.stop()

    def refresh(self) -> None:
        # Die Bildschleife der Oberflaeche laeuft selbst; hier ist nichts
        # zusaetzlich zu tun.
        return


class PageHost(tk.Frame):
    """Haelt alle Seiten und zeigt die zum Modus passende.

    Seiten werden erst gebaut, wenn sie zum ersten Mal gebraucht werden -
    der Start der Performance-Oberflaeche wird dadurch nicht langsamer.
    """

    def __init__(self, master: tk.Misc, modes: ModeController) -> None:
        super().__init__(master, bg=theme.BG)
        self.modes = modes
        self._factories: dict[ApplicationMode, Callable[[tk.Misc], Page]] = {}
        self._pages: dict[ApplicationMode, Page] = {}
        self._current: Page | None = None
        self._unsubscribe = modes.subscribe(self._on_mode)

    # ------------------------------------------------------------------

    def register(
        self,
        mode: ApplicationMode,
        factory: Callable[[tk.Misc], Page],
    ) -> None:
        """Eine Seite fuer einen Modus anmelden."""
        self._factories[mode] = factory

    def register_page(self, mode: ApplicationMode, page: Page) -> None:
        """Eine bereits gebaute Seite anmelden (z. B. ``CdjScreen``)."""
        self._pages[mode] = page

    def page(self, mode: ApplicationMode) -> Page | None:
        """Seite eines Modus, falls es sie gibt - baut sie bei Bedarf."""
        page = self._pages.get(mode)
        if page is not None:
            return page
        factory = self._factories.get(mode)
        if factory is None:
            return None
        page = factory(self)
        self._pages[mode] = page
        return page

    @property
    def current(self) -> Page | None:
        return self._current

    def built_modes(self) -> tuple[ApplicationMode, ...]:
        return tuple(self._pages)

    # ------------------------------------------------------------------

    def show(self, mode: ApplicationMode) -> Page | None:
        """Seite eines Modus anzeigen."""
        page = self.page(mode)
        if page is None or page is self._current:
            return page
        if self._current is not None:
            self._current.active = False
            self._current.on_leave()
            self._current.pack_forget()
        self._current = page
        page.pack(fill="both", expand=True)
        page.active = True
        page.on_enter()
        page.refresh()
        return page

    def _on_mode(self, mode: ApplicationMode) -> None:
        self.show(mode)

    def refresh(self) -> None:
        """Bildtakt: nur die sichtbare Seite zeichnen."""
        if self._current is not None:
            self._current.refresh()

    def destroy(self) -> None:
        self._unsubscribe()
        super().destroy()
