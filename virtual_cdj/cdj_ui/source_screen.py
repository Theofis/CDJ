"""[SOURCE]-Bildschirm - Quellenauswahl (Handbuch S. 18 und 36).

Aufbau wie am Geraet: links die Geraetesymbole mit Playernummer, in der
Mitte die Geraetenamen mit der Markierung ``Loaded``, rechts die
Geraeteinformation.

Es werden ausschliesslich Quellen gezeigt, die die Anwendung wirklich
anbietet. Ohne Quelle bleibt die Liste leer und sagt das - es werden keine
USB- oder SD-Geraete erfunden.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence

from ..deck.commands import CommandType, command
from ..deck.display_state import CdjDisplayState
from ..deck.library import SourceInfo, SourceKind
from ..deck.state import DeckState
from . import icons, theme
from .base import Region

#: Farbe je Geraetetyp - am Geraet die Hintergrundfarbe des Symbols (S. 18).
KIND_COLORS: dict[SourceKind, str] = {
    SourceKind.SD: theme.GREEN,
    SourceKind.USB: theme.RED,
    SourceKind.LINK: theme.ACCENT,
    SourceKind.DEMO: theme.MAGENTA,
    SourceKind.FILE: theme.YELLOW,
}

#: Zeilen der Geraeteinformation: Beschriftung, Feldname (S. 18, Element 3).
#: ``Status``, ``Library`` und ``Format`` stehen am Geraet nicht - sie sind
#: hier noetig, damit ein erkannter, aber noch nicht gelesener oder nicht
#: unterstuetzter Datentraeger als das erkennbar ist und nicht wie eine
#: leere Bibliothek aussieht.
INFO_ROWS: tuple[tuple[str, str], ...] = (
    ("Status", "status"),
    ("Library", "library_format"),
    ("Format", "filesystem"),
    ("Songs", "songs"),
    ("Playlists", "playlists"),
    ("Date", "date"),
    ("Total", "total_mb"),
    ("Available", "available_mb"),
)


def _info_value(info: SourceInfo, field: str) -> str:
    value = getattr(info, field, "")
    if field in ("total_mb", "available_mb"):
        number = float(value or 0.0)
        if number <= 0:
            return "—"
        if number >= 1024:
            return f"{number / 1024:.1f} GB"
        return f"{number:.0f} MB"
    if field in ("songs", "playlists"):
        return str(int(value or 0))
    return str(value) if value else "—"


class CdjSourceScreen(Region):
    """Quellenauswahl."""

    background = theme.BG

    def __init__(self, master: tk.Misc, send, deck_id: int) -> None:
        super().__init__(master, send)
        self.deck_id = deck_id
        self.display: CdjDisplayState | None = None
        #: Quelle der Liste. Wird von der Verdrahtung gesetzt.
        self.sources: Callable[[], Sequence[SourceInfo]] | None = None
        self._rows: list[tuple[tuple[float, float, float, float], int]] = []
        self.bind("<Button-1>", self._on_press)

    # ------------------------------------------------------------------

    def set_display(self, display: CdjDisplayState) -> None:
        # Kein blindes Ungueltigmachen: was sichtbar wird, steht in
        # ``signature`` - sonst zeichnete jeder Bereich in jedem Bild neu.
        self.display = display

    def signature(self, state: DeckState) -> tuple:
        sources = self.source_list()
        display = self.display
        return (
            # Auch Zustand und Warnung gehoeren dazu: ein Stick, der von
            # "wird gelesen" auf "bereit" springt, hat oft noch dieselbe
            # Trackzahl (0) und wuerde sonst nicht neu gezeichnet.
            tuple(
                (
                    info.source_id, info.songs, info.playlists,
                    info.status, info.warning, info.available_mb,
                )
                for info in sources
            ),
            display.source_index if display else 0,
            display.source_confirming if display else False,
            display.source_id if display else "",
        )

    def set_sources(
        self, sources: Callable[[], Sequence[SourceInfo]] | None
    ) -> None:
        self.sources = sources
        self.invalidate()

    def source_list(self) -> tuple[SourceInfo, ...]:
        if self.sources is None:
            return ()
        try:
            return tuple(self.sources())
        except Exception:  # pragma: no cover - defekte Quelle
            return ()

    def selected_source(self) -> SourceInfo | None:
        items = self.source_list()
        if not items:
            return None
        index = self.display.source_index if self.display else 0
        return items[max(0, min(index, len(items) - 1))]

    # ------------------------------------------------------------------

    def _on_press(self, event: tk.Event) -> None:
        """Erste Beruehrung markiert, zweite bestaetigt (S. 25)."""
        display = self.display
        for (x0, y0, x1, y1), index in self._rows:
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                already = (
                    display is not None
                    and display.source_index == index
                    and display.source_confirming
                )
                self.send(
                    command(
                        CommandType.NAV_SELECT, self.deck_id, "TOUCH",
                        index=index, confirm=already,
                    )
                )
                return

    # ------------------------------------------------------------------

    def render(self, state: DeckState) -> None:
        m = self.metrics
        self._rows.clear()
        width, height = self.w, self.h
        display = self.display

        header_h = m.px(24)
        self.box(0, 0, width, header_h, fill=theme.PANEL_HI, outline="")
        self.text(
            m.px(12), header_h / 2, "SOURCE",
            size=10, weight="bold", fill=theme.TEXT, anchor="w",
        )

        sources = self.source_list()
        icon_w = m.px(58)
        info_x = width - m.px(250)

        self.create_line(
            m.snap(icon_w), header_h, m.snap(icon_w), height,
            fill=theme.LINE_SUBTLE,
        )
        self.create_line(
            m.snap(info_x), header_h, m.snap(info_x), height,
            fill=theme.LINE_SUBTLE,
        )

        if not sources:
            self.text(
                (icon_w + info_x) / 2, height / 2 - m.px(10),
                "KEINE QUELLE VERBUNDEN",
                size=12, weight="bold", fill=theme.TEXT_MUTED,
                anchor="center",
            )
            self.text(
                (icon_w + info_x) / 2, height / 2 + m.px(10),
                "Demo-Modus: python run_cdj.py --demo",
                size=8, fill=theme.TEXT_MUTED, anchor="center",
            )
            return

        index = display.source_index if display else 0
        index = max(0, min(index, len(sources) - 1))
        confirming = bool(display.source_confirming) if display else False
        active_id = display.source_id if display else ""

        row_h = m.px(30)
        for position, info in enumerate(sources):
            y0 = header_h + position * row_h
            y1 = y0 + row_h
            selected = position == index
            self._render_icon(info, 0, y0, icon_w, y1, selected)
            self._render_row(
                info, icon_w, y0, info_x, y1,
                selected=selected,
                confirming=selected and confirming,
                active=info.source_id == active_id,
            )
            self._rows.append(((0, y0, info_x, y1), position))

        self._render_info(sources[index], info_x, header_h, width, height)

    # ------------------------------------------------------------------

    def _render_icon(
        self,
        info: SourceInfo,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        selected: bool,
    ) -> None:
        m = self.metrics
        colour = KIND_COLORS.get(info.kind, theme.TEXT_DIM)
        pad = m.px(4)
        self.box(
            x0 + pad, y0 + pad, x1 - pad, y1 - pad,
            fill=colour if selected else theme.PANEL,
            outline=colour,
        )
        # Playernummer oben links im Symbol - wie am Geraet (S. 18).
        if info.player_number:
            self.text(
                x0 + pad + m.px(3), y0 + pad + m.px(2),
                str(info.player_number),
                size=6, weight="bold",
                fill=theme.BG if selected else colour,
            )
        self.text(
            (x0 + x1) / 2, (y0 + y1) / 2 + m.px(3), info.kind.value,
            size=8, weight="bold",
            fill=theme.BG if selected else colour, anchor="center",
        )

    def _render_row(
        self,
        info: SourceInfo,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        selected: bool,
        confirming: bool,
        active: bool,
    ) -> None:
        m = self.metrics
        if selected:
            self.box(
                x0, y0, x1, y1,
                fill=theme.PANEL_ACTIVE if confirming else theme.PANEL_HI,
                outline="",
            )
            self.create_line(
                m.snap(x0 + 1), y0, m.snap(x0 + 1), y1,
                fill=theme.ACCENT, width=2,
            )
        self.text(
            x0 + m.px(10), (y0 + y1) / 2, info.name,
            size=10, weight="bold" if selected else "normal",
            fill=theme.ACCENT if selected else theme.TEXT_DIM, anchor="w",
        )
        if active:
            # "Loaded" wie am Geraet: diese Quelle bedient den Browser.
            self.text(
                x1 - m.px(28), (y0 + y1) / 2, "Loaded",
                size=8, fill=theme.GREEN, anchor="e",
            )
        if selected:
            icons.chevron_right(
                self, x1 - m.px(12), (y0 + y1) / 2, m.px(9), theme.ACCENT
            )

    def _wrapped(
        self, content: str, x0: float, y: float, x1: float, colour: str
    ) -> float:
        """Mehrzeiligen Text setzen und die neue Unterkante liefern.

        Hinweise und Fehlermeldungen sind ganze Saetze und passen nicht in
        eine Zeile. Sie werden umbrochen statt abgeschnitten - eine halb
        sichtbare Fehlermeldung ist keine.
        """
        item = self.create_text(
            x0, y, text=content, fill=colour, anchor="nw",
            font=self.metrics.font(7), width=max(1, x1 - x0),
        )
        box = self.bbox(item)
        return box[3] if box else y

    def _render_info(
        self,
        info: SourceInfo,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
    ) -> None:
        m = self.metrics
        pad = m.px(12)
        row_h = m.px(19)
        y = y0 + m.px(8)
        for label, field in INFO_ROWS:
            self.text(
                x0 + pad, y, label, size=8, fill=theme.TEXT_DIM,
            )
            self.text(
                x1 - pad, y, _info_value(info, field),
                size=9, weight="bold", fill=theme.TEXT, anchor="ne",
                mono=True,
            )
            self.create_line(
                x0 + pad, m.snap(y + row_h - m.px(4)),
                x1 - pad, m.snap(y + row_h - m.px(4)),
                fill=theme.LINE_SUBTLE,
            )
            y += row_h

        # Warnung vor dem Hinweis: ein Fehler ist wichtiger als der Pfad.
        if info.warning:
            y = self._wrapped(
                info.warning, x0 + pad, y + m.px(4), x1 - pad, theme.YELLOW
            )
        if info.note:
            y = self._wrapped(
                info.note, x0 + pad, y + m.px(2), x1 - pad, theme.TEXT_MUTED
            )
        y += m.px(4)

        # Am Geraet folgen hier BACKGROUND COLOR und MY SETTINGS LOAD. Beides
        # ist nicht umgesetzt - die Flaechen bleiben deshalb ausdruecklich
        # abgeblendet statt eine Funktion vorzutaeuschen.
        for label in ("BACKGROUND COLOR", "MY SETTINGS LOAD"):
            box_y = y + m.px(10)
            self.box(
                x0 + pad, box_y, x1 - pad, box_y + m.px(22),
                fill=theme.PANEL, outline=theme.LINE_SUBTLE,
            )
            self.text(
                (x0 + x1) / 2, box_y + m.px(11), label,
                size=7, fill=theme.TEXT_MUTED, anchor="center",
            )
            y = box_y + m.px(22)
        self.text(
            x0 + pad, y + m.px(8), "nicht umgesetzt",
            size=7, fill=theme.TEXT_MUTED,
        )
