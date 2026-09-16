"""Fenster fuer ein CDJ-Display.

Ein Fenster gehoert zu genau einer Deck-ID. Es gibt keine Annahme, dass ein
Fenster immer Deck 1 ist - die ID kommt vom uebergebenen Provider.

Das Fenster zeigt immer die Seite zum aktuellen ``ApplicationMode``:
Performance, Menue, Pruefung, Kalibrierung oder Einstellungen. Welche das
ist, entscheidet der ``ModeController``, nicht das Fenster - deshalb kann
eine weitere Seite ergaenzt werden, ohne die Performance-Oberflaeche
anzufassen. Ohne uebergebenen ``ModeController`` gibt es nur die
Performance-Seite; dann verhaelt sich das Fenster wie bisher.

Fuer den 7-Zoll-Bildschirm gibt es einen echten Kiosk-Modus ohne
Fensterrahmen. Waehrend der Entwicklung bleibt der Fenstermodus verfuegbar.
"""

from __future__ import annotations

import tkinter as tk

from ..deck.commands import CommandType, Views, command
from ..deck.mode_manager import ModeManager
from ..deck.provider import DeckStateProvider
from ..shell.calibration import CalibrationStore
from ..shell.hardware_state import HardwareState
from ..shell.modes import ApplicationMode, ModeController
from . import theme
from .pages import PageHost, PerformancePage
from .screen import CdjScreen

#: Bildtakt der Seiten ausserhalb der Performance-Oberflaeche. Die hat ihre
#: eigene Schleife mit 60 Bildern je Sekunde; fuer Messwerte und Listen
#: reichen 25.
PAGE_INTERVAL_MS = 40


