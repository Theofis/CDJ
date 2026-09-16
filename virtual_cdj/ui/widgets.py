"""Virtuelle Bedienelemente.

Jedes Widget zeichnet sich auf ein Tk-Canvas und meldet Bedienung
ausschliesslich an ``VirtualSource``. Kein Widget kennt den CDJ-Controller.
"""

from __future__ import annotations

import math
import tkinter as tk

from ..core.model import Control, Shape, Status
from ..core.state import InputState
from ..sources.virtual import VirtualSource
from . import theme
from .theme import Transform


class Widget:
    """Basis aller virtuellen Bedienelemente."""

    #: Alle IDs, die dieses Widget bedient (fuer Hover-Anzeige und LEDs).
    control_ids: tuple[str, ...] = ()

    def __init__(self, control: Control, tf: Transform) -> None:
        self.control = control
        self.tf = tf
        self.cx = tf.x(control.x)
        self.cy = tf.y(control.y)
        self.w = tf.s(control.w)
        self.h = tf.s(control.h)
        self.control_ids = (control.id,)
        self.items: list[int] = []
        self.hovered = False

    # -- Geometrie ---------------------------------------------------------

    @property
    def area(self) -> float:
        return self.w * self.h

    def hit(self, x: float, y: float) -> bool:
        if self.control.shape in (Shape.ROUND, Shape.BIG_ROUND):
            r = max(self.w, self.h) / 2
            return (x - self.cx) ** 2 + (y - self.cy) ** 2 <= r * r
        return (
            abs(x - self.cx) <= self.w / 2 and abs(y - self.cy) <= self.h / 2
        )

    # -- Zeichnen ----------------------------------------------------------

    def draw(self, canvas: tk.Canvas) -> None:  # pragma: no cover - GUI
        raise NotImplementedError

    def refresh(self, canvas: tk.Canvas, state: InputState) -> None:
        """Darstellung an den aktuellen Zustand anpassen."""

    # -- Interaktion -------------------------------------------------------

    def on_press(self, src: VirtualSource, x: float, y: float, button: int) -> None:
        ...

    def on_drag(self, src: VirtualSource, x: float, y: float) -> None:
        ...

    def on_release(self, src: VirtualSource, x: float, y: float) -> None:
        ...

    def on_wheel(self, src: VirtualSource, notches: int) -> None:
        ...

    # -- Hilfen ------------------------------------------------------------

    def _bbox(self, inset: float = 0.0) -> tuple[float, float, float, float]:
        return (
            self.cx - self.w / 2 + inset,
            self.cy - self.h / 2 + inset,
            self.cx + self.w / 2 - inset,
            self.cy + self.h / 2 - inset,
        )

    def _outline(self) -> str:
        if self.hovered:
            return theme.BTN_HOVER_EDGE
        if self.control.status is Status.UNRESOLVED:
            return theme.GROUP_COLORS["UNRESOLVED"]
        return theme.BTN_EDGE


# --------------------------------------------------------------------------
# Taster
# --------------------------------------------------------------------------


