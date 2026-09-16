"""Touch-Panels: BEAT LOOP, BEAT JUMP, KEY SHIFT.

Am Geraet oeffnet ein Touch auf die jeweilige Schaltflaeche in der Kopfzeile
ein Panel ueber dem unteren Teil der Wellenform (Handbuch S. 58, 66, 74). Ein
zweiter Touch schliesst es.

Die Panels loesen **echte** Kommandos aus. Sie halten keine eigene Loop- oder
Jump-Logik - die liegt in der Deck-Engine und im Audio-Callback.
"""

from __future__ import annotations

import tkinter as tk

from ..deck.commands import CommandType, command
from ..deck.display_state import (
    BEAT_JUMP_VALUES,
    BEAT_LOOP_VALUES,
    CdjDisplayState,
    TouchPanel,
)
from . import icons, theme
from .base import Region


def beat_label(beats: float) -> str:
    """``0.25`` -> ``1/4``, ``16`` -> ``16``."""
    if beats >= 1:
        return f"{int(beats)}"
    return f"1/{int(round(1 / beats))}"


class Panel(Region):
    """Basis der Panels. Zeichnet Rahmen und Titel, kennt Trefferflaechen."""

    background = theme.PANEL

    #: Welches Panel dies ist.
    panel = TouchPanel.NONE
    title = ""

    def __init__(self, master: tk.Misc, send, deck_id: int) -> None:
        super().__init__(master, send)
        self.deck_id = deck_id
        self.display: CdjDisplayState | None = None
        self._hits: list[tuple[tuple[float, float, float, float], object]] = []
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    # ------------------------------------------------------------------

    def set_display(self, display: CdjDisplayState) -> None:
        # Kein blindes Ungueltigmachen: was sichtbar wird, steht in
        # ``signature`` - sonst zeichnete jeder Bereich in jedem Bild neu.
        self.display = display

    def _hit(self, x: float, y: float) -> object | None:
        for (x0, y0, x1, y1), payload in self._hits:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return payload
        return None

    def _on_press(self, event: tk.Event) -> None:
        payload = self._hit(event.x, event.y)
        if payload is not None:
            self.on_activate(payload, pressed=True)

    def _on_release(self, event: tk.Event) -> None:
        payload = self._hit(event.x, event.y)
        if payload is not None:
            self.on_activate(payload, pressed=False)

    def on_activate(self, payload: object, *, pressed: bool) -> None:
        ...

    # ------------------------------------------------------------------

    def _frame(self) -> tuple[float, float]:
        """Rahmen und Titel zeichnen. Rueckgabe: nutzbarer Bereich (oben, unten)."""
        m = self.metrics
        self.box(0, 0, self.w, self.h, fill=theme.PANEL, outline="")
        # Nur eine Kante oben statt eines Rahmens um alles.
        self.create_line(
            0, m.snap(0), self.w, m.snap(0), fill=theme.ACCENT, width=1
        )
        header = m.px(15)
        self.text(
            m.px(8), header / 2, self.title,
            size=8, weight="bold", fill=theme.ACCENT, anchor="w",
        )
        return (header, self.h - m.px(3))

    def _buttons(
        self,
        values: tuple,
        top: float,
        bottom: float,
        labels: tuple[str, ...],
        actives: tuple[bool, ...],
        colors: tuple[str, ...] | None = None,
    ) -> None:
        """Eine Reihe gleich breiter Touch-Ziele zeichnen und registrieren."""
        m = self.metrics
        pad = m.px(6)
        gap = m.px(4)
        count = len(values)
        if count == 0:
            return
        usable = self.w - 2 * pad - gap * (count - 1)
        button_w = usable / count
        for index, value in enumerate(values):
            x0 = pad + index * (button_w + gap)
            x1 = x0 + button_w
            active = actives[index]
            colour = (
                colors[index] if colors else theme.ACCENT
            )
            # Aktiv heisst gefuellt, inaktiv eine ruhige Flaeche. Kein
            # Rahmen je Taste - sonst wird das Panel ein Gitter.
            self.box(
                x0, top, x1, bottom,
                fill=colour if active else theme.PANEL_HI,
                outline="",
            )
            self.text(
                (x0 + x1) / 2, (top + bottom) / 2, labels[index],
                size=12, weight="bold",
                fill=theme.BG if active else theme.TEXT,
                anchor="center",
            )
            self._hits.append(((x0, top, x1, bottom), value))


