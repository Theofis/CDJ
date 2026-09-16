"""Kalibrierung und Pruefung des Jogwheels.

    python run_jog.py                 Demo-Signal (kein Geraet noetig)
    python run_jog.py --stdin         Protokollzeilen von der Standardeingabe
    python run_jog.py --on 700 --off 650   Grenzwerte vorbelegen

Mit ``--stdin`` liest das Programm dasselbe Geraeteprotokoll wie
``sources/hardware.py`` und gibt die Jog-Rohdaten in das Scan-Programm:

    Q JOG_MOVE 10      Pegel beider Lichtschranken (A zuerst)
    C JOG_TOUCH 812    Rohwert des kapazitiven Sensors
    P JOG_MOVE 15427   fertiger Positionszaehler des Geraets

Damit laesst sich ein angeschlossenes Geraet ohne weiteren Code einmessen,
solange der Transport in ``HardwareSource.start()`` noch fehlt:

    <geraet> | python run_jog.py --stdin
"""

from __future__ import annotations

import argparse
import sys
import threading

from virtual_cdj.core.input_layer import InputLayer
from virtual_cdj.demo.jog import SimulatedJog
from virtual_cdj.jog import JogScanner
from virtual_cdj.sources.hardware import HardwareSource, ProtocolError
from virtual_cdj.ui.jog_calibration import JogCalibrationApp


def read_stdin(source: HardwareSource) -> threading.Thread:
    """Protokollzeilen im Hintergrund einlesen.

    Der Faden fuellt nur das Scan-Programm. Die Oberflaeche liest davon
    unabhaengig den jeweils aktuellen Stand - genau die Trennung, um die es
    beim Jogwheel geht.
    """

    def loop() -> None:
        for line in sys.stdin:
            try:
                source.feed_line(line)
            except ProtocolError as error:
                print(f"unlesbare Zeile: {error}", file=sys.stderr)

    thread = threading.Thread(target=loop, name="jog-stdin", daemon=True)
    thread.start()
    return thread


def build(args: argparse.Namespace) -> JogCalibrationApp:
    scanner = JogScanner(
        touch_on=args.on, touch_off=args.off, invert=args.invert
    )
    if args.stdin:
        source = HardwareSource(InputLayer(), jog=scanner)
        read_stdin(source)
        return JogCalibrationApp(scanner)
    return JogCalibrationApp(scanner, demo=SimulatedJog())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Jog-Kalibrierung")
    parser.add_argument(
        "--stdin", action="store_true",
        help="Protokollzeilen von der Standardeingabe lesen",
    )
    parser.add_argument(
        "--on", type=float, default=700.0,
        help="Grenzwert EIN des Touch-Sensors (Standard 700)",
    )
    parser.add_argument(
        "--off", type=float, default=650.0,
        help="Grenzwert AUS des Touch-Sensors (Standard 650)",
    )
    parser.add_argument(
        "--invert", action="store_true",
        help="Drehrichtung umkehren (je nach Einbaurichtung der Sensoren)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    build(parse_args(argv)).mainloop()


if __name__ == "__main__":
    main()