class ButtonWidget(Widget):
    """Momentan-Taster mit PRESS/RELEASE.

    Kein Toggle. Zum Testen von Tastenkombinationen kann ein Taster mit der
    rechten Maustaste gerastet ("gehalten") werden; ein Linksklick loest die
    Rastung wieder.
    """

    def __init__(
        self,
        control: Control,
        tf: Transform,
        *,
        led_colors: tuple[str, str] | None = None,
        light_led_on_press: bool = False,
    ) -> None:
        super().__init__(control, tf)
        self.latched = False
        self._skip_release = False
        self._body: int | None = None
        self._led: int | None = None
        self._led_on, self._led_off = led_colors or (
            theme.GREEN, theme.GREEN_DIM
        )
        self._light_led_on_press = light_led_on_press

    # -- Darstellung -------------------------------------------------------

    def _base_color(self) -> str:
        group = self.control.group
        if group == "LOOP":
            return theme.AMBER
        if group == "PLAY_MODE":
            return theme.RED
        return theme.BTN_FACE

    def _active_color(self) -> str:
        if self.control.group == "LOOP":
            return theme.AMBER_ACTIVE
        return theme.BTN_FACE_ACTIVE

    def draw(self, canvas: tk.Canvas) -> None:
        x0, y0, x1, y1 = self._bbox()
        if self.control.shape in (Shape.ROUND, Shape.BIG_ROUND):
            self._body = canvas.create_oval(
                x0, y0, x1, y1, fill=self._base_color(),
                outline=self._outline(), width=1,
            )
        else:
            self._body = canvas.create_rectangle(
                x0, y0, x1, y1, fill=self._base_color(),
                outline=self._outline(), width=1,
            )
        self.items.append(self._body)

        if self.control.shape is Shape.BIG_ROUND:
            label = self.control.short or (
                "CUE" if self.control.id == "CUE" else "▶‖"
            )
            self.items.append(
                canvas.create_text(
                    self.cx, self.cy, text=label,
                    fill="#2a2d31", font=theme.FONT_HEAD,
                )
            )

        if self.control.has_led:
            r = max(2.0, self.tf.s(2.2))
            self._led = canvas.create_oval(
                x1 - r * 2 - 1, y0 + 1, x1 - 1, y0 + r * 2 + 1,
                fill=self._led_off, outline="",
            )
            self.items.append(self._led)

    def refresh(self, canvas: tk.Canvas, state: InputState) -> None:
        if self._body is None:
            return
        pressed = state.is_pressed(self.control.id)
        if pressed and self.latched:
            fill = theme.BTN_FACE_LATCHED
        elif pressed:
            fill = self._active_color()
        else:
            fill = self._base_color()
        canvas.itemconfigure(self._body, fill=fill, outline=self._outline())
        if self._led is not None:
            led_on = state.led(self.control.id) or (
                self._light_led_on_press and pressed
            )
            canvas.itemconfigure(
                self._led,
                fill=self._led_on if led_on else self._led_off,
            )

    # -- Interaktion -------------------------------------------------------

    def on_press(self, src: VirtualSource, x: float, y: float, button: int) -> None:
        if button == 3:
            if self.latched:
                src.release(self.control.id)
                self.latched = False
            else:
                src.press(self.control.id)
                self.latched = True
            return

        if self.latched:
            src.release(self.control.id)
            self.latched = False
            self._skip_release = True
            return
        src.press(self.control.id)

    def on_release(self, src: VirtualSource, x: float, y: float) -> None:
        if self._skip_release:
            self._skip_release = False
            return
        if not self.latched:
            src.release(self.control.id)


# --------------------------------------------------------------------------
# Fader
# --------------------------------------------------------------------------


class FaderWidget(Widget):
    """Senkrechter Fader, Wert 0.0 (unten) bis 1.0 (oben)."""

    def __init__(self, control: Control, tf: Transform) -> None:
        super().__init__(control, tf)
        self.top = self.cy - self.h / 2
        self.bottom = self.cy + self.h / 2
        self._knob: int | None = None
        self._readout: int | None = None

    def draw(self, canvas: tk.Canvas) -> None:
        x0, y0, x1, y1 = self._bbox()
        self.items.append(
            canvas.create_rectangle(
                x0, y0, x1, y1, fill=theme.BG_PANEL_EDGE,
                outline=theme.BTN_EDGE,
            )
        )
        self.items.append(
            canvas.create_rectangle(
                self.cx - 2, y0 + 4, self.cx + 2, y1 - 4,
                fill=theme.FADER_TRACK, outline="",
            )
        )
        kw = self.w * 0.8
        kh = max(8.0, self.tf.s(10))
        self._knob = canvas.create_rectangle(
            self.cx - kw / 2, self.cy - kh / 2,
            self.cx + kw / 2, self.cy + kh / 2,
            fill=theme.FADER_KNOB, outline=theme.BTN_EDGE,
        )
        self.items.append(self._knob)
        self._readout = canvas.create_text(
            self.cx, y1 + 9, text="0.000",
            fill=theme.FG_DIM, font=theme.FONT_SMALL,
        )
        self.items.append(self._readout)

    def refresh(self, canvas: tk.Canvas, state: InputState) -> None:
        if self._knob is None:
            return
        value = state.analog(self.control.id)
        kh = max(8.0, self.tf.s(10))
        usable_top = self.top + 4 + kh / 2
        usable_bottom = self.bottom - 4 - kh / 2
        y = usable_bottom - value * (usable_bottom - usable_top)
        kw = self.w * 0.8
        canvas.coords(
            self._knob,
            self.cx - kw / 2, y - kh / 2, self.cx + kw / 2, y + kh / 2,
        )
        canvas.itemconfigure(self._readout, text=f"{value:.3f}")

    def _value_at(self, y: float) -> float:
        kh = max(8.0, self.tf.s(10))
        usable_top = self.top + 4 + kh / 2
        usable_bottom = self.bottom - 4 - kh / 2
        span = usable_bottom - usable_top
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (usable_bottom - y) / span))

    def on_press(self, src: VirtualSource, x: float, y: float, button: int) -> None:
        src.set_analog(self.control.id, self._value_at(y))

    def on_drag(self, src: VirtualSource, x: float, y: float) -> None:
        src.set_analog(self.control.id, self._value_at(y))

    def on_wheel(self, src: VirtualSource, notches: int) -> None:
        current = src.input_layer.state.analog(self.control.id)
        src.set_analog(self.control.id, current + notches * 0.01)


