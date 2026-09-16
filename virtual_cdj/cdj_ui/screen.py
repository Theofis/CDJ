"""``CdjScreen`` - die CDJ-Bildschirmoberflaeche eines Decks.

Der Bildschirm haelt einen ``CdjDisplayState``. Der enthaelt eine Referenz auf
den ``DeckState`` des lokalen Decks, eine ``MasterDeckView`` und die reinen
Anzeigezustaende (Zeitmodus, Zoom, Wellenformfarbe, offenes Panel).

Der Bildschirm rechnet nichts nach, was im Deck-Zustand steht. Bedienung geht
ausschliesslich als ``DeckCommand`` an den ``DeckStateProvider``; reine
Anzeigebefehle behandelt er selbst.

Er weiss nicht, ob das Deck oder der Master auf diesem Rechner laufen.
"""

from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Callable

from ..deck.commands import CommandType, DeckCommand, Views, command
from ..deck.display_state import (
    BrowseContext,
    CdjDisplayState,
    JumpMode,
    MasterDeckView,
    RotaryMode,
    TagMenuAction,
    TouchPanel,
    WaveformMode,
)
from ..deck.library import (
    TAG_LIST_CATEGORY,
    BrowseColumn,
    BrowseNode,
    EntryKind,
    MediaLibrary,
)
from ..deck.provider import DeckStateProvider
from ..deck.state import DeckState
from . import theme
from .base import Region
from .browser import CdjBrowser
from .debug_overlay import CdjDebugOverlay
from .master_waveform import CdjMasterWaveform
from .overview_waveform import CdjOverviewWaveform
from .panels import PANEL_CLASSES, Panel
from .reference_view import ReferenceView
from .scrolling_waveform import CdjScrollingWaveform
from .source_screen import CdjSourceScreen
from .status_bar import CdjStatusBar
from .theme import Metrics
from .top_bar import CdjTopBar
from .track_info_popup import CdjTrackInfoPopup
from .waveform_header import CdjWaveformHeader

#: Bildwiederholung. 16 ms entspricht rund 60 FPS.
FRAME_INTERVAL_MS = 16

#: Ab dieser Haltedauer gilt ein Tastendruck als "gedrueckt gehalten"
#: (Handbuch S. 22, 25, 38).
LONG_PRESS_S = 0.5

#: Schritt des Drehreglers im Rastereinstellungsmodus (S. 72). Am Geraet
#: verschiebt eine Rastung das Beatgrid um wenige Millisekunden; 10 ms sind
#: auf einem Mausrad brauchbar und bleiben fein genug.
GRID_STEP_S = 0.010

#: Zeilen je Seitensprung (S. 38).
PAGE_JUMP_ROWS = 10

#: Anzeigebefehle, die der Bildschirm selbst behandelt und **nicht** an die
#: Deck-Engine schickt - sie aendern keinen Deck-Zustand.
DISPLAY_COMMANDS = frozenset({
    CommandType.VIEW,
    CommandType.PANEL,
    CommandType.TIME_MODE,
    CommandType.WAVEFORM_ZOOM,
    CommandType.WAVEFORM_MODE,
    CommandType.HEADER_TOGGLE,
    CommandType.NEEDLE_LOCK,
    CommandType.NAV_SELECT,
    CommandType.NAV_ENTER,
    CommandType.NAV_TOP,
    CommandType.BROWSE_SORT,
    CommandType.BROWSE_TOGGLE,
    CommandType.JUMP_MODE,
    CommandType.ROTARY_MODE,
    CommandType.MENU,
    CommandType.TAG_TRACK_TOGGLE,
    CommandType.TAG_MENU_ACTION,
})

#: Wie lange eine Rueckmeldung ueber der Liste stehen bleibt.
NOTICE_S = 2.5


