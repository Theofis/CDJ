"""Start der CDJ-Bildschirmoberflaeche mit echter Audioausgabe.

    python run_cdj.py --track song.flac       Track auf Deck 1 laden
    python run_cdj.py --decks 1,2 --track a.mp3 --track b.mp3
    python run_cdj.py --fullscreen            Kiosk-Modus fuer 7-Zoll
    python run_cdj.py --debug                 Entwicklungsanzeige (F3)
    python run_cdj.py --reference bild.png    Referenzvergleich (F4)
    python run_cdj.py --no-panel              ohne virtuelles Bedienfeld
    python run_cdj.py --no-audio              ohne Ausgabegeraet

Verdrahtung siehe ``virtual_cdj/app.py``.
"""

from __future__ import annotations

import argparse
import logging
import tkinter as tk

from virtual_cdj.app import CdjApplication
from virtual_cdj.core.button_customization import ButtonCustomizationStore
from virtual_cdj.core.model import Control
from virtual_cdj.prolink import RealProLinkProvider, SimulatorProLinkProvider
from virtual_cdj.deck.commands import CommandType, command
from virtual_cdj.cdj_ui import theme
from virtual_cdj.cdj_ui.window import CdjDisplayApp
from virtual_cdj.sources.virtual import VirtualSource
from virtual_cdj.ui.panel import PanelView


class HardwarePanelWindow(tk.Toplevel):
    """Das virtuelle Bedienfeld als eigenes Fenster.

    Nutzt unveraendert die bestehende ``PanelView``. Die Bedienung erzeugt
    dieselben Kommandos, die spaeter vom ESP32 kommen.
    """

    def __init__(
        self,
        master: tk.Misc,
        source: VirtualSource,
        deck_id: int,
        customizations: ButtonCustomizationStore,
    ):
        super().__init__(master)
        self.title(f"Virtuelles Bedienfeld - Deck {deck_id}")
        self.configure(bg=theme.BG)
        self.edit_mode_var = tk.BooleanVar(value=False)
        self.panel = PanelView(
            self,
            source,
            customizations=customizations,
            on_edit=self._edit_button,
        )
        self.panel.pack(fill="both", expand=True)
        footer = tk.Frame(self, bg=theme.BG)
        footer.pack(fill="x", pady=(0, 4))
        tk.Label(
            footer,
            text="Links = druecken   Rechts = rasten   "
                 "Mausrad = Encoder/Jog/Fader   F12 = Bearbeiten",
            bg=theme.BG, fg=theme.TEXT_DIM, font=("Segoe UI", 8),
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))
        tk.Checkbutton(
            footer,
            text="Bearbeitungsmodus",
            variable=self.edit_mode_var,
            command=self._apply_edit_mode,
            bg=theme.BG,
            fg=theme.TEXT,
            selectcolor=theme.BG,
            activebackground=theme.BG,
            activeforeground=theme.TEXT,
            font=("Segoe UI", 8),
            highlightthickness=0,
            bd=0,
        ).pack(side="right", padx=6)
        self.bind("<F12>", lambda _event: self.toggle_edit_mode())
        self._after_id: str | None = None
        self._tick()

    def toggle_edit_mode(self) -> bool:
        enabled = not self.edit_mode_var.get()
        self.edit_mode_var.set(enabled)
        self._apply_edit_mode()
        return enabled

    def _apply_edit_mode(self) -> None:
        self.panel.set_edit_mode(self.edit_mode_var.get())

    def _edit_button(self, control: Control) -> None:
        from virtual_cdj.ui.button_editor import ButtonEditorDialog

        ButtonEditorDialog(self, control, self.panel.customizations)

    def _tick(self) -> None:
        self.panel.refresh()
        self._after_id = self.after(40, self._tick)

    def destroy(self) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:  # pragma: no cover
                pass
            self._after_id = None
        super().destroy()