# --------------------------------------------------------------------------
# Potentiometer (Drehregler)
# --------------------------------------------------------------------------


class PotWidget(Widget):
    """Drehpotentiometer 0.0 - 1.0.

    Bedienung: senkrecht ziehen oder Mausrad. Der Zeiger deckt 270 Grad ab.
    """

    def __init__(self, control: Control, tf: Transform) -> None:
        super().__init__(control, tf)
        self._pointer: int | None = None
        self._drag_start_y: float | None = None
        self._drag_start_value = 0.0

    def draw(self, canvas: tk.Canvas) -> None:
        x0, y0, x1, y1 = self._bbox()
        self.items.append(
            canvas.create_oval(
                x0, y0, x1, y1, fill=theme.BTN_FACE, outline=self._outline()
            )
        )
        self._pointer = canvas.create_line(
            self.cx, self.cy, self.cx, y0 + 2,
            fill=theme.ACCENT, width=2,
        )
        self.items.append(self._pointer)

    def refresh(self, canvas: tk.Canvas, state: InputState) -> None:
        if self._pointer is None:
            return
        value = state.analog(self.control.id)
        angle = math.radians(-135 + 270 * value)
        r = max(self.w, self.h) / 2 - 2
        canvas.coords(
            self._pointer,
            self.cx, self.cy,
            self.cx + r * math.sin(angle), self.cy - r * math.cos(angle),
        )

    def on_press(self, src: VirtualSource, x: float, y: float, button: int) -> None:
        self._drag_start_y = y
        self._drag_start_value = src.input_layer.state.analog(self.control.id)

    def on_drag(self, src: VirtualSource, x: float, y: float) -> None:
        if self._drag_start_y is None:
            return
        delta = (self._drag_start_y - y) / 120.0
        src.set_analog(self.control.id, self._drag_start_value + delta)

    def on_release(self, src: VirtualSource, x: float, y: float) -> None:
        self._drag_start_y = None

    def on_wheel(self, src: VirtualSource, notches: int) -> None:
        current = src.input_layer.state.analog(self.control.id)
        src.set_analog(self.control.id, current + notches * 0.01)


# --------------------------------------------------------------------------
# Drehgeber
# --------------------------------------------------------------------------


#: Anteil des Radius, der als Nabe gilt. Ein Zug **ausserhalb** davon dreht
#: nur; ein Klick **auf** der Nabe drueckt zusaetzlich. Dieselbe Trennung
#: gibt es schon am Jogwheel (Platte gegen Rand), damit sich beide
#: Bedienelemente gleich anfuehlen.
ENCODER_HUB_RATIO = 0.55


