"""Virtuelles Bedienfeld.

Baut aus der zentralen Komponentenliste ein anklickbares Bedienfeld auf.
Das Panel kennt ausschliesslich ``VirtualSource`` - keine CDJ-Funktion, keinen
Controller.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from ..core import controls, ids
from ..core.button_customization import ButtonCustomizationStore
from ..core.model import Control, ControlType, Shape, Status
from ..sources.virtual import VirtualSource
from . import theme
from .theme import TRANSFORM
from .widgets import (
    ButtonWidget,
    EncoderWidget,
    FaderWidget,
    JogWidget,
    LedWidget,
    PotWidget,
    SwitchWidget,
    Widget,
)

#: IDs, die von einem anderen Widget mitbedient werden und deshalb kein
#: eigenes Widget bekommen.
_MERGED_INTO_OTHER_WIDGET = frozenset({ids.BROWSE_PRESS, ids.JOG_TOUCH})


class PanelView(tk.Frame):
    """Das virtuelle Bedienfeld als Tk-Frame."""

    def __init__(
        self,
        master: tk.Misc,
        source: VirtualSource,
        *,
        on_hover: Callable[[Control | None], None] | None = None,
        customizations: ButtonCustomizationStore | None = None,
        on_edit: Callable[[Control], None] | None = None,
    ) -> None:
        super().__init__(master, bg=theme.BG_WINDOW)
        self.source = source
        self.on_hover = on_hover
        self.customizations = customizations
        self.on_edit = on_edit
        self.edit_mode = False
        self.tf = TRANSFORM

        width, height = theme.PANEL_CANVAS_SIZE
        self.canvas = tk.Canvas(
            self, width=width, height=height,
            bg=theme.BG_WINDOW, highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self.widgets: list[Widget] = []
        self._widget_by_id: dict[str, Widget] = {}
        self._active: Widget | None = None
        self._hovered: Widget | None = None
        self._unsubscribe_customizations = (
            customizations.subscribe(self._on_customization_changed)
            if customizations is not None
            else None
        )

        self._draw_chassis()
        self._build_widgets()
        self._bind_events()
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.refresh()

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _draw_chassis(self) -> None:
        tf = self.tf
        c = self.canvas

        # Gehaeuseplatte
        c.create_rectangle(
            tf.x(96), tf.y(130), tf.x(500), tf.y(680),
            fill=theme.BG_PANEL, outline=theme.BG_PANEL_EDGE, width=2,
        )

        # Displaymodul (kommt in einem spaeteren Schritt)
        c.create_rectangle(
            tf.x(183), tf.y(78), tf.x(412), tf.y(236),
            fill=theme.BG_SCREEN, outline=theme.BG_PANEL_EDGE, width=2,
        )
        c.create_text(
            tf.x(297), tf.y(160), text="DISPLAY\n(spaeterer Schritt)",
            fill="#4b525a", font=theme.FONT_LABEL, justify="center",
        )

        # Kartenschacht / Port oben links (kein Eingang)
        c.create_rectangle(
            tf.x(118), tf.y(143), tf.x(168), tf.y(172),
            fill=theme.BG_PANEL_EDGE, outline=theme.BTN_EDGE,
        )
        c.create_text(
            tf.x(143), tf.y(157), text="USB / SD",
            fill=theme.FG_DIM, font=theme.FONT_TINY,
        )

        # Schriftzug
        c.create_rectangle(
            tf.x(193), tf.y(612), tf.x(400), tf.y(664),
            fill="#101216", outline="",
        )
        c.create_text(
            tf.x(296), tf.y(638), text="Fischertechnik",
            fill="#f2f4f7", font=("Segoe UI Black", 15, "italic"),
        )

        # Gruppenhinweise, damit die Kurzbeschriftungen zuordenbar bleiben.
        captions = (
            (300, 252, "PERFORMANCE PADS A-H", "s"),
            (143, 556, "TRACK SEARCH / SEARCH", "n"),
        )
        for x, y, text, anchor in captions:
            c.create_text(
                tf.x(x), tf.y(y), text=text, anchor=anchor,
                fill=theme.FG_DIM, font=theme.FONT_TINY,
            )

        if self.edit_mode:
            c.create_rectangle(
                tf.x(108), tf.y(45), tf.x(488), tf.y(68),
                fill="#5a401e", outline=theme.AMBER, width=1,
            )
            c.create_text(
                tf.x(298), tf.y(56.5),
                text="BEARBEITUNGSMODUS - BUTTON ANKLICKEN",
                fill="#ffd08a", font=theme.FONT_HEAD,
            )

    def _build_widgets(self) -> None:
        for control in controls.CONTROL_LIST:
            if control.id in _MERGED_INTO_OTHER_WIDGET:
                continue
            if self.customizations is not None:
                control = self.customizations.display_control(control)
            widget = self._make_widget(control)
            if widget is None:
                continue
            widget.draw(self.canvas)
            self._draw_label(control)
            self.widgets.append(widget)
            for control_id in widget.control_ids:
                self._widget_by_id[control_id] = widget

        # Kleine Elemente zuerst treffen, damit innenliegende Flaechen
        # (z. B. Jog-Platte im Jog-Rand) erreichbar bleiben.
        self.widgets.sort(key=lambda w: w.area)

    def _make_widget(self, control: Control) -> Widget | None:
        tf = self.tf
        if control.type is ControlType.DIGITAL_BUTTON:
            if self.customizations is None:
                return ButtonWidget(control, tf)
            led = self.customizations.led_color(control)
            return ButtonWidget(
                control,
                tf,
                led_colors=theme.BUTTON_LED_COLORS.get(led.value),
                light_led_on_press=(
                    self.customizations.has_led_override(control.id)
                    and control.has_led
                ),
            )
        if control.type is ControlType.ANALOG_FADER:
            return FaderWidget(control, tf)
        if control.type is ControlType.ANALOG_POT:
            return PotWidget(control, tf)
        if control.type is ControlType.ENCODER:
            press = (
                controls.CONTROLS.get(ids.BROWSE_PRESS)
                if control.id == ids.BROWSE_ROTATE
                else None
            )
            return EncoderWidget(control, tf, press_control=press)
        if control.type is ControlType.SWITCH:
            return SwitchWidget(control, tf)
        if control.type is ControlType.JOG:
            return JogWidget(control, tf, controls.get(ids.JOG_TOUCH))
        if control.type is ControlType.LED:
            return LedWidget(control, tf)
        return None

    def _draw_label(self, control: Control) -> None:
        if control.type is ControlType.JOG:
            return
        if control.shape is Shape.BIG_ROUND:
            return
        text = control.short or control.id
        if not text:
            return
        color = (
            theme.GROUP_COLORS["UNRESOLVED"]
            if control.status is Status.UNRESOLVED
            else theme.FG_LABEL
        )
        cx = self.tf.x(control.x)
        cy = self.tf.y(control.y)
        half_w = self.tf.s(control.w) / 2
        half_h = self.tf.s(control.h) / 2
        gap = 5

        if control.label_side == "above":
            x, y, anchor = cx, cy - half_h - gap, "s"
        elif control.label_side == "left":
            x, y, anchor = cx - half_w - gap, cy, "e"
        elif control.label_side == "right":
            x, y, anchor = cx + half_w + gap, cy, "w"
        else:
            x, y, anchor = cx, cy + half_h + gap, "n"

        self.canvas.create_text(
            x, y, text=text, fill=color, anchor=anchor, font=theme.FONT_TINY
        )

    # ------------------------------------------------------------------
    # Ereignisse
    # ------------------------------------------------------------------

    def _bind_events(self) -> None:
        c = self.canvas
        c.bind("<Button-1>", lambda e: self._on_button(e, 1))
        c.bind("<Button-3>", lambda e: self._on_button(e, 3))
        c.bind("<B1-Motion>", self._on_motion_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Motion>", self._on_hover_motion)
        c.bind("<Leave>", self._on_leave)
        c.bind("<MouseWheel>", self._on_wheel)

    def _find(self, x: float, y: float) -> Widget | None:
        for widget in self.widgets:
            if widget.hit(x, y):
                return widget
        return None

    def _on_button(self, event: tk.Event, button: int) -> None:
        widget = self._find(event.x, event.y)
        if widget is None:
            return
        if self.edit_mode:
            if isinstance(widget, ButtonWidget) and self.on_edit is not None:
                self.on_edit(widget.control)
            return
        if button == 1:
            self._active = widget
        widget.on_press(self.source, event.x, event.y, button)
        self.refresh()

    def _on_motion_drag(self, event: tk.Event) -> None:
        if self.edit_mode:
            return
        if self._active is None:
            return
        self._active.on_drag(self.source, event.x, event.y)
        self.refresh()

    def _on_release(self, event: tk.Event) -> None:
        if self.edit_mode:
            self._active = None
            return
        if self._active is None:
            return
        widget, self._active = self._active, None
        widget.on_release(self.source, event.x, event.y)
        self.refresh()

    def _on_wheel(self, event: tk.Event) -> None:
        if self.edit_mode:
            return
        widget = self._find(event.x, event.y)
        if widget is None:
            return
        notches = 1 if event.delta > 0 else -1
        if isinstance(widget, JogWidget):
            boost = 5 if event.state & 0x0004 else 1  # Strg = schneller
            widget.on_wheel(self.source, notches, boost)
        else:
            widget.on_wheel(self.source, notches)
        self.refresh()

    def _on_hover_motion(self, event: tk.Event) -> None:
        widget = self._find(event.x, event.y)
        if widget is self._hovered:
            return
        if self._hovered is not None:
            self._hovered.hovered = False
        self._hovered = widget
        if widget is not None:
            widget.hovered = True
        if self.on_hover is not None:
            self.on_hover(widget.control if widget else None)
        self.refresh()

    def _on_leave(self, event: tk.Event) -> None:
        if self._hovered is not None:
            self._hovered.hovered = False
            self._hovered = None
        if self.on_hover is not None:
            self.on_hover(None)
        self.refresh()

    # ------------------------------------------------------------------
    # Darstellung aktualisieren
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        state = self.source.input_layer.state
        for widget in self.widgets:
            widget.refresh(self.canvas, state)

    def release_all_latches(self) -> None:
        """Alle gerasteten Taster loesen."""
        for widget in self.widgets:
            if isinstance(widget, ButtonWidget) and widget.latched:
                self.source.release(widget.control.id)
                widget.latched = False
        self.refresh()

    def set_edit_mode(self, enabled: bool) -> None:
        """Bedienung sperren und Klicks stattdessen an den Editor geben."""
        enabled = bool(enabled)
        if enabled == self.edit_mode:
            return
        if enabled:
            if self._active is not None:
                widget, self._active = self._active, None
                widget.on_release(self.source, widget.cx, widget.cy)
            self.release_all_latches()
        self.edit_mode = enabled
        self._rebuild()

    def _on_customization_changed(self, _control_id: str | None) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        self._active = None
        self._hovered = None
        self.widgets.clear()
        self._widget_by_id.clear()
        self.canvas.delete("all")
        self._draw_chassis()
        self._build_widgets()
        if self.on_hover is not None:
            self.on_hover(None)
        self.refresh()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is not self or self._unsubscribe_customizations is None:
            return
        self._unsubscribe_customizations()
        self._unsubscribe_customizations = None
