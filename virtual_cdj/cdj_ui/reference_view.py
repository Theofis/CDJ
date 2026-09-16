"""Referenzbild zum Vergleich einblenden - nur fuer die Entwicklung.

Gedacht fuer den Abgleich der eigenen Oberflaeche mit einer Aufnahme eines
CDJ-3000-Displays: Bild laden, mit einem Tastendruck zwischen Referenz und
eigener Oberflaeche hin- und herschalten. Positionen, Schriftgewichte und
Kontraste fallen im direkten Wechsel deutlich mehr auf als beim Nebeneinander.

**Bewusst kein halbtransparentes Uebereinanderlegen.** Tk kann Canvas-Widgets
nicht miteinander verrechnen; ein echtes Ueberblenden braeuchte eine
Bildschirmaufnahme des sichtbaren Fensters und waere damit von der
Fenstersichtbarkeit und dem Betriebssystem abhaengig. Der Wechsel liefert
denselben Nutzen ohne diese Abhaengigkeit.

Diese Ansicht ist **nicht** Teil der Bedienoberflaeche: ohne
``--reference`` existiert sie nicht, und sie sendet keine Kommandos.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

from . import theme


class ReferenceView(tk.Canvas):
    """Vollflaechige Anzeige eines Referenzbildes.

    Liegt ueber der Oberflaeche und wird nur auf Zuruf eingeblendet.
    """

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(
            master, bg=theme.BG, highlightthickness=0, bd=0,
        )
        self.path: Path | None = None
        self.visible = False
        self._photo = None
        self._source = None
        self._size: tuple[int, int] = (0, 0)
        self._error = ""

    # ------------------------------------------------------------------

    def load(self, path: str | Path) -> bool:
        """Referenzbild laden. Rueckgabe: ob es gelesen werden konnte."""
        target = Path(path)
        try:
            from PIL import Image

            self._source = Image.open(target).convert("RGB")
        except Exception as error:  # pragma: no cover - fehlendes Bild
            self._source = None
            self._error = f"{target.name}: {error}"
            self.path = None
            return False
        self.path = target
        self._error = ""
        self._size = (0, 0)
        return True

    # ------------------------------------------------------------------

    def toggle(self) -> bool:
        self.visible = not self.visible
        return self.visible

    def show(self, width: int, height: int) -> None:
        """Ueber die ganze Flaeche legen und zeichnen."""
        self.place(x=0, y=0, width=width, height=height)
        tk.Misc.lift(self)
        self.render(width, height)

    def hide(self) -> None:
        self.place_forget()

    # ------------------------------------------------------------------

    def render(self, width: int, height: int) -> None:
        self.delete("all")
        if width <= 1 or height <= 1:
            return
        if self._source is None:
            self.create_text(
                width / 2, height / 2,
                text=self._error or "kein Referenzbild geladen",
                fill=theme.TEXT_MUTED,
                font=theme.Metrics(width, height).font(11, "bold"),
            )
            return

        from PIL import Image, ImageTk

        if self._size != (width, height) or self._photo is None:
            # Auf die Bildschirmgroesse bringen - ohne Weichzeichnen, damit
            # der Vergleich Kanten gegen Kanten zeigt.
            scaled = self._source.resize((width, height), Image.LANCZOS)
            self._photo = ImageTk.PhotoImage(scaled)
            self._size = (width, height)
        self.create_image(0, 0, image=self._photo, anchor="nw")

        metrics = theme.Metrics(width, height)
        label = f"REFERENZ  {self.path.name if self.path else ''}"
        self.create_rectangle(
            0, 0, metrics.px(320), metrics.px(20),
            fill=theme.BG, outline="",
        )
        self.create_text(
            metrics.px(8), metrics.px(10), text=label, anchor="w",
            fill=theme.ORANGE, font=metrics.font(8, "bold"),
        )