class EncoderWidget(Widget):
    """Drehgeber mit relativen Ereignissen, optional mit Drucktaster.

    Rotation und Druck sind zwei getrennte Control-IDs - und zwei getrennte
    Flaechen:

    * **Rand ziehen** -> nur Drehung. So laesst sich auswaehlen, ohne
      auszuloesen; am Geraet fasst man den Regler ebenfalls am Rand an.
    * **Nabe klicken oder ziehen** -> Druck (und Drehung). Nur hier
      entsteht ``BROWSE_PRESS``, also auch nur hier das lange Druecken.
    * **Mausrad** -> nur Drehung, egal wo.

    Vorher loeste jeder Linksklick den Druck aus; Drehen ohne Druecken war
    mit der Maus gar nicht moeglich.
    """

    def __init__(
        self,
        control: Control,
        tf: Transform,
        press_control: Control | None = None,
    ) -> None:
        super().__init__(control, tf)
        self.press_control = press_control
        if press_control is not None:
            self.control_ids = (control.id, press_control.id)
        self._angle = 0.0  # nur fuer die Darstellung
        self._residual = 0.0
        self._last_angle: float | None = None
        self._mark: int | None = None
        self._body: int | None = None
        self._hub: int | None = None
        self._pressed = False

    @property
    def _hub_radius(self) -> float:
        return max(self.w, self.h) / 2 * ENCODER_HUB_RATIO

    def _on_hub(self, x: float, y: float) -> bool:
        return math.hypot(x - self.cx, y - self.cy) <= self._hub_radius

    @property
    def _step_deg(self) -> float:
        detents = max(1, self.control.detents_per_rev)
        return 360.0 / detents

    def draw(self, canvas: tk.Canvas) -> None:
        x0, y0, x1, y1 = self._bbox()
        self.items.append(
            canvas.create_oval(
                x0 - 4, y0 - 4, x1 + 4, y1 + 4,
                fill=theme.BG_PANEL_EDGE, outline=theme.BTN_EDGE,
            )
        )
        self._body = canvas.create_oval(
            x0, y0, x1, y1, fill=theme.BTN_FACE, outline=self._outline(), width=1
        )
        self.items.append(self._body)
        # Nabe sichtbar absetzen: nur dort loest ein Klick den Druck aus.
        hub = self._hub_radius
        self._hub = canvas.create_oval(
            self.cx - hub, self.cy - hub, self.cx + hub, self.cy + hub,
            fill=theme.BG_PANEL_EDGE, outline=theme.BTN_EDGE, width=1,
        )
        self.items.append(self._hub)
        r = max(self.w, self.h) / 2
        self._mark = canvas.create_line(
            self.cx, self.cy, self.cx, self.cy - r + 3,
            fill=theme.ACCENT, width=3,
        )
        self.items.append(self._mark)

    def refresh(self, canvas: tk.Canvas, state: InputState) -> None:
        if self._mark is None or self._body is None:
            return
        r = max(self.w, self.h) / 2 - 3
        inner = self._hub_radius + 2
        a = math.radians(self._angle)
        sin_a, cos_a = math.sin(a), math.cos(a)
        # Die Marke beginnt erst hinter der Nabe, damit die Nabe als eigene
        # Flaeche erkennbar bleibt.
        canvas.coords(
            self._mark,
            self.cx + inner * sin_a, self.cy - inner * cos_a,
            self.cx + r * sin_a, self.cy - r * cos_a,
        )
        pressed = (
            self.press_control is not None
            and state.is_pressed(self.press_control.id)
        )
        canvas.itemconfigure(self._body, outline=self._outline())
        if self._hub is not None:
            canvas.itemconfigure(
                self._hub,
                fill=theme.BTN_FACE_ACTIVE if pressed else theme.BG_PANEL_EDGE,
            )

    # -- Interaktion -------------------------------------------------------

    def on_press(self, src: VirtualSource, x: float, y: float, button: int) -> None:
        self._last_angle = _angle_deg(x - self.cx, y - self.cy)
        self._residual = 0.0
        # Nur die Nabe drueckt. Am Rand zu ziehen dreht ausschliesslich -
        # sonst laesst sich mit der Maus nicht auswaehlen, ohne zu laden.
        if (
            button == 1
            and self.press_control is not None
            and self._on_hub(x, y)
        ):
            src.press(self.press_control.id)
            self._pressed = True

    def on_drag(self, src: VirtualSource, x: float, y: float) -> None:
        if self._last_angle is None:
            return
        angle = _angle_deg(x - self.cx, y - self.cy)
        delta = _wrap180(angle - self._last_angle)
        self._last_angle = angle
        self._angle = (self._angle + delta) % 360.0
        self._residual += delta
        step = self._step_deg
        detents = int(self._residual / step)
        if detents:
            self._residual -= detents * step
            src.rotate(self.control.id, detents)

    def on_release(self, src: VirtualSource, x: float, y: float) -> None:
        self._last_angle = None
        if self._pressed and self.press_control is not None:
            src.release(self.press_control.id)
            self._pressed = False

    def on_wheel(self, src: VirtualSource, notches: int) -> None:
        self._angle = (self._angle + notches * self._step_deg) % 360.0
        src.rotate(self.control.id, notches)