class CdjScreen(tk.Frame):
    """Vollstaendige Bildschirmoberflaeche eines CDJ."""

    def __init__(
        self,
        master: tk.Misc,
        provider: DeckStateProvider,
        *,
        master_provider: DeckStateProvider | None = None,
        master_view: Callable[[], MasterDeckView | None] | None = None,
        show_debug: bool = False,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(master, bg=theme.BG)
        self.provider = provider
        self.deck_id = provider.deck_id
        self._time = time_source
        #: Quelle der Master-Sicht. Entweder ein lokaler Provider oder eine
        #: Funktion - letztere kann spaeter Netzwerkdaten liefern.
        self.master_provider = master_provider
        self.master_view_source = master_view

        self.metrics = Metrics()
        self.view = Views.WAVEFORM
        self.display = CdjDisplayState(deck=provider.get_state())
        self._running = False
        self._after_id: str | None = None
        self._command_hooks: list[Callable[[DeckCommand], None]] = []
        self._load_hooks: list[Callable[[str], None]] = []
        #: Zuletzt angewandte Grid-Belegung des Inhaltsbereichs.
        self._content_layout: dict = {}
        #: Inhalt fuer SOURCE und BROWSE. Ohne Modell bleiben beide leer.
        self.model: MediaLibrary | None = None
        #: Laufende Tastendruecke: Kommandotyp -> Startzeit. Damit werden
        #: kurzes und langes Druecken unterschieden (S. 22, 25, 38).
        self._holds: dict[CommandType, float] = {}
        self._holds_fired: set[CommandType] = set()
        #: Tag List des aktiven Datentraegers. Wird von der Verdrahtung
        #: gesetzt; ohne sie bleibt TAG TRACK wirkungslos statt zu raten.
        self.tag_list = None
        #: Wann die aktuelle Rueckmeldung eingeblendet wurde.
        self._notice_at = 0.0

        self._build()
        self._unsubscribe = provider.subscribe(self._on_state)
        self.bind("<Configure>", self._on_configure)

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _build(self) -> None:
        send = self.send_command
        deck_id = self.deck_id

        self.top_bar = CdjTopBar(self, send, deck_id)

        self.content = tk.Frame(self, bg=theme.BG)
        self.content.grid_columnconfigure(0, weight=1)
        # Groessenweitergabe abschalten: der Inhaltsbereich bekommt seine
        # Groesse vom aeusseren Grid. Mit Propagation wuerde er sie von den
        # Kindern ableiten, und beim Wechsel der Ansicht bliebe der innere
        # Grid auf der alten Rechnung stehen - die Bereiche waeren 1 px hoch.
        self.content.grid_propagate(False)

        self.waveform_header = CdjWaveformHeader(self.content, send, deck_id)
        self.master_wave = CdjMasterWaveform(self.content, send, deck_id)
        self.scrolling = CdjScrollingWaveform(self.content, send, deck_id)
        self.browser = CdjBrowser(self.content, send, deck_id)
        self.source_screen = CdjSourceScreen(self.content, send, deck_id)

        # Panels liegen als Overlay ueber dem unteren Teil der Wellenform.
        self.panels: dict[TouchPanel, Panel] = {
            panel: cls(self, send, deck_id)
            for panel, cls in PANEL_CLASSES.items()
        }
        self.track_info_popup = CdjTrackInfoPopup(self, send, deck_id)

        # Untere Zeilen = Wiedergabestatusanzeige (Handbuch S. 21-23).
        # Eine Pad-Reihe gibt es am Geraet nicht: die Hot Cues sind Tasten
        # unter dem Display, auf dem Schirm erscheinen sie als Marken auf
        # beiden Wellenformen.
        self.status_bar = CdjStatusBar(self, send, deck_id)
        self.overview = CdjOverviewWaveform(self, send, deck_id)
        self.debug = CdjDebugOverlay(self)
        # Entwicklungswerkzeug, standardmaessig nicht vorhanden.
        self.reference: ReferenceView | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        m = self.metrics
        # Zeile 0 bleibt frei: am Geraet gibt es ueber der Kopfzeile keine
        # Reiterleiste, die Ansichten schalten die Taster des Bedienfelds.
        self.top_bar.configure(height=m.px(theme.H_TOP_BAR))
        self.content.grid(row=2, column=0, sticky="nsew")
        self.status_bar.grid(row=3, column=0, sticky="ew")
        self.status_bar.configure(height=m.px(theme.H_STATUS_BAR))
        self.overview.grid(row=4, column=0, sticky="ew")
        self.overview.configure(height=m.px(theme.H_OVERVIEW))

        self._layout_content()

    def _layout_content(self) -> None:
        """Inhalt je nach Ansicht und Master-Zustand einhaengen.

        Bewusst **idempotent**: nur was sich aendert, wird neu gegriddet.
        Ein bedingungsloses ``grid_forget`` + ``grid`` bei jedem
        ``<Configure>`` liesse die Geometrie dauerhaft ausstehend und die
        Bereiche blieben 1 px hoch.
        """
        m = self.metrics

        # Gewuenschte Belegung: Widget -> (Zeile, minsize, weight)
        desired: dict[Region, tuple[int, int, int]] = {}
        if self.view is Views.BROWSE:
            desired[self.browser] = (0, 0, 1)
        elif self.view is Views.SOURCE:
            desired[self.source_screen] = (0, 0, 1)
        else:
            row = 0
            desired[self.waveform_header] = (
                row, m.px(theme.H_WAVE_HEADER), 0
            )
            row += 1
            if self.master_wave.should_show(self.display.deck):
                desired[self.master_wave] = (
                    row, m.px(theme.H_MASTER_WAVE), 0
                )
                row += 1
            desired[self.scrolling] = (row, 0, 1)

        # Kopfzeile mit Track und Panel-Tasten gibt es nur auf dem
        # Wiedergabebildschirm (S. 21); SOURCE und BROWSE haben eigene
        # Kopfzeilen (S. 18, 19).
        if self.view is Views.WAVEFORM:
            self.top_bar.grid(row=1, column=0, sticky="ew")
        else:
            self.top_bar.grid_forget()

        if desired == self._content_layout:
            return
        self._content_layout = desired

        for widget in (
            self.waveform_header, self.master_wave,
            self.scrolling, self.browser, self.source_screen,
        ):
            if widget not in desired:
                widget.grid_forget()
        for row in range(4):
            self.content.grid_rowconfigure(row, weight=0, minsize=0)
        for widget, (row, minsize, weight) in desired.items():
            widget.grid(row=row, column=0, sticky="nsew")
            self.content.grid_rowconfigure(
                row, minsize=minsize, weight=weight
            )

    def _place_panels(self) -> None:
        """Offenes Panel ueber den unteren Teil der Wellenform legen."""
        m = self.metrics
        active = self.display.panel
        # Panel-Oberkante aus den Layouthoehen rechnen statt aus
        # ``winfo_rooty`` - das ist vor dem ersten Mapping noch nicht gesetzt.
        height = m.px(theme.H_PANEL)
        bottom_rows = theme.H_STATUS_BAR + theme.H_OVERVIEW
        panel_y = self.metrics.height - m.px(bottom_rows) - height
        for panel, widget in self.panels.items():
            if panel is active and self.view is Views.WAVEFORM:
                widget.place(
                    x=m.px(4),
                    y=max(m.px(theme.H_TOUCH_NAV + theme.H_TOP_BAR), panel_y),
                    width=self.metrics.width - m.px(8),
                    height=height,
                )
                tk.Misc.lift(widget)
            else:
                widget.place_forget()

        if active is TouchPanel.TRACK_INFO and self.view is Views.WAVEFORM:
            width = m.px(360)
            self.track_info_popup.place(
                x=m.px(8),
                y=m.px(theme.H_TOUCH_NAV + theme.H_TOP_BAR + 4),
                width=width,
                height=m.px(150),
            )
            tk.Misc.lift(self.track_info_popup)
        else:
            self.track_info_popup.place_forget()

    # ------------------------------------------------------------------
    # Groesse
    # ------------------------------------------------------------------

    def _on_configure(self, event: tk.Event) -> None:
        metrics = Metrics(width=event.width, height=event.height)
        if (metrics.width, metrics.height) == (
            self.metrics.width, self.metrics.height
        ):
            return
        self.metrics = metrics
        for region in self.regions():
            region.apply_metrics(metrics)
        for panel in self.panels.values():
            panel.apply_metrics(metrics)
        self.track_info_popup.apply_metrics(metrics)
        self.debug.apply_metrics(metrics)

        self.top_bar.configure(height=metrics.px(theme.H_TOP_BAR))
        self.status_bar.configure(height=metrics.px(theme.H_STATUS_BAR))
        self.overview.configure(height=metrics.px(theme.H_OVERVIEW))
        self._layout_content()
        self._place_panels()
        self._place_debug()

    def regions(self):
        return (
            self.top_bar, self.waveform_header,
            self.master_wave, self.scrolling, self.browser,
            self.source_screen,
            self.status_bar, self.overview,
        )

    def _place_debug(self) -> None:
        m = self.metrics
        width = m.px(320)
        height = m.px(520)
        self.debug.place(
            x=self.metrics.width - width - m.px(8),
            y=m.px(theme.H_TOUCH_NAV) + m.px(8),
            width=width, height=height,
        )
        if not self.debug.visible:
            self.debug.place_forget()

    # ------------------------------------------------------------------
    # Kommandos
    # ------------------------------------------------------------------

    def add_command_hook(self, hook: Callable[[DeckCommand], None]) -> None:
        self._command_hooks.append(hook)

    def send_command(self, cmd: DeckCommand) -> None:
        """Einziger Weg der Oberflaeche, etwas auszuloesen."""
        for hook in self._command_hooks:
            hook(cmd)
        self.debug.note_command(cmd)

        if cmd.type in DISPLAY_COMMANDS:
            self._handle_display_command(cmd)
            return

        # Drehgeber und BACK: kurz und lang unterscheiden (S. 22, 25, 38).
        if cmd.type in (CommandType.BROWSE_PRESS, CommandType.BACK):
            self._handle_hold(cmd)
            return

        if cmd.type is CommandType.BROWSE_ROTATE:
            self._rotate(int(cmd.get("delta", 0)))
            return

        if cmd.type is CommandType.LOAD:
            # Laden macht die Anwendung; die Oberflaeche meldet nur den Wunsch.
            for hook in self._load_hooks:
                hook(str(cmd.get("track_id", "")))
            self.set_view(Views.WAVEFORM)
            return

        self.provider.send(cmd)

    def add_load_hook(self, hook: Callable[[str], None]) -> None:
        """Wird gerufen, wenn der Browser einen Track laden soll."""
        self._load_hooks.append(hook)

    # ------------------------------------------------------------------
    # Inhalt fuer SOURCE und BROWSE
    # ------------------------------------------------------------------

    def set_model(self, model: MediaLibrary | None) -> None:
        """Quellen und Trackhierarchie einsetzen."""
        self.model = model
        self.browser.set_model(model)
        self.source_screen.set_sources(
            (lambda: model.sources()) if model is not None else None
        )
        if model is not None:
            # Die aktive Quelle kann inzwischen verschwunden sein - ein
            # abgezogener USB-Stick. Dann auf die naechste vorhandene
            # umschalten, statt weiter auf eine Quelle zu zeigen, die es
            # nicht mehr gibt (der Browser zeigte sonst dauerhaft leer).
            active = self.display.source_id
            if not active or model.library(active) is None:
                self.display = self.display.with_source(
                    model.default_source_id()
                )
        self.refresh(force=True)

    def set_track_source(self, source) -> None:
        """Flache Trackliste als einzige Quelle - Kurzform von ``set_model``."""
        from ..deck.library import SourceInfo, SourceKind, TrackListLibrary

        if source is None:
            self.set_model(None)
            return
        model = MediaLibrary()
        model.add(
            TrackListLibrary(
                SourceInfo(
                    source_id="TRACKS", name="TRACKS",
                    kind=SourceKind.FILE, has_library=False,
                ),
                source,
            )
        )
        self.set_model(model)

    def node(self) -> BrowseNode:
        """Aktuelle Browse-Ebene."""
        return self.browser.node()

    # ------------------------------------------------------------------
    # Drehgeber und Taster
    # ------------------------------------------------------------------

    def _handle_hold(self, cmd: DeckCommand) -> None:
        """Druecken merken, beim Loslassen die kurze Aktion ausloesen."""
        if cmd.pressed:
            self._holds[cmd.type] = self._time()
            self._holds_fired.discard(cmd.type)
            return
        started = self._holds.pop(cmd.type, None)
        fired = cmd.type in self._holds_fired
        self._holds_fired.discard(cmd.type)
        if started is None or fired:
            return
        if self._time() - started >= LONG_PRESS_S:
            self._long_press(cmd.type)
            return
        self._short_press(cmd.type)

    def poll_holds(self) -> None:
        """Gehaltene Taster pruefen. Wird aus der Bildschleife gerufen.

        Die lange Aktion greift **waehrend** des Haltens, wie am Geraet -
        nicht erst beim Loslassen.
        """
        if not self._holds:
            return
        now = self._time()
        for command_type, started in list(self._holds.items()):
            if command_type in self._holds_fired:
                continue
            if now - started >= LONG_PRESS_S:
                self._holds_fired.add(command_type)
                self._long_press(command_type)

    def force_long_press(self, command_type: CommandType) -> None:
        """Einen laufenden Tastendruck sofort als "gehalten" werten.

        Die Tastatur kann kein echtes Halten abbilden; damit bleibt die
        lange Aktion trotzdem ausloesbar.
        """
        if command_type in self._holds:
            self._holds[command_type] = self._time() - LONG_PRESS_S
            self.poll_holds()

    def _short_press(self, command_type: CommandType) -> None:
        if command_type is CommandType.BROWSE_PRESS:
            self._confirm()
        elif command_type is CommandType.BACK:
            self._back()

    def _long_press(self, command_type: CommandType) -> None:
        if command_type is CommandType.BROWSE_PRESS:
            if self.view is Views.BROWSE and self._in_tag_list():
                # In der Tag List entfernt langes Druecken den markierten
                # Track (S. 41). Der Alphabet-Sprung waere hier ohnehin
                # kaum nuetzlich: die Liste ist hoechstens 100 Eintraege
                # lang und vom DJ selbst zusammengestellt. In allen anderen
                # Listen bleibt das lange Druecken der Sprungmodus.
                self._remove_marked_tag_track()
                return
            if self.view is Views.BROWSE:
                self._toggle_jump_mode()
            elif self.view is Views.WAVEFORM:
                # Zoom- <-> Rastereinstellungsmodus (S. 22, Element 11)
                self.display = self.display.with_rotary_toggled()
                self.refresh(force=True)
        elif command_type is CommandType.BACK:
            # Oberste Hierarchieebene hervorheben (S. 25)
            if self.view is Views.BROWSE:
                self.display = self.display.with_browse(
                    self.display.browse.to_top()
                )
                self.refresh(force=True)
            else:
                self._back()

    # ------------------------------------------------------------------

    def _rotate(self, delta: int) -> None:
        """Drehregler drehen - Wirkung je nach Ansicht (S. 22, 24, 38)."""
        if delta == 0:
            return
        if self.display.tag_menu is not None:
            # Ein offenes Menue faengt den Regler ab - sonst wanderte im
            # Hintergrund die Markierung der Liste mit.
            self.display = self.display.with_tag_menu_moved(delta)
            self.refresh(force=True)
            return
        if self.view is Views.SOURCE:
            count = len(self.source_screen.source_list())
            if count:
                index = max(
                    0, min(self.display.source_index + delta, count - 1)
                )
                self.display = self.display.with_source_index(index)
            self.refresh(force=True)
            return

        if self.view is Views.BROWSE:
            node = self.node()
            browse = self.display.browse
            entries = self._level_entries(node)
            if browse.jump is JumpMode.OFF:
                browse = browse.moved(delta, len(entries))
            else:
                browse = self._jump(browse, entries, delta)
            self.display = self.display.with_browse(browse)
            self.refresh(force=True)
            return

        # Wellenform: Zoom oder Beatgrid
        if self.display.rotary is RotaryMode.GRID:
            self.provider.send(
                command(
                    CommandType.BEATGRID_SHIFT, self.deck_id, "ROTARY",
                    delta_s=delta * GRID_STEP_S,
                )
            )
        else:
            self.display = self.display.with_zoom(delta)
        self.refresh(force=True)

    def _level_entries(self, node: BrowseNode):
        """Eintraege, auf denen der Cursor gerade steht.

        Auf der Wurzelebene sind das die Kategorien in der linken Spalte,
        darunter die Zeilen der Liste.
        """
        return node.entries

    def _jump(self, browse, entries, delta: int):
        """Alphabet- oder Seitensprung (S. 38)."""
        count = len(entries)
        if count == 0:
            return browse
        if browse.jump is JumpMode.PAGE:
            return browse.moved(delta * PAGE_JUMP_ROWS, count)
        index = max(0, min(browse.selected, count - 1))
        current = entries[index].initial
        step = 1 if delta > 0 else -1
        target = index
        while 0 <= target + step < count:
            target += step
            if entries[target].initial != current:
                break
        return browse.moved(target - browse.selected, count)

    def _toggle_jump_mode(self) -> None:
        """Sprungmodus einschalten - Alphabet bei sortierten Listen (S. 38)."""
        browse = self.display.browse
        if browse.jump is not JumpMode.OFF:
            self.display = self.display.with_browse(
                browse.with_jump(JumpMode.OFF)
            )
        else:
            node = self.node()
            mode = (
                JumpMode.ALPHABET
                if node.sort_column.is_alphabetical or not node.is_track_list
                else JumpMode.PAGE
            )
            self.display = self.display.with_browse(browse.with_jump(mode))
        self.refresh(force=True)

    # ------------------------------------------------------------------

    def _confirm(self) -> None:
        """Drehregler druecken bzw. zweite Beruehrung (S. 24, 25)."""
        if self.display.tag_menu is not None:
            self._run_tag_menu_action(self.display.tag_menu)
            return
        if self.view is Views.SOURCE:
            self._confirm_source()
            return
        if self.view is Views.BROWSE:
            self._confirm_browse()
            return

    def _confirm_source(self) -> None:
        sources = self.source_screen.source_list()
        if not sources:
            return
        index = max(0, min(self.display.source_index, len(sources) - 1))
        self.display = self.display.with_source(
            sources[index].source_id
        ).with_source_index(index)
        # Am Geraet fuehrt die bestaetigte Quelle direkt in die Trackliste.
        self.set_view(Views.BROWSE)

    def _confirm_browse(self) -> None:
        node = self.node()
        browse = self.display.browse
        entry = node.entry(browse.selected)
        if entry is None:
            return
        if entry.kind in (EntryKind.CATEGORY, EntryKind.FOLDER):
            self.display = self.display.with_browse(
                browse.entered(entry.node_key or entry.label)
            )
            self.refresh(force=True)
            return
        if entry.is_track and entry.track is not None:
            self.send_command(
                command(
                    CommandType.LOAD, self.deck_id, "ROTARY",
                    track_id=entry.track.track_id,
                )
            )

    # ------------------------------------------------------------------
    # Tag List (Handbuch S. 40-41)
    # ------------------------------------------------------------------

    @property
    def active_source_id(self) -> str:
        """Kennung der Quelle, mit der der Browser gerade arbeitet."""
        model = self.model
        if model is None:
            return ""
        return self.display.source_id or model.default_source_id()

    def target_track_id(self) -> str:
        """Track, den TAG TRACK / REMOVE meint.

        Im Durchsuchen-Bildschirm der **markierte** Track, sonst der in
        dieses Deck **geladene** (S. 40). Markiert und geladen bleiben
        damit getrennte Dinge - die Taste wirkt nur auf eines davon, je
        nachdem, was gerade sichtbar ist.
        """
        if self.view is Views.BROWSE:
            entry = self.browser.selected_entry()
            if entry is not None and entry.track is not None:
                return entry.track.track_id
            return ""
        track = self.display.deck.track
        return track.track_id if track is not None else ""

    def toggle_tag_track(self) -> bool:
        """Markierten bzw. geladenen Track vormerken oder entfernen.

        Rueckgabe: ob der Track danach in der Tag List steht.
        """
        service = self.tag_list
        source_id = self.active_source_id
        track_id = self.target_track_id()
        if service is None or not source_id or not track_id:
            self._notice("kein Track markiert")
            return False
        result = service.toggle(source_id, track_id)
        if result.message:
            self._notice(result.message)
        elif result.changed:
            self._notice(
                "zur Tag List hinzugefuegt" if result.tagged
                else "aus der Tag List entfernt"
            )
        self.refresh(force=True)
        return result.tagged

    def _remove_marked_tag_track(self) -> None:
        """Markierten Track aus der Tag List nehmen - sonst nichts.

        Die Audiodatei, die Playlists und die rekordbox-Datenbank bleiben
        unberuehrt; entfernt wird ein Eintrag in einer Liste im Speicher.
        """
        service = self.tag_list
        source_id = self.active_source_id
        track_id = self.target_track_id()
        if service is None or not source_id or not track_id:
            return
        result = service.remove(source_id, track_id)
        if result.changed:
            self._notice("aus der Tag List entfernt")
        self.refresh(force=True)

    def _menu_pressed(self) -> None:
        """MENU: in der Tag List das Tag-Menue, sonst der Verlauf (S. 37/41)."""
        if self.view is Views.BROWSE and self._in_tag_list():
            self.display = self.display.with_tag_menu_toggled()
            self.refresh(force=True)
            return
        self.set_view(Views.BROWSE, path=("HISTORY",))

    def _in_tag_list(self) -> bool:
        return self.display.browse.context is BrowseContext.TAG_LIST

    def _run_tag_menu_action(self, action: TagMenuAction) -> None:
        service = self.tag_list
        source_id = self.active_source_id
        self.display = self.display.with_tag_menu(None)
        if service is None or not source_id:
            self._notice("keine Quelle ausgewaehlt")
            self.refresh(force=True)
            return
        if action is TagMenuAction.REMOVE_ALL:
            result = service.clear(source_id)
            self._notice(result.message or "Tag List ist bereits leer")
        else:
            # Schreibzugriff. Ohne Schreib-Layer meldet der Dienst das
            # ehrlich und ruehrt den Datentraeger nicht an.
            result = service.create_playlist(source_id)
            self._notice(result.message)
        self.refresh(force=True)

    def _notice(self, text: str) -> None:
        self.display = self.display.with_notice(text)
        self._notice_at = self._time()

    def _expire_notice(self) -> None:
        if self.display.notice and self._time() - self._notice_at > NOTICE_S:
            self.display = self.display.with_notice("")

    def _back(self) -> None:
        """BACK kurz: eine Ebene hoeher, sonst zurueck zur Wellenform."""
        if self.display.tag_menu is not None:
            self.display = self.display.with_tag_menu(None)
            self.refresh(force=True)
            return
        if self.view is Views.BROWSE:
            browse = self.display.browse
            if not browse.at_top:
                self.display = self.display.with_browse(browse.up())
                self.refresh(force=True)
                return
            self.set_view(Views.WAVEFORM)
            return
        if self.view is Views.SOURCE:
            self.set_view(Views.WAVEFORM)
            return
        if self.display.panel is not TouchPanel.NONE:
            self.display = self.display.with_panel(TouchPanel.NONE)
            self._place_panels()
            self.refresh(force=True)

    def _handle_display_command(self, cmd: DeckCommand) -> None:
        """Anzeigebefehle: aendern nur ``CdjDisplayState``."""
        if cmd.type is CommandType.VIEW:
            view = cmd.get("view")
            if isinstance(view, Views):
                self.set_view(view, path=cmd.get("path"))
            return

        if cmd.type is CommandType.MENU:
            self._menu_pressed()
            return
        if cmd.type is CommandType.TAG_TRACK_TOGGLE:
            self.toggle_tag_track()
            return
        if cmd.type is CommandType.TAG_MENU_ACTION:
            action = cmd.get("action")
            if isinstance(action, TagMenuAction):
                self._run_tag_menu_action(action)
            return

        if cmd.type is CommandType.NAV_SELECT:
            self._nav_select(cmd)
            return
        if cmd.type is CommandType.NAV_ENTER:
            self._confirm()
            return
        if cmd.type is CommandType.NAV_TOP:
            self.display = self.display.with_browse(
                self.display.browse.to_top()
            )
            self.refresh(force=True)
            return
        if cmd.type is CommandType.BROWSE_SORT:
            column = cmd.get("column")
            if isinstance(column, BrowseColumn):
                self.display = self.display.with_browse(
                    self.display.browse.with_sort(column)
                )
                self.refresh(force=True)
            return
        if cmd.type is CommandType.BROWSE_TOGGLE:
            option = str(cmd.get("option", ""))
            browse = self.display.browse
            if option == "PREVIEW":
                browse = browse.toggled_preview()
            elif option == "INFO":
                browse = browse.toggled_info()
            elif option == "FONT":
                browse = browse.toggled_font()
            else:
                return
            self.display = self.display.with_browse(browse)
            self.refresh(force=True)
            return
        if cmd.type is CommandType.JUMP_MODE:
            self._toggle_jump_mode()
            return
        if cmd.type is CommandType.ROTARY_MODE:
            self.display = self.display.with_rotary_toggled()
            self.refresh(force=True)
            return

        if cmd.type is CommandType.PANEL:
            panel = cmd.get("panel")
            if isinstance(panel, TouchPanel):
                self.display = self.display.with_panel(panel)
                self._place_panels()
                self.refresh(force=True)
            return

        if cmd.type is CommandType.TIME_MODE:
            self.display = self.display.with_time_mode_toggled()
        elif cmd.type is CommandType.WAVEFORM_ZOOM:
            self.display = self.display.with_zoom(int(cmd.get("delta", 0)))
        elif cmd.type is CommandType.WAVEFORM_MODE:
            mode = cmd.get("mode")
            if isinstance(mode, WaveformMode):
                self.display = self.display.with_waveform_mode(mode)
        elif cmd.type is CommandType.HEADER_TOGGLE:
            self.display = self.display.with_header_toggled()
        elif cmd.type is CommandType.NEEDLE_LOCK:
            import dataclasses

            self.display = dataclasses.replace(
                self.display, needle_lock=not self.display.needle_lock
            )
        self.refresh(force=True)

    def _nav_select(self, cmd: DeckCommand) -> None:
        """Beruehrung einer Zeile: erst markieren, dann bestaetigen (S. 25)."""
        path = cmd.get("path")
        if path is not None:
            # Direkter Sprung in eine Kategorie ueber die linke Spalte.
            self._enter_path(path)
            self.refresh(force=True)
            return

        index = int(cmd.get("index", 0))
        confirm = bool(cmd.get("confirm", False))
        if self.view is Views.SOURCE:
            self.display = self.display.with_source_index(
                index, confirming=True
            )
            if confirm:
                self._confirm_source()
            else:
                self.refresh(force=True)
            return

        browse = self.display.browse
        node = self.node()
        if node.level_kind is EntryKind.CATEGORY and node.entries:
            # Der Cursor stand links in der Kategorienspalte; die Beruehrung
            # einer Listenzeile fuehrt in die markierte Kategorie hinein.
            entry = node.entry(browse.selected)
            if entry is not None:
                browse = browse.entered(entry.node_key or entry.label)
        self.display = self.display.with_browse(browse.at_index(index))
        if confirm:
            self._confirm_browse()
        else:
            self.refresh(force=True)

    def set_view(self, view: Views, *, path=None) -> None:
        if path is not None:
            self._enter_path(path)
        elif view is self.view:
            return
        if view is not Views.BROWSE:
            # Ein Menue der Durchsuchen-Ansicht bleibt nicht offen stehen,
            # wenn die Ansicht wechselt.
            self.display = self.display.with_tag_menu(None)
        self.view = view
        self._layout_content()
        self._place_panels()
        self.refresh(force=True)

    def _enter_path(self, path) -> None:
        """In eine Kategorie springen - PLAYLIST, TAG LIST, HISTORY.

        Der Einstieg entscheidet ueber den Kontext; jeder Kontext hat
        seinen eigenen gemerkten Stand (Markierung, Ebene, Sortierung).
        Wer dieselbe Taste erneut drueckt, landet wieder dort, wo er war -
        nicht am Anfang.
        """
        target = str(path[0]) if path else ""
        context = (
            BrowseContext.of((target,)) if target else BrowseContext.LIBRARY
        )
        display = self.display.with_context(context)
        browse = display.browse
        if target and (browse.at_top or browse.path[0] != target):
            # Frischer Einstieg in diese Kategorie. Steht der gemerkte
            # Stand schon darin, bleibt er - dieselbe Taste ein zweites Mal
            # fuehrt dorthin zurueck, wo man war, nicht an den Anfang.
            browse = browse.to_top().entered(target)
        self.display = display.with_browse(browse).with_tag_menu(None)

    # ------------------------------------------------------------------
    # Zustand und Bildaufbau
    # ------------------------------------------------------------------

    def _on_state(self, state: DeckState) -> None:
        """Die Bildschleife holt den Zustand ab - hier nichts zeichnen."""

    def _current_master_view(self) -> MasterDeckView | None:
        """Master-Sicht besorgen: lokal oder ueber die gesetzte Funktion."""
        if self.master_view_source is not None:
            return self.master_view_source()
        if self.master_provider is not None:
            return MasterDeckView.from_deck_state(
                self.master_provider.get_state()
            )
        return None

    def refresh(self, *, force: bool = False) -> None:
        deck_state = self.provider.get_state()
        self._expire_notice()
        self.display = self.display.with_deck(deck_state).with_master(
            self._current_master_view()
        )
        display = self.display

        # Anzeigezustand **zuerst** verteilen. Die Bereiche fragen ihn ab -
        # unter anderem entscheidet die Master-Zeile daran, ob sie sichtbar
        # sein muss.
        for region in self.regions():
            if hasattr(region, "set_display"):
                region.set_display(display)  # type: ignore[attr-defined]
        for panel in self.panels.values():
            panel.set_display(display)
        self.track_info_popup.set_display(display)

        # Master-Zeile kommt und geht, je nachdem welches Deck Master ist.
        show_master = (
            self.view is not Views.BROWSE
            and self.master_wave.should_show(deck_state)
        )
        if (
            self.view is not Views.BROWSE
            and show_master != bool(self.master_wave.winfo_ismapped())
        ):
            self._layout_content()
            self._place_panels()
            force = True

        # Jeder Bereich entscheidet ueber seine Signatur selbst, ob sich
        # sichtbar etwas geaendert hat. Nichts wird pauschal 60-mal je
        # Sekunde neu gezeichnet.
        if self.view is Views.BROWSE:
            self.browser.update_state(deck_state, force=force)
        elif self.view is Views.SOURCE:
            self.source_screen.update_state(deck_state, force=force)
        else:
            self.waveform_header.update_state(deck_state, force=force)
            # An ``should_show`` gekoppelt, nicht an ``winfo_ismapped``: nach
            # einer Layoutaenderung ist die Sichtbarkeit im selben Durchlauf
            # noch nicht gesetzt, gezeichnet werden muss trotzdem.
            if show_master:
                self.master_wave.update_state(deck_state, force=force)
            self.scrolling.update_state(deck_state, force=force)

        if self.view is Views.WAVEFORM:
            self.top_bar.update_state(deck_state, force=force)
        self.status_bar.update_state(deck_state, force=force)
        self.overview.update_state(deck_state, force=force)

        active = self.display.panel
        panel_widget = self.panels.get(active)
        if panel_widget is not None and panel_widget.winfo_ismapped():
            panel_widget.update_state(deck_state, force=True)
        if self.track_info_popup.winfo_ismapped():
            self.track_info_popup.update_state(deck_state, force=True)

        self.debug.render(deck_state)

    # ------------------------------------------------------------------
    # Bildschleife
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._frame()

    def stop(self) -> None:
        self._running = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:  # pragma: no cover - Fenster bereits zu
                pass
            self._after_id = None

    def _frame(self) -> None:
        if not self._running:
            return
        self.provider.tick()
        self.poll_holds()
        self.refresh()
        self.debug.note_frame()
        self._after_id = self.after(FRAME_INTERVAL_MS, self._frame)

    # ------------------------------------------------------------------

    def load_reference(self, path) -> bool:
        """Referenzbild fuer den Vergleich laden (Entwicklung).

        Erzeugt die Ansicht erst bei Bedarf - ohne ``--reference`` gibt es
        sie im laufenden Programm nicht.
        """
        if self.reference is None:
            self.reference = ReferenceView(self)
        return self.reference.load(path)

    def toggle_reference(self) -> bool:
        """Zwischen Referenzbild und eigener Oberflaeche wechseln."""
        if self.reference is None:
            return False
        if self.reference.toggle():
            self.reference.show(self.metrics.width, self.metrics.height)
        else:
            self.reference.hide()
        return self.reference.visible

    def toggle_debug(self) -> bool:
        visible = self.debug.toggle()
        if visible:
            self._place_debug()
            tk.Misc.lift(self.debug)
        else:
            self.debug.place_forget()
        return visible

    def destroy(self) -> None:
        self.stop()
        self._unsubscribe()
        super().destroy()