class CdjDisplayWindow(tk.Toplevel):
    """Ein CDJ-Display als eigenes Fenster."""

    def __init__(
        self,
        master: tk.Misc,
        provider: DeckStateProvider,
        *,
        master_provider: DeckStateProvider | None = None,
        master_view=None,
        fullscreen: bool = False,
        show_debug: bool = False,
        geometry: str | None = None,
        modes: ModeController | None = None,
        operating_modes: ModeManager | None = None,
        hardware: HardwareState | None = None,
        calibration: CalibrationStore | None = None,
        jog=None,
        audio_status=None,
    ) -> None:
        super().__init__(master)
        self.provider = provider
        self.deck_id = provider.deck_id
        self.title(f"CDJ {self.deck_id}")
        self.configure(bg=theme.BG)
        self.minsize(800, 480)

        self.modes = modes if modes is not None else ModeController()
        self.operating_modes = operating_modes
        self.hardware = hardware
        self.calibration = calibration
        self.jog = jog
        self.audio_status = audio_status

        self.host = PageHost(self, self.modes)
        self.host.pack(fill="both", expand=True)
        self.performance = PerformancePage(
            self.host,
            lambda parent: CdjScreen(
                parent, provider,
                master_provider=master_provider,
                master_view=master_view,
            ),
        )
        #: Die Performance-Oberflaeche. Bleibt der Weg fuer alles, was mit
        #: dem Deck zu tun hat - die uebrigen Seiten haengen daneben.
        self.screen = self.performance.screen
        self.host.register_page(ApplicationMode.PERFORMANCE, self.performance)
        self._register_pages()

        self.geometry(geometry or f"{theme.BASE_WIDTH}x{theme.BASE_HEIGHT}")
        self._fullscreen = False
        if fullscreen:
            self.set_fullscreen(True)
        if show_debug:
            self.screen.toggle_debug()

        self.bind("<F11>", lambda e: self.toggle_fullscreen())
        self.bind("<Escape>", lambda e: self._on_escape())
        self.bind("<F3>", lambda e: self.screen.toggle_debug())
        self.bind("<F4>", lambda e: self.screen.toggle_reference())
        self.bind("<F1>", lambda e: self.toggle_menu())
        self.bind("<F2>", lambda e: self.set_mode(ApplicationMode.TEST))
        self._bind_hardware_keys()
        self._bind_menu_keys()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._page_after: str | None = None
        self.host.show(self.modes.mode)
        self._page_frame()

    # ------------------------------------------------------------------
    # Seiten
    # ------------------------------------------------------------------

    def _register_pages(self) -> None:
        """Die uebrigen Seiten anmelden - gebaut werden sie bei Bedarf.

        Ohne zentralen Hardware-Zustand gibt es Pruefung und Kalibrierung
        nicht: beide leben von echten Messwerten, und eine Seite, die keine
        hat, waere eine Attrappe.
        """
        from .menu_page import MenuPage
        from .settings_page import SettingsPage

        self.host.register(
            ApplicationMode.MENU,
            lambda parent: MenuPage(
                parent, self.modes, operating_modes=self.operating_modes
            ),
        )
        if self.calibration is not None:
            self.host.register(
                ApplicationMode.SETTINGS,
                lambda parent: SettingsPage(
                    parent,
                    display=lambda: self.screen.display,
                    send=self.screen.send_command,
                    deck_id=self.deck_id,
                    store=self.calibration,
                    audio_status=self.audio_status,
                    modes=self.modes,
                ),
            )
        if self.hardware is None:
            return

        from .calibration_page import CalibrationPage
        from .test_page import TestPage

        self.host.register(
            ApplicationMode.TEST,
            lambda parent: TestPage(parent, self.hardware, self.modes),
        )
        if self.calibration is not None:
            self.host.register(
                ApplicationMode.CALIBRATION,
                lambda parent: CalibrationPage(
                    parent, self.hardware, self.calibration,
                    jog=self.jog, modes=self.modes,
                ),
            )

    def set_mode(self, mode: ApplicationMode) -> ApplicationMode:
        """Modus wechseln. Fehlt die Seite, bleibt es beim aktuellen Modus."""
        if self.host.page(mode) is None:
            return self.modes.mode
        return self.modes.set_mode(mode)

    def toggle_menu(self) -> ApplicationMode:
        if self.modes.mode is ApplicationMode.MENU:
            page = self.host.current
            if page is not None and getattr(page, "back", lambda: False)():
                return self.modes.mode
        return self.modes.toggle_menu()

    def _on_escape(self) -> None:
        """Esc: erst zurueck aus einer Seite, sonst aus dem Kiosk-Modus."""
        if self.modes.mode is not ApplicationMode.PERFORMANCE:
            page = self.host.current
            if (
                self.modes.mode is ApplicationMode.MENU
                and page is not None
                and getattr(page, "back", lambda: False)()
            ):
                return
            self.modes.to_performance()
            return
        self.set_fullscreen(False)

    def _bind_menu_keys(self) -> None:
        """Tastatur fuer das Menue - dieselben Schritte wie am Drehregler."""

        def in_menu() -> bool:
            return self.modes.mode is ApplicationMode.MENU

        def move(delta: int) -> None:
            page = self.host.current
            if in_menu() and page is not None:
                page.move(delta)

        def activate() -> None:
            page = self.host.current
            if in_menu() and page is not None:
                page.activate()

        self.bind("<Prior>", lambda e: move(-1), add="+")
        self.bind("<Next>", lambda e: move(+1), add="+")
        self.bind("<KP_Enter>", lambda e: activate(), add="+")

    def _page_frame(self) -> None:
        """Bildtakt der Seiten ausserhalb der Performance-Oberflaeche."""
        if self.modes.mode is not ApplicationMode.PERFORMANCE:
            self.host.refresh()
        self._page_after = self.after(PAGE_INTERVAL_MS, self._page_frame)

    # ------------------------------------------------------------------

    def _bind_hardware_keys(self) -> None:
        """Tastatur als Ersatz fuer die Taster oberhalb des Displays.

        Es werden genau dieselben Kommandos gesendet wie vom Bedienfeld -
        die Tastatur ist eine weitere Eingabequelle, keine zweite Logik.
        Ohne virtuelles Bedienfeld (``--no-panel``) bleibt der Bildschirm so
        bedienbar.
        """
        screen = self.screen
        deck_id = self.deck_id

        def send(command_type: CommandType, **params) -> None:
            screen.send_command(
                command(command_type, deck_id, "KEYBOARD", **params)
            )

        def press_release(command_type: CommandType, *, hold: bool) -> None:
            """Druecken und Loslassen - fuer kurz/lang unterscheidbare Taster."""
            send(command_type, pressed=True)
            if hold:
                screen.force_long_press(command_type)
            send(command_type, pressed=False)

        self.bind(
            "<F5>", lambda e: send(CommandType.VIEW, view=Views.SOURCE)
        )
        self.bind(
            "<F6>", lambda e: send(CommandType.VIEW, view=Views.BROWSE)
        )
        self.bind(
            "<F7>",
            lambda e: send(
                CommandType.VIEW, view=Views.BROWSE, path=("HISTORY",)
            ),
        )
        self.bind(
            "<F8>", lambda e: send(CommandType.VIEW, view=Views.BROWSE)
        )
        self.bind("<Up>", lambda e: send(CommandType.BROWSE_ROTATE, delta=-1))
        self.bind("<Down>", lambda e: send(CommandType.BROWSE_ROTATE, delta=+1))
        self.bind(
            "<Return>",
            lambda e: press_release(CommandType.BROWSE_PRESS, hold=False),
        )
        self.bind(
            "<Shift-Return>",
            lambda e: press_release(CommandType.BROWSE_PRESS, hold=True),
        )
        self.bind(
            "<BackSpace>",
            lambda e: press_release(CommandType.BACK, hold=False),
        )
        self.bind(
            "<Shift-BackSpace>",
            lambda e: press_release(CommandType.BACK, hold=True),
        )

        # Hot Cues A bis H. Am Geraet sind das Tasten unter dem Display -
        # auf dem Schirm gibt es sie nicht, also braucht die Bedienung ohne
        # Bedienfeld einen Weg dorthin.
        for index in range(8):
            self.bind(
                f"<Key-{index + 1}>",
                lambda e, i=index: (
                    send(CommandType.PAD, index=i, pressed=True),
                    send(CommandType.PAD, index=i, pressed=False),
                ),
            )

    # ------------------------------------------------------------------

    @property
    def fullscreen(self) -> bool:
        return self._fullscreen

    def set_fullscreen(self, enabled: bool) -> None:
        """Kiosk-Modus: kein Rahmen, keine Titelleiste, kein Taskleisteneintrag."""
        self._fullscreen = enabled
        self.attributes("-fullscreen", enabled)
        self.overrideredirect(enabled)
        if enabled:
            self.lift()
            self.focus_force()

    def toggle_fullscreen(self) -> bool:
        self.set_fullscreen(not self._fullscreen)
        return self._fullscreen

    def move_to_display(self, x: int, y: int) -> None:
        """Fenster auf einen bestimmten Bildschirm schieben."""
        self.geometry(f"+{x}+{y}")

    def destroy(self) -> None:
        if self._page_after is not None:
            try:
                self.after_cancel(self._page_after)
            except Exception:  # pragma: no cover - Fenster bereits zu
                pass
            self._page_after = None
        self.screen.stop()
        super().destroy()