# --------------------------------------------------------------------------
# Schalter
# --------------------------------------------------------------------------


class SwitchWidget(Widget):
    """Mehrstufiger Schalter. Klick auf ein Segment waehlt die Position."""

    def __init__(self, control: Control, tf: Transform) -> None:
        super().__init__(control, tf)
        self.positions = control.positions or ("0", "1")
        self._lever: int | None = None
        self._labels: list[int] = []

    def _segment_center(self, index: int) -> float:
        n = len(self.positions)
        top = self.cy - self.h / 2
        return top + self.h * (index + 0.5) / n

    def draw(self, canvas: tk.Canvas) -> None:
        x0, y0, x1, y1 = self._bbox()
        self.items.append(
            canvas.create_rectangle(
                x0, y0, x1, y1, fill=theme.BG_PANEL_EDGE,
                outline=self._outline(),
            )
        )
        for i, name in enumerate(self.positions):
            cy = self._segment_center(i)
            self._labels.append(
                canvas.create_text(
                    x1 + 4, cy, text=name, anchor="w",
                    fill=theme.FG_DIM, font=theme.FONT_TINY,
                )
            )
            self.items.append(self._labels[-1])
        self._lever = canvas.create_rectangle(
            x0 + 3, self._segment_center(0) - 4,
            x1 - 3, self._segment_center(0) + 4,
            fill=theme.FADER_KNOB, outline=theme.BTN_EDGE,
        )
        self.items.append(self._lever)

    def refresh(self, canvas: tk.Canvas, state: InputState) -> None:
        if self._lever is None:
            return
        position = state.switch(self.control.id)
        index = (
            self.positions.index(position) if position in self.positions else 0
        )
        cy = self._segment_center(index)
        x0, _, x1, _ = self._bbox()
        canvas.coords(self._lever, x0 + 3, cy - 4, x1 - 3, cy + 4)
        for i, item in enumerate(self._labels):
            canvas.itemconfigure(
                item,
                fill=theme.ACCENT if i == index else theme.FG_DIM,
            )

    def on_press(self, src: VirtualSource, x: float, y: float, button: int) -> None:
        n = len(self.positions)
        top = self.cy - self.h / 2
        index = int((y - top) / self.h * n)
        index = max(0, min(n - 1, index))
        src.set_switch(self.control.id, self.positions[index])


# --------------------------------------------------------------------------
# Jogwheel
# --------------------------------------------------------------------------


