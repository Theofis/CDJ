"""Durchsuchen-Bildschirm (Handbuch S. 19-20, 24-25).

Aufbau wie am Geraet:

* Kopfzeile: Geraetesymbol, ``←`` eine Ebene hoeher, Name der Ebene,
  ``PREVIEW``, Schriftgroesse, ``INFO``
* links die Kategorien, rechts die Liste der aktuellen Ebene
* Titelzeile der Liste sortiert, ``▲``/``▼`` zeigt die Richtung
* unten bleibt die Wiedergabestatusanzeige des Bildschirms sichtbar

Der Inhalt kommt aus der ``MediaLibrary``; der Navigationszustand (Pfad,
Markierung, Sortierung, Schalter) steht in ``CdjDisplayState.browse``. Der
Bildschirm haelt selbst keinen zweiten Zustand.

Ohne Quelle bleibt die Ansicht ausdruecklich leer - es wird keine Liste
erfunden.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Sequence

from ..deck.commands import CommandType, command
from ..deck.display_state import CdjDisplayState, JumpMode, TagMenuAction
from ..deck.library import (
    PLAYLIST_CATEGORY,
    TAG_LIST_CATEGORY,
    BrowseColumn,
    BrowseEntry,
    BrowseNode,
    EntryKind,
    MediaLibrary,
)
from ..deck.state import DeckState, TrackInfo
from . import icons, theme
from .base import Region

#: Spalten der Trackliste: Spalte, relative Breite, Feldname. Ein Tippen
#: auf den Kopf sortiert danach (S. 20); die Anteile ergeben zusammen 1.0.
COLUMNS: tuple[tuple[BrowseColumn, float, str], ...] = (
    (BrowseColumn.NUMBER, 0.06, "number"),
    (BrowseColumn.TITLE, 0.28, "title"),
    (BrowseColumn.ARTIST, 0.20, "artist"),
    (BrowseColumn.BPM, 0.10, "bpm"),
    (BrowseColumn.KEY, 0.08, "key"),
    (BrowseColumn.RATING, 0.12, "rating"),
    (BrowseColumn.TIME, 0.10, "time"),
    (BrowseColumn.COLOR, 0.06, "color"),
)

#: Schalter der Kopfzeile (S. 19, Elemente 5-7).
HEADER_TOGGLES: tuple[tuple[str, str], ...] = (
    ("PREVIEW", "PREVIEW"),
    ("A A", "FONT"),
    ("INFO", "INFO"),
)

#: Hoechste darstellbare Bewertung.
MAX_RATING = 5


def _duration_text(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}:{rest:02d}"


def _cell(entry: BrowseEntry, field: str) -> str:
    track = entry.track
    if field == "number":
        return f"{entry.number:03d}" if entry.number else "—"
    if track is None:
        return entry.label if field == "title" else ""
    if field == "bpm":
        return f"{track.original_bpm:.1f}" if track.original_bpm else "—"
    if field == "key":
        return track.key or "—"
    if field == "artist":
        return track.artist or "—"
    if field == "rating":
        # Sterne werden gezeichnet; ohne Bewertung steht hier ein
        # Gedankenstrich statt fuenf leerer Sterne - eine nicht bewertete
        # Datei ist etwas anderes als eine mit null Sternen bewertete.
        return "" if track.rating > 0 else "—"
    if field == "time":
        return _duration_text(track.duration_s)
    if field == "color":
        # Die Farbe wird als Flaeche gezeichnet, nicht als Text. Fehlt sie,
        # bleibt die Zelle leer - es wird keine Farbe erfunden.
        return ""
    return track.display_title


class CdjBrowser(Region):
    background = theme.BG

    def __init__(self, master: tk.Misc, send, deck_id: int) -> None:
        super().__init__(master, send)
        self.deck_id = deck_id
        self.display: CdjDisplayState | None = None
        #: Inhaltsquelle. Wird von der Verdrahtung gesetzt.
        self.model: MediaLibrary | None = None
        self._rows: list[tuple[tuple[float, float, float, float], int]] = []
        self._categories: list[
            tuple[tuple[float, float, float, float], str]
        ] = []
        self._sort_hits: list[
            tuple[tuple[float, float, float, float], BrowseColumn]
        ] = []
        self._toggle_hits: list[
            tuple[tuple[float, float, float, float], str]
        ] = []
        self._back_box: tuple[float, float, float, float] | None = None
        self._load_box: tuple[float, float, float, float] | None = None
        self._tag_menu_hits: list[
            tuple[tuple[float, float, float, float], TagMenuAction]
        ] = []
        #: Ob ``CREATE PLAYLIST`` benutzbar ist. Wird von der Verdrahtung
        #: gesetzt; ohne Schreib-Layer bleibt der Eintrag abgeblendet.
        self.tag_list_writable = False
        self.bind("<Button-1>", self._on_press)

    # ------------------------------------------------------------------
    # Zustand und Inhalt
    # ------------------------------------------------------------------

    def set_display(self, display: CdjDisplayState) -> None:
        # Kein blindes Ungueltigmachen: was sichtbar wird, steht in
        # ``signature`` - sonst zeichnete jeder Bereich in jedem Bild neu.
        self.display = display

    def signature(self, state: DeckState) -> tuple:
        """Der Browser aendert sich nur auf Navigation oder Trackwechsel."""
        display = self.display
        if display is None:
            return ("leer", self.model is None)
        browse = display.browse
        library = (
            self.model.library(display.source_id or "")
            if self.model is not None else None
        )
        return (
            self.model is None,
            display.source_id,
            browse.path, browse.selected, browse.confirming,
            browse.sort_column, browse.ascending,
            browse.preview, browse.info, browse.large_font, browse.jump,
            state.track.track_id if state.track else "",
            display.tag_menu, display.notice,
            # Die Tag List aendert sich, ohne dass sich die Navigation
            # aendert - ein neuer Haken muss trotzdem erscheinen.
            library.tag_ids() if library is not None else (),
        )

    def set_model(self, model: MediaLibrary | None) -> None:
        self.model = model
        self.invalidate()

    def set_track_source(self, source) -> None:
        """Rueckwaertskompatibel: flache Trackliste als einzige Quelle.

        Baut aus der Liste eine Quelle ohne Bibliothek. Die eigentliche
        Verdrahtung setzt besser ``set_model``.
        """
        from ..deck.library import SourceInfo, SourceKind, TrackListLibrary

        if source is None:
            self.set_model(None)
            return
        library = MediaLibrary()
        library.add(
            TrackListLibrary(
                SourceInfo(
                    source_id="TRACKS", name="TRACKS",
                    kind=SourceKind.FILE, has_library=False,
                ),
                source,
            )
        )
        self.set_model(library)

    # ------------------------------------------------------------------

    def node(self) -> BrowseNode:
        """Aktuelle Ebene aus Modell und Anzeigezustand."""
        display = self.display
        model = self.model
        if model is None or display is None:
            return BrowseNode(source_id="")
        browse = display.browse
        source_id = display.source_id or model.default_source_id()
        return model.node(
            source_id, browse.path,
            column=browse.sort_column, ascending=browse.ascending,
        )

    def _listing(self, node: BrowseNode) -> tuple[BrowseNode, bool]:
        """Was rechts in der Liste steht und ob der Cursor dort liegt.

        Auf der Wurzelebene steht der Cursor in der Kategorienspalte; die
        Liste zeigt dann bereits den Inhalt der markierten Kategorie - wie
        am Geraet (S. 19). Sonst ist es die aktuelle Ebene selbst.
        """
        display = self.display
        model = self.model
        if (
            node.level_kind is not EntryKind.CATEGORY
            or not node.entries
            or display is None
            or model is None
        ):
            return node, True
        entry = node.entry(display.browse.selected)
        if entry is None:
            return node, True
        browse = display.browse
        listing = model.node(
            node.source_id, (entry.node_key or entry.label,),
            column=browse.sort_column, ascending=browse.ascending,
        )
        return listing, False

    def list_node(self) -> BrowseNode:
        """Inhalt der Liste - fuer Tests und die Verdrahtung."""
        return self._listing(self.node())[0]

    def categories(self) -> tuple[BrowseEntry, ...]:
        """Kategorienspalte - immer die Wurzel der aktiven Quelle."""
        display = self.display
        model = self.model
        if model is None or display is None:
            return ()
        source_id = display.source_id or model.default_source_id()
        root = model.node(source_id)
        if root.level_kind is EntryKind.CATEGORY:
            return root.entries
        return ()

    def selected_entry(self) -> BrowseEntry | None:
        display = self.display
        if display is None:
            return None
        return self.node().entry(display.browse.selected)

    def entries(self) -> Sequence[BrowseEntry]:
        return self.node().entries

    # ------------------------------------------------------------------
    # Bedienung
    # ------------------------------------------------------------------

    def load_selected(self) -> None:
        entry = self.selected_entry()
        if entry is None or not entry.is_track:
            return
        track: TrackInfo = entry.track  # type: ignore[assignment]
        self.send(
            command(
                CommandType.LOAD, self.deck_id, "TOUCH",
                track_id=track.track_id,
            )
        )

    def _on_press(self, event: tk.Event) -> None:
        x, y = event.x, event.y
        display = self.display

        def inside(box) -> bool:
            return box is not None and box[0] <= x <= box[2] and box[1] <= y <= box[3]

        # Ein offenes Menue liegt oben: es faengt die Beruehrung ab, sonst
        # wuerde durch das Menue hindurch die Liste bedient.
        if display is not None and display.tag_menu is not None:
            for box, action in self._tag_menu_hits:
                if inside(box):
                    self.send(
                        command(
                            CommandType.TAG_MENU_ACTION, self.deck_id,
                            "TOUCH", action=action,
                        )
                    )
                    return
            return

        if inside(self._back_box):
            self.send(command(CommandType.BACK, self.deck_id, "TOUCH"))
            return
        if inside(self._load_box):
            self.load_selected()
            return
        for box, option in self._toggle_hits:
            if inside(box):
                self.send(
                    command(
                        CommandType.BROWSE_TOGGLE, self.deck_id, "TOUCH",
                        option=option,
                    )
                )
                return
        for box, column in self._sort_hits:
            if inside(box):
                self.send(
                    command(
                        CommandType.BROWSE_SORT, self.deck_id, "TOUCH",
                        column=column,
                    )
                )
                return
        for box, name in self._categories:
            if inside(box):
                self.send(
                    command(
                        CommandType.NAV_SELECT, self.deck_id, "TOUCH",
                        path=(name,),
                    )
                )
                return
        for box, index in self._rows:
            if inside(box):
                already = (
                    display is not None
                    and display.browse.selected == index
                    and display.browse.confirming
                )
                self.send(
                    command(
                        CommandType.NAV_SELECT, self.deck_id, "TOUCH",
                        index=index, confirm=already,
                    )
                )
                return

    # ------------------------------------------------------------------
    # Zeichnen
    # ------------------------------------------------------------------

    def render(self, state: DeckState) -> None:
        m = self.metrics
        self._rows.clear()
        self._categories.clear()
        self._sort_hits.clear()
        self._toggle_hits.clear()
        self._back_box = None
        self._load_box = None

        width, height = self.w, self.h
        display = self.display
        node = self.node()
        # Auf der Wurzelebene liegt der Cursor links; rechts steht der Inhalt
        # der markierten Kategorie.
        listing, cursor_in_list = self._listing(node)
        browse = display.browse if display else None

        header_h = m.px(26)
        self._render_header(node, header_h)

        sidebar = m.px(120)
        self._render_sidebar(node, sidebar, header_h, height)

        list_x = sidebar
        list_w = width - sidebar
        if browse is not None and browse.info:
            list_w -= m.px(210)

        if self.model is None:
            self._render_empty(
                list_x, header_h, list_x + list_w, height,
                "KEINE TRACKQUELLE VERBUNDEN",
                "Demo-Modus: python run_cdj.py --demo",
            )
            return

        column_h = m.px(20)
        self._render_columns(
            listing, list_x, header_h, list_x + list_w, column_h
        )

        if listing.is_empty:
            message, detail = self._empty_text(listing)
            self._render_empty(
                list_x, header_h + column_h, list_x + list_w, height,
                message, detail,
            )
        else:
            self._render_entries(
                listing, state,
                list_x, header_h + column_h, list_x + list_w, height,
                cursor=cursor_in_list,
            )

        if browse is not None and browse.info:
            self._render_info(
                node, list_x + list_w, header_h, width, height
            )

        if browse is not None and browse.jump is not JumpMode.OFF:
            self._render_jump(node, browse)

        if display is not None and display.notice:
            self._render_notice(display.notice, list_x, header_h, width)

        if display is not None and display.tag_menu is not None:
            self._render_tag_menu(display.tag_menu, list_x, header_h, width)

    # ------------------------------------------------------------------

    def _render_notice(
        self, text: str, x0: float, y0: float, x1: float
    ) -> None:
        """Kurze Rueckmeldung ueber der Liste, z. B. "Tag List voll"."""
        m = self.metrics
        height = m.px(20)
        self.box(x0, y0, x1, y0 + height, fill=theme.PANEL_ACTIVE, outline="")
        self.text(
            (x0 + x1) / 2, y0 + height / 2, text,
            size=8, weight="bold", fill=theme.TEXT, anchor="center",
        )

    def _render_tag_menu(
        self, active: TagMenuAction, x0: float, y0: float, x1: float
    ) -> None:
        """Tag-List-Menue (Handbuch S. 41).

        Zwei Eintraege. ``CREATE PLAYLIST`` bleibt abgeblendet, solange die
        Bibliothek nur lesend geoeffnet ist - es wird nicht so getan, als
        koennte hier auf den Stick geschrieben werden.
        """
        m = self.metrics
        self._tag_menu_hits.clear()
        row_h = m.px(28)
        width = m.px(260)
        left = (x0 + x1) / 2 - width / 2
        top = y0 + m.px(40)
        entries = list(TagMenuAction)
        height = row_h * len(entries) + m.px(24)

        self.box(
            left, top, left + width, top + height,
            fill=theme.PANEL, outline=theme.ACCENT,
        )
        self.text(
            left + m.px(10), top + m.px(12), "TAG LIST MENU",
            size=8, weight="bold", fill=theme.TEXT_DIM, anchor="w",
        )
        y = top + m.px(24)
        for entry in entries:
            enabled = entry is not TagMenuAction.CREATE_PLAYLIST or (
                self.tag_list_writable
            )
            selected = entry is active
            if selected:
                self.box(
                    left + m.px(4), y, left + width - m.px(4), y + row_h,
                    fill=theme.PANEL_HI, outline="",
                )
            colour = theme.TEXT_DISABLED
            if enabled:
                colour = theme.ACCENT if selected else theme.TEXT_SECOND
            self.text(
                left + m.px(14), y + row_h / 2, entry.value,
                size=9, weight="bold" if selected else "normal",
                fill=colour, anchor="w",
            )
            if not enabled:
                self.text(
                    left + width - m.px(14), y + row_h / 2, "nur lesend",
                    size=7, fill=theme.TEXT_MUTED, anchor="e",
                )
            self._tag_menu_hits.append(
                ((left, y, left + width, y + row_h), entry)
            )
            y += row_h

    # ------------------------------------------------------------------

    def _empty_text(self, node: BrowseNode) -> tuple[str, str]:
        """Was eine leere Liste sagt - je nachdem, warum sie leer ist.

        "Keine Eintraege" waere in der Playlist-Kategorie irrefuehrend: der
        Stick hat dann keine rekordbox-Playlists, und das ist etwas anderes
        als eine leere Playlist.
        """
        path = node.path
        if path and path[0] == TAG_LIST_CATEGORY:
            return (
                "TAG LIST IST LEER",
                "Track markieren und TAG TRACK / REMOVE druecken",
            )
        if path and path[0] == PLAYLIST_CATEGORY:
            if len(path) == 1:
                return (
                    "NO PLAYLISTS",
                    "Dieser Datentraeger hat keine rekordbox-Playlists",
                )
            return ("PLAYLIST IST LEER", node.breadcrumb())
        return ("KEINE EINTRAEGE", node.breadcrumb())

    def _source_badge(self, source_id: str) -> str:
        """Kurzzeichen der Quelle fuer das Geraetesymbol (S. 19)."""
        model = self.model
        if model is not None and source_id:
            library = model.library(source_id)
            if library is not None:
                return library.info.kind.value
        return "—"

    def _render_header(self, node: BrowseNode, height: float) -> None:
        m = self.metrics
        display = self.display
        self.box(0, 0, self.w, height, fill=theme.PANEL_HI, outline="")
        self.create_line(0, height, self.w, height, fill=theme.BORDER)

        # Geraetesymbol der aktiven Quelle. Gezeigt wird der Geraetetyp
        # (USB/SD/LINK) wie am Geraet - nicht die technische Kennung. Die
        # ist bei einem echten Stick ``USB:<Seriennummer>`` und im Symbol
        # abgeschnitten unlesbar.
        icon_w = m.px(40)
        self.box(
            m.px(4), m.px(3), m.px(4) + icon_w, height - m.px(3),
            fill=theme.PANEL_HI, outline="",
        )
        self.text(
            m.px(4) + icon_w / 2, height / 2,
            self._source_badge(node.source_id),
            size=7, weight="bold", fill=theme.TEXT_DIM, anchor="center",
        )

        # ← eine Ebene hoeher (S. 19, Element 3)
        back_x = m.px(4) + icon_w + m.px(6)
        back_w = m.px(26)
        enabled = not node.is_root
        self.box(
            back_x, m.px(3), back_x + back_w, height - m.px(3),
            fill=theme.PANEL_HI if enabled else theme.PANEL,
            outline="",
        )
        icons.arrow_left(
            self, back_x + back_w / 2, height / 2, m.px(11),
            theme.ACCENT if enabled else theme.TEXT_DISABLED,
        )
        if enabled:
            self._back_box = (back_x, 0, back_x + back_w, height)

        self.text(
            back_x + back_w + m.px(10), height / 2,
            node.breadcrumb(node.title),
            size=10, weight="bold", fill=theme.TEXT, anchor="w",
        )

        # PREVIEW / Schriftgroesse / INFO rechts
        browse = display.browse if display else None
        x1 = self.w - m.px(6)
        for label, option in reversed(HEADER_TOGGLES):
            active = bool(
                browse is not None
                and (
                    (option == "PREVIEW" and browse.preview)
                    or (option == "INFO" and browse.info)
                    or (option == "FONT" and browse.large_font)
                )
            )
            box_w = m.px(58) if option != "FONT" else m.px(34)
            x0 = x1 - box_w
            self.box(
                x0, m.px(3), x1, height - m.px(3),
                fill=theme.ACCENT if active else theme.PANEL_HI,
                outline="",
            )
            self.text(
                (x0 + x1) / 2, height / 2, label,
                size=7, weight="bold",
                fill=theme.BG if active else theme.TEXT_DIM, anchor="center",
            )
            self._toggle_hits.append(((x0, 0, x1, height), option))
            x1 = x0 - m.px(4)

    def _render_sidebar(
        self, node: BrowseNode, sidebar: float, top: float, height: float
    ) -> None:
        m = self.metrics
        self.box(0, top, sidebar, height, fill=theme.PANEL, outline="")
        self.create_line(
            m.snap(sidebar), top, m.snap(sidebar), height,
            fill=theme.LINE_SUBTLE,
        )

        categories = self.categories()
        if not categories:
            return

        display = self.display
        browse = display.browse if display else None
        # Auf der Wurzelebene liegt der Cursor in der Kategorienspalte.
        cursor_here = browse is not None and browse.at_top
        current = browse.path[0] if browse and browse.path else ""

        row_h = m.px(26)
        for index, entry in enumerate(categories):
            y0 = top + index * row_h
            y1 = y0 + row_h
            selected = (
                (cursor_here and browse is not None and browse.selected == index)
                or (not cursor_here and entry.label == current)
            )
            if selected:
                self.box(
                    0, y0, sidebar, y1,
                    fill=theme.PANEL_HI,
                    outline=(
                        theme.ACCENT
                        if cursor_here and browse and browse.confirming
                        else ""
                    ),
                )
                self.create_line(0, y0, 0, y1, fill=theme.ACCENT, width=4)
            self.text(
                m.px(12), (y0 + y1) / 2, entry.label,
                size=8, weight="bold" if selected else "normal",
                fill=theme.TEXT if selected else theme.TEXT_DIM, anchor="w",
            )
            self.text(
                sidebar - m.px(8), (y0 + y1) / 2, str(entry.count or ""),
                size=7, fill=theme.TEXT_MUTED, anchor="e",
            )
            self._categories.append(((0, y0, sidebar, y1), entry.label))

    def _render_columns(
        self,
        node: BrowseNode,
        x0: float,
        y0: float,
        x1: float,
        height: float,
    ) -> None:
        m = self.metrics
        self.box(x0, y0, x1, y0 + height, fill=theme.PANEL_HI, outline="")
        self.create_line(
            x0, y0 + height, x1, y0 + height, fill=theme.BORDER
        )
        if not node.is_track_list:
            self.text(
                x0 + m.px(8), y0 + height / 2, node.title or "—",
                size=7, weight="bold", fill=theme.TEXT_DIM, anchor="w",
            )
            count = len(node.entries)
            self.text(
                x1 - m.px(8), y0 + height / 2,
                f"{count} Eintrag" if count == 1 else f"{count} Eintraege",
                size=7, fill=theme.TEXT_MUTED, anchor="e",
            )
            return

        display = self.display
        browse = display.browse if display else None
        width = x1 - x0
        preview_w = self._preview_width(x1 - x0)
        x = x0 + preview_w
        if preview_w:
            self.text(
                x0 + m.px(6), y0 + height / 2, "PREVIEW",
                size=7, weight="bold", fill=theme.TEXT_MUTED, anchor="w",
            )
        for column, share, _ in COLUMNS:
            cell_w = (width - preview_w) * share
            sorted_by = browse is not None and browse.sort_column is column
            item = self.text(
                x + m.px(6), y0 + height / 2, column.value,
                size=7, weight="bold",
                fill=theme.TEXT if sorted_by else theme.TEXT_DIM, anchor="w",
            )
            if sorted_by:
                bounds = self.bbox(item)
                caret = (
                    icons.caret_up if browse.ascending else icons.caret_down
                )
                caret(
                    self, bounds[2] + m.px(5), y0 + height / 2,
                    m.px(5), theme.ACCENT,
                )
            self._sort_hits.append(
                ((x, y0, x + cell_w, y0 + height), column)
            )
            x += cell_w
            self.create_line(
                m.snap(x), y0, m.snap(x), self.h, fill=theme.LINE_SUBTLE
            )

    def _preview_width(self, list_width: float) -> float:
        display = self.display
        if display is None or not display.browse.preview:
            return 0.0
        return list_width * 0.16

    def _render_entries(
        self,
        node: BrowseNode,
        state: DeckState,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        cursor: bool = True,
    ) -> None:
        m = self.metrics
        display = self.display
        browse = display.browse if display else None
        selected = browse.selected if browse and cursor else -1
        confirming = bool(browse.confirming) if browse and cursor else False
        large = bool(browse.large_font) if browse else False

        row_h = m.px(30 if large else 24)
        size = 9 if large else 8
        visible = max(1, int((y1 - y0) / row_h))
        first = max(
            0,
            min(max(0, selected) - visible // 2, len(node.entries) - visible),
        )
        loaded_id = state.track.track_id if state.track else ""

        preview_w = self._preview_width(x1 - x0)
        width = x1 - x0

        for offset in range(min(visible, len(node.entries) - first)):
            index = first + offset
            entry = node.entries[index]
            ry0 = y0 + offset * row_h
            ry1 = ry0 + row_h
            is_selected = index == selected
            if is_selected:
                self.box(
                    x0, ry0, x1, ry1,
                    fill=theme.PANEL_ACTIVE if confirming else theme.PANEL_HI,
                    outline="",
                )
                # Markierung als Balken links - kein Rahmen um die Zeile.
                self.create_line(
                    m.snap(x0 + 1), ry0, m.snap(x0 + 1), ry1,
                    fill=theme.ACCENT, width=2,
                )
            self._rows.append(((x0, ry0, x1, ry1), index))

            if entry.is_track:
                self._render_track_row(
                    entry, x0, ry0, x1, ry1,
                    preview_w=preview_w, width=width, size=size,
                    is_selected=is_selected,
                    is_loaded=entry.track is not None
                    and entry.track.track_id == loaded_id,
                )
                if is_selected and confirming:
                    self._render_load_button(x1, ry0, ry1)
            else:
                self._render_folder_row(
                    entry, x0, ry0, x1, ry1,
                    size=size, is_selected=is_selected,
                )

    def _render_folder_row(
        self,
        entry: BrowseEntry,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        size: int,
        is_selected: bool,
    ) -> None:
        m = self.metrics
        colour = theme.TEXT if is_selected else theme.TEXT_SECOND
        text_x = x0 + m.px(10)
        if entry.kind is EntryKind.FOLDER:
            icons.triangle_right(
                self, text_x + m.px(3), (y0 + y1) / 2, m.px(6),
                theme.ACCENT if is_selected else theme.TEXT_DIM,
            )
            text_x += m.px(12)
        self.text(
            text_x, (y0 + y1) / 2, entry.label,
            size=size, weight="bold" if is_selected else "normal",
            fill=colour, anchor="w",
        )
        if entry.count:
            self.text(
                x1 - m.px(12), (y0 + y1) / 2, str(entry.count),
                size=7, fill=theme.TEXT_MUTED, anchor="e",
            )

    def _render_track_row(
        self,
        entry: BrowseEntry,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        preview_w: float,
        width: float,
        size: int,
        is_selected: bool,
        is_loaded: bool,
    ) -> None:
        m = self.metrics
        if preview_w:
            self._render_preview(
                entry, x0 + m.px(3), y0 + m.px(3),
                x0 + preview_w - m.px(3), y1 - m.px(3),
            )
        x = x0 + preview_w
        for column, share, field in COLUMNS:
            cell_w = (width - preview_w) * share
            if field == "color":
                self._render_colour(entry, x, y0, x + cell_w, y1)
                x += cell_w
                continue
            if field == "rating" and entry.track is not None and (
                entry.track.rating > 0
            ):
                self._render_rating(entry.track.rating, x, y0, y1)
                x += cell_w
                continue
            text = _cell(entry, field)
            cell_x = x + m.px(6)
            if field == "title":
                if entry.tagged:
                    # Haken wie am Geraet: der Track steht in der Tag List.
                    icons.check(
                        self, cell_x + m.px(4), (y0 + y1) / 2, m.px(7),
                        theme.YELLOW,
                    )
                    cell_x += m.px(12)
                if is_loaded:
                    icons.triangle_right(
                        self, cell_x + m.px(3), (y0 + y1) / 2, m.px(6),
                        theme.ACCENT,
                    )
                    cell_x += m.px(12)
            # Gruen bedeutet laut Handbuch S. 42 "wurde gespielt".
            colour = (
                theme.TEXT_SECOND if field == "title" else theme.TEXT_DIM
            )
            if entry.played:
                colour = theme.GREEN
            if is_selected:
                colour = theme.TEXT if not entry.played else theme.GREEN
            if is_loaded and field == "title":
                colour = theme.ACCENT
            self.text(
                cell_x, (y0 + y1) / 2, text,
                size=size,
                weight="bold" if (is_selected or is_loaded) else "normal",
                fill=colour, anchor="w",
                mono=field in ("number", "bpm", "key", "time"),
            )
            x += cell_w

    def _render_rating(
        self, rating: int, x0: float, y0: float, y1: float
    ) -> None:
        """Bewertung als gefuellte Sterne (0-5)."""
        m = self.metrics
        size = m.px(7)
        x = x0 + m.px(8)
        for _ in range(max(0, min(rating, MAX_RATING))):
            icons.star(self, x, (y0 + y1) / 2, size, theme.YELLOW)
            x += size + m.px(2)

    def _render_colour(
        self,
        entry: BrowseEntry,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
    ) -> None:
        """Farbkennzeichnung als kleine Flaeche. Ohne Farbe bleibt es leer."""
        track = entry.track
        colour = theme.track_colour(track.color) if track is not None else ""
        if not colour:
            return
        m = self.metrics
        cy = (y0 + y1) / 2
        self.box(
            x0 + m.px(6), cy - m.px(4), x0 + m.px(16), cy + m.px(4),
            fill=colour, outline="",
        )

    def _render_preview(
        self,
        entry: BrowseEntry,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
    ) -> None:
        """Kleine Wellenform in der PREVIEW-Spalte (S. 19, Element 5)."""
        track = entry.track
        if track is None or track.waveform is None:
            return
        if not track.waveform.is_consistent():
            return
        level = track.waveform.coarsest
        if level is None or not level.length:
            return
        import numpy as np

        values = np.asarray(level.low, dtype=float)
        columns = max(1, int(x1 - x0))
        if values.size < 2 or columns < 2:
            return
        centre = (y0 + y1) / 2
        amplitude = (y1 - y0) / 2
        step = values.size / columns
        for column in range(columns):
            start = int(column * step)
            stop = max(start + 1, int((column + 1) * step))
            value = float(values[start:stop].max()) * amplitude
            px = x0 + column
            self.create_line(
                px, centre - value, px, centre + value, fill=theme.WAVE_LOW
            )

    def _render_load_button(self, x1: float, y0: float, y1: float) -> None:
        """``LOAD`` erscheint auf dem markierten Track (S. 25)."""
        m = self.metrics
        width = m.px(60)
        x0 = x1 - width - m.px(4)
        self.box(
            x0, y0 + m.px(2), x1 - m.px(4), y1 - m.px(2),
            fill=theme.ACCENT, outline=theme.ACCENT,
        )
        self.text(
            (x0 + x1 - m.px(4)) / 2, (y0 + y1) / 2, "LOAD",
            size=8, weight="bold", fill=theme.BG, anchor="center",
        )
        self._load_box = (x0, y0, x1, y1)

    def _render_info(
        self,
        node: BrowseNode,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
    ) -> None:
        """Infospalte zum markierten Track (S. 20, Element 7)."""
        m = self.metrics
        self.box(x0, y0, x1, y1, fill=theme.PANEL, outline="")
        self.create_line(x0, y0, x0, y1, fill=theme.BORDER)
        entry = self.selected_entry()
        track = entry.track if entry is not None else None
        if track is None:
            self.text(
                (x0 + x1) / 2, y0 + m.px(20), "INFO",
                size=8, weight="bold", fill=theme.TEXT_MUTED, anchor="center",
            )
            return

        pad = m.px(10)
        self.text(
            x0 + pad, y0 + m.px(10), track.display_title,
            size=10, weight="bold", fill=theme.TEXT,
        )
        rows = (
            ("ARTIST", track.artist),
            ("ALBUM", track.album),
            ("GENRE", track.genre),
            ("LABEL", track.label),
            ("BPM", f"{track.original_bpm:.2f}" if track.original_bpm else ""),
            ("KEY", track.key),
            ("TIME", theme.format_time(track.duration_s)),
            ("SOURCE", track.source),
        )
        y = y0 + m.px(34)
        for label, value in rows:
            self.text(x0 + pad, y, label, size=7, fill=theme.TEXT_MUTED)
            self.text(
                x1 - pad, y, value or "—",
                size=8, fill=theme.TEXT_DIM, anchor="ne",
            )
            y += m.px(20)

    def _render_jump(self, node: BrowseNode, browse) -> None:
        """Sprungmodus: Anfangsbuchstabe bzw. Seite anzeigen (S. 38)."""
        m = self.metrics
        entry = node.entry(browse.selected)
        if browse.jump is JumpMode.ALPHABET:
            text = entry.initial if entry is not None else "—"
            label = "ALPHABET"
        else:
            page = browse.selected // 10 + 1
            text = str(page)
            label = "SEITE"
        width = m.px(120)
        height = m.px(96)
        x0 = (self.w - width) / 2
        y0 = (self.h - height) / 2
        self.box(
            x0, y0, x0 + width, y0 + height,
            fill=theme.PANEL_HI, outline=theme.ACCENT, width=2,
        )
        self.text(
            x0 + width / 2, y0 + height / 2 + m.px(6), text,
            size=34, weight="bold", fill=theme.ACCENT, anchor="center",
        )
        self.text(
            x0 + width / 2, y0 + m.px(12), label,
            size=7, weight="bold", fill=theme.TEXT_DIM, anchor="center",
        )

    def _render_empty(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        message: str,
        detail: str = "",
    ) -> None:
        m = self.metrics
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        self.text(
            cx, cy - m.px(10), message,
            size=11, weight="bold", fill=theme.TEXT_MUTED, anchor="center",
        )
        if detail:
            self.text(
                cx, cy + m.px(10), detail,
                size=8, fill=theme.TEXT_MUTED, anchor="center",
            )