def wire_display(
    application: CdjApplication, deck_id: int, screen
) -> None:
    """Einen Bildschirm mit der Anwendung verbinden.

    Eine Stelle fuer die ganze Verdrahtung, damit sie geprueft werden kann,
    ohne ein Fenster zu oeffnen:

    * Quellen und Trackhierarchie fuer SOURCE und BROWSE
    * Ladewuensche des Browsers
    * die Taster oberhalb des Displays und der Drehgeber. Der Bildschirm
      behandelt Anzeigebefehle selbst und gibt alles andere ans Deck weiter.
    """
    controller = application.controllers[deck_id]
    screen.set_model(controller.get_library())
    # Tag List: **ein** Dienst fuer alle Ansichten und alle Decks. Die
    # Liste haengt am Datentraeger, nicht am Bildschirm.
    screen.tag_list = application.tag_lists
    screen.browser.tag_list_writable = application.tag_lists.can_create_playlist
    screen.add_load_hook(
        controller.load_track
    )
    # Spaetere Backends duerfen unterschiedliche Libraries liefern. Die
    # Oberflaeche fragt auch dann nur den Controller und kennt weder MIDI-
    # Bridge noch USB-Scanner.
    unsubscribe_mode = application.mode_manager.subscribe(
        lambda _mode: screen.set_model(controller.get_library())
    )

    # Ein angeschlossener oder abgezogener USB-Stick aendert die Quellenliste.
    # Der Bildschirm bekommt dann dieselbe Library noch einmal gezeigt und
    # setzt dabei eine verschwundene aktive Quelle zurueck.
    def sources_changed() -> None:
        screen.set_model(controller.get_library())

    application.on_sources_changed.append(sources_changed)

    def unsubscribe_when_destroyed(event) -> None:
        if event.widget is screen:
            unsubscribe_mode()
            if sources_changed in application.on_sources_changed:
                application.on_sources_changed.remove(sources_changed)
            application.set_command_sink(deck_id, None)

    screen.bind("<Destroy>", unsubscribe_when_destroyed, add="+")
    application.set_command_sink(deck_id, screen.send_command)