class JogWidget(Widget):
    """Jogwheel mit eigener Eingabestruktur.

    Zwei Flaechen, wie am Geraet:

    * Ziehen auf der **Platte** -> Beruehrung **und** Drehung
    * Ziehen auf dem **Rand**   -> nur Drehung, keine Beruehrung
    * Mausrad                   -> schnelle Stoesse fuer Backspins

    Das Widget erzeugt **kein** Ereignis. Es rechnet die Zeigerbewegung in
    Jog-Schritte um und legt sie im Positionszaehler von
    ``VirtualSource.jog`` ab; weitergegeben wird im Bildtakt
    (``poll_jog()``). Sonst entstuende bei schnellem Ziehen eine
    Warteschlange aus Dutzenden Teilbewegungen je Bild, waehrend das echte
    Jogwheel genau eine liefert.

    Der sichtbare Winkel (``visual_angle``) ist reine Darstellung. Er wird
    nie zurueckgelesen - die Trackposition kommt ausschliesslich aus der
    Deck-Engine.
    """

    #: Bruchteil einer Umdrehung je Mausrad-Rastung (mit Strg x5). Als
    #: Bruchteil, damit sich das Gefuehl nicht aendert, wenn das Jogwheel
    #: mit einer anderen Aufloesung eingetragen wird.
    WHEEL_FRACTION = 1 / 90

    def __init__(
        self, control: Control, tf: Transform, touch_control: Control
    ) -> None:
        super().__init__(control, tf)
        self.touch_control = touch_control
        self.control_ids = (control.id, touch_control.id)
        self.r_outer = max(self.w, self.h) / 2
        self.r_touch = tf.s(max(touch_control.w, touch_control.h) / 2)
        self._last_angle: float | None = None
        self._residual = 0.0
        self._touching = False
        #: Sichtbarer Winkel, 0-360 Grad. Nur Darstellung.
        self._visual_angle = 0.0
        #: Aufsummierte Drehung in Grad, ohne Umlauf - beliebig viele
        #: Umdrehungen. Nur fuer die Diagnoseanzeige.
        self._accumulated_deg = 0.0
        self._dimples: list[int] = []
        self._mark: int | None = None
        self._platter: int | None = None
        self._readout: int | None = None

    @property
    def _step_deg(self) -> float:
        ticks = max(1, self.control.ticks_per_rev)
        return 360.0 / ticks

    def hit(self, x: float, y: float) -> bool:
        d2 = (x - self.cx) ** 2 + (y - self.cy) ** 2
        return d2 <= self.r_outer * self.r_outer

    def draw(self, canvas: tk.Canvas) -> None:
        self.items.append(
            canvas.create_oval(
                self.cx - self.r_outer, self.cy - self.r_outer,
                self.cx + self.r_outer, self.cy + self.r_outer,
                fill=theme.JOG_RING, outline=theme.BTN_EDGE, width=2,
            )
        )
        # Griffmulden auf dem Rand, damit die Drehung sichtbar ist.
        r_mid = (self.r_outer + self.r_touch) / 2
        r_dim = (self.r_outer - self.r_touch) * 0.3
        for i in range(24):
            a = math.radians(i * 15)
            dx = self.cx + r_mid * math.sin(a)
            dy = self.cy - r_mid * math.cos(a)
            item = canvas.create_oval(
                dx - r_dim, dy - r_dim, dx + r_dim, dy + r_dim,
                fill=theme.BG_PANEL_EDGE, outline="",
            )
            self._dimples.append(item)
            self.items.append(item)

        self._platter = canvas.create_oval(
            self.cx - self.r_touch, self.cy - self.r_touch,
            self.cx + self.r_touch, self.cy + self.r_touch,
            fill=theme.JOG_PLATTER, outline=theme.BTN_EDGE, width=2,
        )
        self.items.append(self._platter)

        r_center = self.r_touch * 0.42
        self.items.append(
            canvas.create_oval(
                self.cx - r_center, self.cy - r_center,
                self.cx + r_center, self.cy + r_center,
                fill=theme.JOG_CENTER, outline=theme.BTN_EDGE,
            )
        )
        self._mark = canvas.create_line(
            self.cx, self.cy - r_center, self.cx, self.cy - self.r_touch + 2,
            fill=theme.JOG_MARK, width=3,
        )
        self.items.append(self._mark)
        self._readout = canvas.create_text(
            self.cx, self.cy + self.r_touch + 10, text="0 ticks",
            fill=theme.FG_DIM, font=theme.FONT_SMALL,
        )
        self.items.append(self._readout)

    def refresh(self, canvas: tk.Canvas, state: InputState) -> None:
        if self._mark is None or self._platter is None:
            return
        a = math.radians(self._visual_angle)
        r_center = self.r_touch * 0.42
        canvas.coords(
            self._mark,
            self.cx + r_center * math.sin(a), self.cy - r_center * math.cos(a),
            self.cx + (self.r_touch - 2) * math.sin(a),
            self.cy - (self.r_touch - 2) * math.cos(a),
        )
        touched = state.is_pressed(self.touch_control.id)
        canvas.itemconfigure(
            self._platter,
            fill="#6b7178" if touched else theme.JOG_PLATTER,
            outline=theme.ACCENT if touched else theme.BTN_EDGE,
        )
        canvas.itemconfigure(self._readout, text=self.readout_text(state))

    def readout_text(self, state: InputState) -> str:
        """Diagnosezeile unter dem Jogwheel.

        Zeigt genau die Werte der Jog-Schnittstelle, damit sich virtuelles
        und spaeteres echtes Jogwheel vergleichen lassen. Reine Anzeige -
        sie wird nirgends zurueckgelesen.
        """
        jog = state.jog(self.control.id)
        touched = state.is_pressed(self.touch_control.id)
        if jog.velocity > 1.0:
            direction = "FWD"
        elif jog.velocity < -1.0:
            direction = "REV"
        else:
            direction = "STOP"
        return (
            f"TOUCH {'JA ' if touched else 'NEIN'}  {direction}  "
            f"POS {jog.total:+d}  {jog.velocity:+.0f} t/s  "
            f"{self._accumulated_deg:+.0f}°"
        )

    # -- Interaktion -------------------------------------------------------

    @property
    def visual_angle(self) -> float:
        """Sichtbarer Winkel in Grad (0-360). Reine Darstellung."""
        return self._visual_angle

    @property
    def accumulated_angle(self) -> float:
        """Aufsummierte Drehung in Grad. Laeuft nicht um (0, 360, 720, ...)."""
        return self._accumulated_deg

    def _turn(self, degrees: float) -> None:
        """Drehung verbuchen: Darstellung und Diagnosezaehler."""
        self._visual_angle = (self._visual_angle + degrees) % 360.0
        self._accumulated_deg += degrees

    def on_press(self, src: VirtualSource, x: float, y: float, button: int) -> None:
        self._last_angle = _angle_deg(x - self.cx, y - self.cy)
        self._residual = 0.0
        d2 = (x - self.cx) ** 2 + (y - self.cy) ** 2
        if d2 <= self.r_touch * self.r_touch:
            # Beruehren heisst noch nicht bewegen: die Platte anzufassen und
            # stillzuhalten ergibt touch=True bei delta=0.
            src.jog_set_touch(True)
            self._touching = True

    def on_drag(self, src: VirtualSource, x: float, y: float) -> None:
        if self._last_angle is None:
            return
        angle = _angle_deg(x - self.cx, y - self.cy)
        # ``_wrap180`` faengt den Sprung 359 -> 0 Grad ab: gedreht wird
        # immer auf dem kuerzeren Weg, es gibt keinen Riesensprung.
        delta = _wrap180(angle - self._last_angle)
        self._last_angle = angle
        self._turn(delta)
        self._residual += delta
        step = self._step_deg
        ticks = int(self._residual / step)
        if ticks:
            self._residual -= ticks * step
            src.jog_rotate(ticks)

    def on_release(self, src: VirtualSource, x: float, y: float) -> None:
        self._last_angle = None
        self._residual = 0.0
        if self._touching:
            src.jog_set_touch(False)
            self._touching = False

    def cancel(self, src: VirtualSource) -> None:
        """Eingabe sicher beenden - Zeiger verlaesst das Jogwheel.

        Verlaesst die Maus das Bedienfeld oder wird ausserhalb losgelassen,
        darf die Beruehrung nicht haengen bleiben.
        """
        self.on_release(src, self.cx, self.cy)

    @property
    def _wheel_ticks(self) -> int:
        ticks = round(self.control.ticks_per_rev * self.WHEEL_FRACTION)
        return max(1, ticks)

    def on_wheel(self, src: VirtualSource, notches: int, boost: int = 1) -> None:
        ticks = notches * self._wheel_ticks * boost
        self._turn(ticks * self._step_deg)
        src.jog_rotate(ticks)