class CdjDisplayApp(tk.Tk):
    """Eigenstaendige Anwendung fuer ein oder mehrere CDJ-Displays.

    Das Wurzelfenster bleibt unsichtbar; jedes Display ist ein eigenes
    Fenster mit eigener Deck-ID.
    """

    def __init__(self) -> None:
        super().__init__()
        self.withdraw()
        self.windows: list[CdjDisplayWindow] = []

    def add_display(
        self,
        provider: DeckStateProvider,
        *,
        master_provider: DeckStateProvider | None = None,
        master_view=None,
        fullscreen: bool = False,
        show_debug: bool = False,
        geometry: str | None = None,
        modes: ModeController | None = None,
        operating_modes: ModeManager | None = None,
        hardware: HardwareState | None = None,
        calibration: CalibrationStore | None = None,
        jog=None,
        audio_status=None,
    ) -> CdjDisplayWindow:
        window = CdjDisplayWindow(
            self, provider,
            master_provider=master_provider,
            master_view=master_view,
            fullscreen=fullscreen,
            show_debug=show_debug,
            geometry=geometry,
            modes=modes,
            operating_modes=operating_modes,
            hardware=hardware,
            calibration=calibration,
            jog=jog,
            audio_status=audio_status,
        )
        self.windows.append(window)
        window.bind("<Destroy>", self._on_window_destroy, add="+")
        return window

    def _on_window_destroy(self, event: tk.Event) -> None:
        if event.widget in self.windows:
            self.windows.remove(event.widget)
            if not self.windows:
                self.quit()