class BeatLoopPanel(Panel):
    """Beat Loop: 1/4 bis 32 Beats (Handbuch S. 58).

    Ein Touch startet den Loop beatgrid-synchron; die Loop-Engine des Decks
    erzeugt ihn. Ein Touch auf den aktiven Wert verlaesst den Loop.
    """

    panel = TouchPanel.BEAT_LOOP
    title = "BEAT LOOP"

    def on_activate(self, payload: object, *, pressed: bool) -> None:
        if not pressed:
            return
        beats = float(payload)  # type: ignore[arg-type]
        display = self.display
        loop = display.deck.loop if display else None
        if (
            loop is not None
            and loop.active
            and loop.beats is not None
            and abs(loop.beats - beats) < 1e-6
        ):
            # Derselbe Wert erneut: Loop verlassen, wie am Geraet.
            self.send(
                command(CommandType.RELOOP_EXIT, self.deck_id, "TOUCH")
            )
            return
        self.send(
            command(
                CommandType.BEAT_LOOP, self.deck_id, "TOUCH", beats=beats
            )
        )

    def render(self, state) -> None:
        self._hits.clear()
        top, bottom = self._frame()
        display = self.display
        loop = display.deck.loop if display else None
        active_beats = (
            loop.beats if loop is not None and loop.active else None
        )
        values = BEAT_LOOP_VALUES
        self._buttons(
            values,
            top + self.metrics.px(3),
            bottom,
            labels=tuple(beat_label(v) for v in values),
            actives=tuple(
                active_beats is not None and abs(active_beats - v) < 1e-6
                for v in values
            ),
            colors=tuple(theme.LOOP_EDGE for _ in values),
        )
        if loop is not None and loop.active:
            self.text(
                self.w - self.metrics.px(8), self.metrics.px(8),
                f"LOOP {loop.label()}",
                size=8, weight="bold", fill=theme.LOOP_EDGE, anchor="e",
            )


class BeatJumpPanel(Panel):
    """Beat Jump: Laenge waehlen und in beide Richtungen springen.

    Am Geraet zeigt das Panel Paare mit ◀◀/▶▶ (Handbuch S. 66). Hier eine
    Reihe mit Laengen plus zwei Richtungstasten - das ist auf einem
    7-Zoll-Touchscreen treffsicherer und braucht keine Seitenumschaltung.
    """

    panel = TouchPanel.BEAT_JUMP
    title = "BEAT JUMP"

    def __init__(self, master: tk.Misc, send, deck_id: int) -> None:
        super().__init__(master, send, deck_id)
        self.beats = 16.0

    def on_activate(self, payload: object, *, pressed: bool) -> None:
        if not pressed:
            return
        if isinstance(payload, tuple):
            direction = int(payload[1])
            self.send(
                command(
                    CommandType.BEAT_JUMP, self.deck_id, "TOUCH",
                    direction=direction, beats=self.beats,
                )
            )
            return
        self.beats = float(payload)  # type: ignore[arg-type]
        self.invalidate()

    def render(self, state) -> None:
        m = self.metrics
        self._hits.clear()
        top, bottom = self._frame()
        top += m.px(3)
        middle = top + (bottom - top) * 0.52

        values = BEAT_JUMP_VALUES
        self._buttons(
            values,
            top,
            middle - m.px(2),
            labels=tuple(beat_label(v) for v in values),
            actives=tuple(abs(self.beats - v) < 1e-6 for v in values),
        )

        # Richtungstasten
        pad = m.px(6)
        gap = m.px(6)
        half = (self.w - 2 * pad - gap) / 2
        for index, (label, direction) in enumerate(
            (("BACK", -1), ("FWD", +1))
        ):
            x0 = pad + index * (half + gap)
            x1 = x0 + half
            self.box(
                x0, middle + m.px(2), x1, bottom,
                fill=theme.PANEL_HI, outline="",
            )
            centre_y = (middle + bottom) / 2 + m.px(1)
            symbol = m.px(11)
            if direction < 0:
                icons.jump_back(
                    self, x0 + m.px(20), centre_y, symbol, theme.TEXT
                )
                text_x = (x0 + x1) / 2 + m.px(10)
            else:
                icons.jump_forward(
                    self, x1 - m.px(20), centre_y, symbol, theme.TEXT
                )
                text_x = (x0 + x1) / 2 - m.px(10)
            self.text(
                text_x, centre_y, label,
                size=11, weight="bold", fill=theme.TEXT, anchor="center",
            )
            self._hits.append(
                ((x0, middle + m.px(2), x1, bottom), ("jump", direction))
            )

        self.text(
            self.w - m.px(8), m.px(8),
            f"{beat_label(self.beats)} BEATS",
            size=8, weight="bold", fill=theme.ACCENT, anchor="e",
        )