def build(args: argparse.Namespace) -> CdjDisplayApp:
    if args.prolink_raw_dump:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
    deck_ids = [int(part) for part in args.decks.split(",") if part.strip()]
    if not deck_ids:
        deck_ids = [1]

    prolink_provider = None
    if args.prolink_source == "simulator":
        prolink_provider = SimulatorProLinkProvider(
            args.prolink_host, args.prolink_port
        )
    elif args.prolink_source == "real":
        prolink_provider = RealProLinkProvider(
            args.prolink_interface,
            presence=args.prolink_presence,
            presence_mac=args.prolink_mac,
            capture_path=args.prolink_capture,
            raw_dump=args.prolink_raw_dump,
        )

    application = CdjApplication(
        deck_ids,
        block_frames=args.block,
        start_audio=args.audio,
        demo=args.demo,
        usb=args.usb,
        prolink_provider=prolink_provider,
    )

    app = CdjDisplayApp()
    app.application = application  # type: ignore[attr-defined]

    for index, deck_id in enumerate(deck_ids):
        # Die Master-Sicht kommt aus der Anwendung; Simulator und echte
        # Netzwerkquelle enden oberhalb des Providers im selben Modell.
        window = app.add_display(
            application.providers[deck_id],
            master_view=(
                lambda own=deck_id: application.master_view(own)
            ),
            fullscreen=args.fullscreen,
            show_debug=args.debug,
            geometry=args.geometry,
            # Anwendungsrahmen: Modus, zentraler Hardware-Zustand und
            # Kalibrierung. Damit gibt es neben der Performance-Oberflaeche
            # auch Menue, Pruefung, Kalibrierung und Einstellungen.
            modes=application.modes,
            operating_modes=application.mode_manager,
            hardware=application.hardware,
            calibration=application.calibration,
            jog=application.jog,
            audio_status=lambda: application.audio_status_text,
        )
        wire_display(application, deck_id, window.screen)
        if args.reference:
            if not window.screen.load_reference(args.reference):
                print(f"Referenzbild nicht lesbar: {args.reference}")
        # Messwerte in die Entwicklungsanzeige haengen (Abschnitt 18).
        window.screen.debug.audio_metrics = application.audio.metrics
        window.screen.debug.analysis_metrics = application.loader.metrics
        window.screen.debug.audio_device = application.audio_status_text
        if not args.fullscreen:
            window.move_to_display(40 + index * 50, 40 + index * 50)

    if args.panel:
        panel = HardwarePanelWindow(
            app,
            application.virtual_source,
            deck_ids[0],
            application.button_customizations,
        )
        panel.geometry("+700+40")
        app.panel_window = panel  # type: ignore[attr-defined]

    # Tracks aus der Kommandozeile auf die Decks legen.
    for deck_id, path in zip(deck_ids, args.track or []):
        application.load_track(deck_id, path)

    # Im Demo-Modus ohne ausdrueckliche Tracks: Demo-Tracks verteilen und
    # Deck 1 zum Master machen, damit die gestapelte Ansicht sofort greift.
    if args.demo and not args.track:
        application.load_demo_defaults()
        first = deck_ids[0]
        application.providers[first].send(
            command(CommandType.MASTER_SET, first, "DEMO")
        )

    # Die Anwendung im GUI-Thread mitlaufen lassen: Worker-Ergebnisse
    # abholen und die Decks takten.
    def pump() -> None:
        application.tick()
        app.after(16, pump)

    app.after(16, pump)

    original_destroy = app.destroy

    def destroy() -> None:
        application.close()
        original_destroy()

    app.destroy = destroy  # type: ignore[method-assign]
    return app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CDJ-Bildschirmoberflaeche")
    parser.add_argument(
        "--decks", default="1",
        help="Deck-IDs, mit Komma getrennt (Beispiel: 1,2)",
    )
    parser.add_argument(
        "--track", action="append", default=None, metavar="DATEI",
        help="Track laden; mehrfach angebbar, der Reihenfolge der Decks nach",
    )
    parser.add_argument(
        "--fullscreen", action="store_true",
        help="Kiosk-Modus ohne Fensterrahmen (F11 umschalten, Esc beenden)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Entwicklungsanzeige einblenden (F3 umschalten)",
    )
    parser.add_argument(
        "--geometry", default=None,
        help=f"Fenstergroesse, Standard {theme.BASE_WIDTH}x{theme.BASE_HEIGHT}",
    )
    parser.add_argument(
        "--block", type=int, default=512,
        help="Audio-Blockgroesse in Frames (Standard 512 = 11.6 ms)",
    )
    parser.add_argument(
        "--no-panel", dest="panel", action="store_false",
        help="ohne virtuelles Bedienfeld starten",
    )
    parser.add_argument(
        "--no-audio", dest="audio", action="store_false",
        help="Ausgabegeraet nicht oeffnen",
    )
    parser.add_argument(
        "--reference", default=None, metavar="BILD",
        help="Referenzbild zum Vergleich laden (F4 wechselt, nur Entwicklung)",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Demo-Tracks laden (synthetische Analyse- und Audiodaten)",
    )
    parser.add_argument(
        "--no-usb", dest="usb", action="store_false",
        help="angeschlossene Datentraeger nicht einlesen",
    )
    parser.add_argument(
        "--prolink-source", choices=("none", "simulator", "real"), default="none",
        help="Quelle externer Playerdaten (Standard: none)",
    )
    parser.add_argument(
        "--prolink-host", default="127.0.0.1",
        help="Host des internen ProLink-Simulators",
    )
    parser.add_argument(
        "--prolink-port", type=int, default=17600,
        help="TCP-Port des internen ProLink-Simulators",
    )
    parser.add_argument(
        "--prolink-interface", default="auto", metavar="IP",
        help=(
            "lokale IPv4-Adresse fuer echtes PRO DJ LINK; auto bevorzugt "
            "eine eindeutige 169.254-Adresse, sonst alle Interfaces"
        ),
    )
    parser.add_argument(
        "--prolink-presence",
        choices=("passive", "peer"),
        default="passive",
        help=(
            "passive sendet nichts; peer sendet nur den belegten "
            "Observer-7-Keepalive fuer Status-Unicasts"
        ),
    )
    parser.add_argument(
        "--prolink-mac",
        default=None,
        metavar="MAC",
        help="MAC des mit --prolink-interface gewaehlten NIC (nur peer-Fallback)",
    )
    parser.add_argument(
        "--prolink-capture",
        default=None,
        metavar="JSONL",
        help="empfangene Rohpakete im replay-faehigen JSONL-Journal speichern",
    )
    parser.add_argument(
        "--prolink-raw-dump",
        action="store_true",
        help="vollstaendigen Paket-Hexdump im Debug-Log ausgeben",
    )
    parser.set_defaults(panel=True, audio=True, usb=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    build(parse_args(argv)).mainloop()


if __name__ == "__main__":
    main()