# --------------------------------------------------------------------------
# Joystick
# --------------------------------------------------------------------------


class JoystickWidget(Widget):
    """Zwei-Achsen-Joystick, Achsen -1.0 bis +1.0.

    Aktuell ist im Bedienfeld kein Joystick zugeordnet. Das Widget liegt
    bereit, falls sich das runde Bedienelement oben rechts als Joystick
    herausstellt.
    """

    def __init__(self, control: Control, tf: Transform) -> None:
        super().__init__(control, tf)
        self.r = max(self.w, self.h) / 2
        self._stick: int | None = None
        self._x = 0.0
        self._y = 0.0

    def draw(self, canvas: tk.Canvas) -> None:
        self.items.append(
            canvas.create_oval(
                self.cx - self.r, self.cy - self.r,
                self.cx + self.r, self.cy + self.r,
                fill=theme.BG_PANEL_EDGE, outline=self._outline(),
            )
        )
        r = self.r * 0.45
        self._stick = canvas.create_oval(
            self.cx - r, self.cy - r, self.cx + r, self.cy + r,
            fill=theme.BTN_FACE, outline=theme.BTN_EDGE,
        )
        self.items.append(self._stick)

    def refresh(self, canvas: tk.Canvas, state: InputState) -> None:
        if self._stick is None:
            return
        r = self.r * 0.45
        px = self.cx + self._x * (self.r - r)
        py = self.cy - self._y * (self.r - r)
        canvas.coords(self._stick, px - r, py - r, px + r, py + r)

    def _update(self, src: VirtualSource, x: float, y: float) -> None:
        nx = max(-1.0, min(1.0, (x - self.cx) / self.r))
        ny = max(-1.0, min(1.0, (self.cy - y) / self.r))
        if abs(nx - self._x) >= 0.01:
            self._x = nx
            src.set_axis(self.control.id, "X", nx)
        if abs(ny - self._y) >= 0.01:
            self._y = ny
            src.set_axis(self.control.id, "Y", ny)

    def on_press(self, src: VirtualSource, x: float, y: float, button: int) -> None:
        self._update(src, x, y)

    def on_drag(self, src: VirtualSource, x: float, y: float) -> None:
        self._update(src, x, y)

    def on_release(self, src: VirtualSource, x: float, y: float) -> None:
        self._x = 0.0
        self._y = 0.0
        src.set_axis(self.control.id, "X", 0.0)
        src.set_axis(self.control.id, "Y", 0.0)