class KeyShiftPanel(Panel):
    """Key Shift: Halbton hoch, runter, zurueck (Handbuch S. 74).

    **Kategorie B**: Der Zustand wird gefuehrt und angezeigt, die Tonhoehe im
    Audio aendert sich noch nicht. Das steht auch im Panel, damit niemand
    einen Effekt erwartet, den es nicht gibt.
    """

    panel = TouchPanel.KEY_SHIFT
    title = "KEY SHIFT"

    def on_activate(self, payload: object, *, pressed: bool) -> None:
        if not pressed:
            return
        action = str(payload)
        if action == "reset":
            self.send(
                command(CommandType.KEY_SHIFT_RESET, self.deck_id, "TOUCH")
            )
            return
        self.send(
            command(
                CommandType.KEY_SHIFT, self.deck_id, "TOUCH",
                semitones=1 if action == "up" else -1,
            )
        )

    def render(self, state) -> None:
        m = self.metrics
        self._hits.clear()
        top, bottom = self._frame()
        top += m.px(3)

        deck = self.display.deck if self.display else None
        shift = deck.key_shift if deck else 0
        base_key = deck.key if deck else ""

        pad = m.px(6)
        gap = m.px(6)
        button_w = m.px(78)

        # Minus
        self.box(
            pad, top, pad + button_w, bottom,
            fill=theme.PANEL_HI, outline="",
        )
        icons.minus(
            self, pad + button_w / 2, (top + bottom) / 2,
            m.px(16), theme.TEXT,
        )
        self._hits.append(((pad, top, pad + button_w, bottom), "down"))

        # Anzeige
        centre_x0 = pad + button_w + gap
        centre_x1 = self.w - pad - 2 * button_w - 2 * gap
        self.box(
            centre_x0, top, centre_x1, bottom,
            fill=theme.BG, outline="",
        )
        self.text(
            (centre_x0 + centre_x1) / 2, top + m.px(13),
            f"{shift:+d}" if shift else "0",
            size=18, weight="bold",
            fill=theme.YELLOW if shift else theme.TEXT_DIM,
            anchor="center", mono=True,
        )
        self.text(
            (centre_x0 + centre_x1) / 2, bottom - m.px(11),
            f"{base_key or '—'}   ·   Halbtoene",
            size=8, fill=theme.TEXT_DIM, anchor="center",
        )

        # Plus
        plus_x0 = centre_x1 + gap
        self.box(
            plus_x0, top, plus_x0 + button_w, bottom,
            fill=theme.PANEL_HI, outline=theme.ACCENT,
        )
        self.text(
            plus_x0 + button_w / 2, (top + bottom) / 2, "+",
            size=20, weight="bold", fill=theme.TEXT, anchor="center",
        )
        self._hits.append(((plus_x0, top, plus_x0 + button_w, bottom), "up"))

        # Reset
        reset_x0 = plus_x0 + button_w + gap
        self.box(
            reset_x0, top, self.w - pad, bottom,
            fill=theme.PANEL_HI, outline=theme.BORDER,
        )
        self.text(
            (reset_x0 + self.w - pad) / 2, (top + bottom) / 2, "RESET",
            size=10, weight="bold", fill=theme.TEXT, anchor="center",
        )
        self._hits.append(((reset_x0, top, self.w - pad, bottom), "reset"))

        # Ehrlicher Hinweis auf die fehlende Tonhoehenverschiebung.
        self.text(
            self.w - m.px(8), m.px(8),
            "Zustand aktiv · Pitch-Shifting im Audio fehlt",
            size=7, fill=theme.ORANGE, anchor="e",
        )


#: Panel-Klassen nach Typ.
PANEL_CLASSES: dict[TouchPanel, type[Panel]] = {
    TouchPanel.BEAT_LOOP: BeatLoopPanel,
    TouchPanel.BEAT_JUMP: BeatJumpPanel,
    TouchPanel.KEY_SHIFT: KeyShiftPanel,
}