# --------------------------------------------------------------------------
# Reine Anzeige
# --------------------------------------------------------------------------


class LedWidget(Widget):
    """Eigenstaendige Anzeige-LED. Spiegelt nur den LED-Zustand.

    Im aktuellen Bedienfeld gibt es keine solche LED - alle LEDs sitzen in
    Tastern und werden von ``ButtonWidget`` gezeichnet.
    """

    def __init__(self, control: Control, tf: Transform) -> None:
        super().__init__(control, tf)
        self._body: int | None = None

    def draw(self, canvas: tk.Canvas) -> None:
        x0, y0, x1, y1 = self._bbox()
        self._body = canvas.create_oval(
            x0, y0, x1, y1, fill=theme.GREEN_DIM, outline=theme.BTN_EDGE
        )
        self.items.append(self._body)

    def refresh(self, canvas: tk.Canvas, state: InputState) -> None:
        if self._body is None:
            return
        canvas.itemconfigure(
            self._body,
            fill=theme.GREEN if state.led(self.control.id) else theme.GREEN_DIM,
            outline=self._outline(),
        )


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------


def _angle_deg(dx: float, dy: float) -> float:
    """Winkel in Grad, 0 = oben, im Uhrzeigersinn steigend."""
    return math.degrees(math.atan2(dx, -dy))


def _wrap180(deg: float) -> float:
    while deg > 180.0:
        deg -= 360.0
    while deg < -180.0:
        deg += 360.0
    return deg
