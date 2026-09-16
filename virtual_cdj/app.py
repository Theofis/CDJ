"""Verdrahtung der Anwendung.

Hier - und nur hier - werden die Schichten zusammengesteckt. Keine Schicht
kennt die andere direkt:

    Bedienfeld/ESP32 -> InputLayer -> InputRouter -> InputMapper
                                          |               |
                                    HardwareState     DeckCommand
                                          |               |
                                      Oberflaeche   DeckController
                                                          |
                                                     ModeManager
                                                   |             |
                                            MidiBackend       CdjBackend
                                                   +------|------+
                                                      DeckState
                                                          |
                                                     CDJ-Display

Der ``InputRouter`` entscheidet anhand des ``ApplicationMode``, ob eine
Eingabe ueberhaupt bis zur Deck-Engine kommt. In Pruefung und Kalibrierung
kommt sie das nicht - dort sieht sie nur die jeweilige Seite.

Threads (Abschnitt 9):

* GUI-Thread: Tkinter, Bildschleife, Abholen der Worker-Ergebnisse
* Audio-Thread: PortAudio-Callback, sample-genaue Position
* Analyse-Worker: Dekodieren, Resampeln, Analysieren, Cache
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path

from .audio.cache import AnalysisCache
from .audio.engine import AudioEngine
from .audio.loader import TrackLoader
from .audio.worker import AnalysisWorker, LoadResult
from .core import ids
from .core.button_customization import ButtonCustomizationStore
from .core.input_layer import InputLayer
from .deck.engine import Deck
from .deck.backend import CdjBackend, MidiBackend, OutputSink
from .deck.commands import CommandType
from .deck.controller import DeckController
from .deck.library import (
    MediaLibrary,
    SourceInfo,
    SourceKind,
    TrackListLibrary,
)
from .deck.mapping import InputMapper
from .deck.mode_manager import ModeManager, OperatingMode
from .deck.state import DeckState, JogMode, PadMode, TrackInfo
from .jog import JogScanner
from .media_library.devices import MediaDevice, UsbDeviceService
from .media_library.sources import build_library
from .media_library.taglist import TagListService
from .shell.calibration import CalibrationStore
from .shell.hardware_state import HardwareState
from .shell.modes import ApplicationMode, ModeController
from .shell.router import InputRouter
from .sources.virtual import VirtualSource

#: Ab dieser Spieldauer landet ein Track im Verlauf (Handbuch S. 42).
HISTORY_AFTER_S = 60.0

#: Modustaster -> Pad-Modus, den seine LED anzeigt. Genau einer leuchtet,
#: weil ``DeckState.pad_mode`` genau einen Wert hat.
PAD_MODE_LEDS: dict[str, PadMode] = {
    ids.HOT_CUE: PadMode.HOT_CUE,
    ids.BEAT_JUMP: PadMode.BEAT_LOOP,
}

#: Taster, deren LED einen zentralen Deck-Schalter spiegelt. Genau wie bei
#: den Pad-Modi gibt es **einen** Zustand: das virtuelle Bedienfeld zeichnet
#: die LED von hier, und derselbe ``set_led``-Aufruf schreibt spaeter ueber
#: die Hardware-Zuordnung auf einen MCP23017-Ausgang.
#:
#: JOG MODE hat am Geraet zwei Anzeigen (VINYL und CDJ); die LED steht fuer
#: VINYL, weil genau einer der beiden Modi gilt.
STATE_LEDS: dict[str, Callable[[DeckState], bool]] = {
    ids.QUANTIZE: lambda state: state.quantize,
    ids.SLIP: lambda state: state.slip,
    ids.JOG_MODE: lambda state: state.jog_mode is JogMode.VINYL,
}


class CdjApplication:
    """Haelt Decks, Audio, Analyse-Worker und die Eingabekette."""

    def __init__(
        self,
        deck_ids: Iterable[int] = (1,),
        *,
        block_frames: int = 512,
        cache_directory: str | Path | None = None,
        settings_path: str | Path | None = None,
        button_settings_path: str | Path | None = None,
        start_audio: bool = True,
        demo: bool = False,
        #: Datentraeger-Erkennung. Standard aus, damit Tests und Skripte
        #: nicht ungefragt die Laufwerke des Rechners abfragen; ``run_cdj.py``
        #: schaltet sie ein (``--no-usb`` schaltet sie wieder aus).
        usb: bool = False,
        backend_output: OutputSink = print,
    ) -> None:
        self.deck_ids = list(deck_ids)
        self._backend_output = backend_output
        self.button_customizations = ButtonCustomizationStore(
            button_settings_path, output=backend_output
        )

        self.audio = AudioEngine(block_frames=block_frames)
        cache = AnalysisCache(
            cache_directory if cache_directory is not None
            else AnalysisCache().directory
        )
        self.loader = TrackLoader(cache=cache)
        self.worker = AnalysisWorker(self.loader)

        #: Gemeinsame Library-Fassade. Beide Mock-Backends liefern sie ueber
        #: ihren Vertrag; spaeter kann MIDI eine entfernte Library einsetzen.
        self.library = MediaLibrary()

        #: Tag Lists je Datentraeger (Handbuch S. 40). Nur im Speicher - es
        #: wird nichts auf einen Stick geschrieben. Ohne Schreib-Layer
        #: meldet ``create_playlist()`` ehrlich, dass es nicht geht.
        self.tag_lists = TagListService()

        #: Die vorhandenen Decks bleiben die lokale Standalone-Engine.
        self.decks: dict[int, Deck] = {}
        #: MIDI hat einen getrennten State/Transport, aber dieselbe Deck-
        #: Logik. Es ist noch keine Laptop-Verbindung implementiert.
        self.midi_decks: dict[int, Deck] = {}
        #: Zuletzt geladene Samples je Deck - damit ein spaeter geoeffnetes
        #: Ausgabegeraet den bereits geladenen Track bekommt.
        self._samples: dict[int, object] = {}
        for deck_id in self.deck_ids:
            self.decks[deck_id] = Deck(deck_id)
            self.midi_decks[deck_id] = Deck(deck_id)

        self.midi_backend = MidiBackend(
            self.midi_decks,
            library=lambda: self.library,
            output=backend_output,
            suppress_output=self._suppress_default_button_output,
        )
        self.cdj_backend = CdjBackend(
            self.decks,
            library=lambda: self.library,
            load_handler=self.load_by_track_id,
            track_search_handler=self.track_search,
            output=backend_output,
            suppress_output=self._suppress_default_button_output,
        )
        self.mode_manager = ModeManager(
            self.midi_backend,
            self.cdj_backend,
            settings_path=settings_path,
        )
        self.controllers: dict[int, DeckController] = {
            deck_id: DeckController(deck_id, self.mode_manager)
            for deck_id in self.deck_ids
        }
        #: Rueckwaertskompatibler Name fuer GUI und bestehende Aufrufer. Die
        #: Provider sind jetzt die zentralen DeckController.
        self.providers: dict[int, DeckController] = dict(self.controllers)

        # Eine Eingabekette, die auf ein Deck zeigt. Weitere Bedienfelder
        # bekommen jeweils eigene Ketten mit eigener Deck-ID.
        self.input_layer = InputLayer()
        self._unsubscribe_button_output = self.input_layer.subscribe(
            self.button_customizations.emit
        )

        # -- Anwendungsrahmen: Modus, Kalibrierung, Hardware-Zustand -----
        #
        # Der Modus entscheidet, ob Eingaben DJ-Funktionen ausloesen. Die
        # Kalibrierung haengt an der Input-Schicht, damit Rohwerte genau
        # einmal umgerechnet werden - nicht in jeder Oberflaeche neu.
        self.modes = ModeController(ApplicationMode.PERFORMANCE)
        self.calibration = CalibrationStore.load()
        self.input_layer.calibration = self.calibration
        #: Scan-Programm des Jogwheels. Ohne angeschlossenes Geraet bleibt
        #: es leer - die Bewegung kommt dann vom virtuellen Bedienfeld.
        self.jog = JogScanner(
            touch_on=self.calibration.touch.on_threshold,
            touch_off=self.calibration.touch.off_threshold,
            invert=self.calibration.jog.invert,
        )
        self.hardware = HardwareState(
            self.input_layer, jog=None, calibration=self.calibration
        )
        self.router = InputRouter(self.modes)
        self.input_layer.subscribe(self.router.handle_event)

        self.virtual_source = VirtualSource(self.input_layer)
        self.virtual_source.start()
        self.mappers: dict[int, InputMapper] = {}
        #: Empfaenger der Kommandos je Deck. Ohne Eintrag geht es direkt an
        #: den Provider; eine Oberflaeche haengt sich mit
        #: ``set_command_sink`` davor.
        self._sinks: dict[int, Callable[[object], None]] = {}
        self.bind_panel(self.deck_ids[0])

        self.audio_started = False
        if start_audio:
            self.start_audio()

        #: Wird nach jedem geladenen Track aufgerufen (GUI-Thread).
        self.on_track_loaded: list[Callable[[LoadResult], None]] = []
        #: Wird gerufen, wenn sich die Quellenliste geaendert hat - ein
        #: Datentraeger kam hinzu, wurde fertig gelesen oder verschwand.
        #: Die Oberflaeche haengt sich hier ein, um SOURCE und BROWSE neu
        #: auf ``self.library`` zu zeigen (GUI-Thread).
        self.on_sources_changed: list[Callable[[], None]] = []

        #: Aus Dateien geladene Tracks, in Ladereihenfolge.
        self._file_tracks: list[TrackInfo] = []
        self._file_library: TrackListLibrary | None = None
        #: Wiedergabeverlauf: Track-IDs in der Reihenfolge des Abspielens.
        self.history: list[str] = []
        self._played_s: dict[int, float] = {}
        self._history_track: dict[int, str] = {}
        self._last_history_tick = time.monotonic()

        #: Demo-Quelle. Nur hier wird das Demo-Paket eingebunden - der
        #: Produktionspfad kennt es nicht.
        self.demo: object | None = None
        if demo:
            self._enable_demo()

        #: Erkennung angeschlossener Datentraeger. Nur lesend.
        self.usb: UsbDeviceService | None = None
        if usb:
            self._enable_usb()

        self.worker.start()

    # ------------------------------------------------------------------

    def _enable_demo(self) -> None:
        """Demo-Tracks als Quelle ``demo://<id>`` anmelden."""
        from .audio.format import ENGINE_SAMPLE_RATE, AudioBuffer
        from .audio.loader import LoadedTrack
        from .demo import DemoTrackProvider

        provider = DemoTrackProvider(sample_rate=ENGINE_SAMPLE_RATE)
        self.demo = provider
        self.library.add(
            TrackListLibrary(
                SourceInfo(
                    source_id="DEMO",
                    name="DEMO TRACKS",
                    kind=SourceKind.DEMO,
                    has_library=True,
                    note="Demo-Quelle: synthetische Analyse- und Audiodaten",
                ),
                provider.infos,
                history=lambda: tuple(self.history),
                tag_list=self.tag_lists.ids_for("DEMO"),
            )
        )

        def resolve(track_id: str) -> LoadedTrack:
            info, samples = provider.load(track_id)
            buffer = (
                AudioBuffer(samples, ENGINE_SAMPLE_RATE)
                if samples is not None
                else AudioBuffer.empty(ENGINE_SAMPLE_RATE)
            )
            return LoadedTrack(info=info, buffer=buffer)

        self.worker.register_resolver("demo", resolve)

    # ------------------------------------------------------------------
    # Datentraeger (USB)
    # ------------------------------------------------------------------

    def _enable_usb(self) -> None:
        """Datentraeger-Erkennung anmelden.

        Der Dienst laeuft von hier an mit: ``tick()`` fragt ihn, er meldet
        hinzugekommene und entfernte Datentraeger zurueck, und diese Klasse
        haelt daraufhin ``self.library`` aktuell. Die Oberflaeche wird nicht
        von hier aus angefasst - sie liest ``self.library`` ohnehin bei
        jedem Bild neu und wird ueber ``on_sources_changed`` nur angestossen.
        """
        service = UsbDeviceService()
        service.on_attached.append(self._usb_changed)
        service.on_updated.append(self._usb_changed)
        service.on_detached.append(self._usb_detached)
        self.usb = service
        # Ein beim Start bereits angeschlossener Stick soll sofort in SOURCE
        # stehen und nicht erst nach dem ersten Bild.
        service.poll(force=True)

    def _usb_changed(self, device: MediaDevice) -> None:
        """Datentraeger angeschlossen oder fertig gelesen."""
        if not device.is_media_source:
            # Die Systemplatte ist ein Laufwerk, aber keine DJ-Quelle. Falls
            # sie einmal eine war (Bibliothek nachtraeglich verschwunden),
            # verschwindet sie hier auch wieder.
            self.library.remove(device.source_id)
            return
        # Die Tag List haengt am Datentraeger, nicht am Player (S. 40).
        self.library.add(
            build_library(
                device, tag_list=self.tag_lists.ids_for(device.source_id)
            )
        )
        self._notify_sources_changed()

    def _usb_detached(self, device: MediaDevice) -> None:
        """Datentraeger entfernt - sicheren Zustand herstellen.

        Die Quelle verschwindet aus dem Browser. Ein bereits **geladener**
        Track laeuft weiter: seine Samples liegen im Speicher, nicht auf dem
        Stick (siehe ``_apply``). Das ist der sichere Zustand - ein Deck
        mitten im Set stummzuschalten, weil jemand den falschen Stick
        gezogen hat, waere das Gegenteil davon. Neu geladen werden kann von
        diesem Datentraeger danach nichts mehr; ``load_by_track_id`` findet
        die Track-ID nicht mehr und meldet das.
        """
        self.library.remove(device.source_id)
        self._notify_sources_changed()

    def _notify_sources_changed(self) -> None:
        for hook in self.on_sources_changed:
            try:
                hook()
            except Exception:  # pragma: no cover - defekter Zuhoerer
                pass

    def refresh_sources(self) -> None:
        """Manuelles Aktualisieren der Quellen (Handbuch S. 18).

        Liest jeden angeschlossenen Datentraeger neu ein - noetig, wenn der
        Stick zwischenzeitlich in rekordbox veraendert wurde.
        """
        if self.usb is not None:
            self.usb.refresh()

    # ------------------------------------------------------------------
    # Master-Sicht und Trackquelle fuer die Oberflaeche
    # ------------------------------------------------------------------

    def master_view(self, own_deck_id: int):
        """Master-Sicht fuer ein Display.

        Sucht das Deck, das sich selbst als Master fuehrt. Diese Funktion ist
        der Einstiegspunkt, an dem spaeter eine Netzwerkquelle einen
        ``MasterDeckView`` liefern kann - der Bildschirm merkt keinen
        Unterschied.
        """
        from .deck.display_state import MasterDeckView

        for deck_id, controller in self.controllers.items():
            state = controller.get_state()
            if state.is_master:
                return MasterDeckView.from_deck_state(state)
        # Kein Deck ist Master: ein anderes Deck mit Track als Vergleich.
        for deck_id, controller in self.controllers.items():
            state = controller.get_state()
            if deck_id != own_deck_id and state.has_track:
                return MasterDeckView.from_deck_state(state)
        return None

    def track_source(self):
        """Flache Trackliste der Standardquelle.

        Kurzform fuer Aufrufer, die keine Hierarchie brauchen. Der
        Durchsuchen-Bildschirm nutzt stattdessen ``self.library``.
        """
        return self.library.tracks(self.library.default_source_id())

    def _register_file_source(self) -> None:
        """Dateiquelle erst anmelden, wenn es wirklich Dateien gibt."""
        if self._file_library is not None:
            return
        self._file_library = self.library.add(
            TrackListLibrary(
                SourceInfo(
                    source_id="FILES",
                    name="DATEIEN",
                    kind=SourceKind.FILE,
                    has_library=False,
                    note="lokal geladene Dateien",
                ),
                lambda: tuple(self._file_tracks),
                history=lambda: tuple(self.history),
                tag_list=self.tag_lists.ids_for("FILES"),
            )
        )

    def load_by_track_id(self, deck_id: int, track_id: str) -> None:
        """Track anhand seiner Kennung laden - vom Browser aufgerufen."""
        if self.demo is not None and self.demo.get(track_id) is not None:  # type: ignore[attr-defined]
            self.load_track(deck_id, f"demo://{track_id}")
            return
        # Echte Tracks werden ueber ihren Pfad geladen. Der Browser kennt sie
        # ueber die Track-ID, die Bibliothek liefert den Pfad dazu.
        track = self.library.track(track_id)
        path = track.file_path if track is not None and track.file_path else track_id
        self.load_track(deck_id, path)

    def track_search(self, deck_id: int, direction: int) -> None:
        """Nachbartrack laden - TRACK SEARCH |<< / >>| (Handbuch S. 48).

        Die Reihenfolge ist die des Durchsuchen-Bildschirms: die Trackliste
        derjenigen Bibliothek, in der der geladene Track steht. Damit springt
        die Taste dorthin, wo der Benutzer den Track auch gefunden hat.

        Der Sprung an den **Anfang** des laufenden Tracks passiert nicht
        hier, sondern in der Deck-Engine; sie ruft diesen Weg nur auf, wenn
        wirklich ein anderer Track gemeint ist.

        Ohne geladenen Track, ohne Liste oder am Rand der Liste passiert
        nichts. Eine erfundene Reihenfolge waere schlechter als keine, und
        ueber das Ende hinaus gibt es keinen Track.
        """
        controller = self.controllers.get(deck_id)
        if controller is None:
            return
        track = controller.get_state().track
        if track is None:
            return
        for library in self.library.libraries:
            tracks = tuple(library.tracks())
            for index, info in enumerate(tracks):
                if info.track_id != track.track_id:
                    continue
                target = index + (1 if direction > 0 else -1)
                if 0 <= target < len(tracks):
                    self.load_by_track_id(deck_id, tracks[target].track_id)
                return

    def demo_track_ids(self) -> tuple[str, ...]:
        provider = self.demo
        if provider is None:
            return ()
        return tuple(
            track.info.track_id for track in provider.tracks()  # type: ignore[attr-defined]
        )

    def load_demo_defaults(self) -> None:
        """Je Deck einen Demo-Track laden, Deck 1 wird Master."""
        ids = self.demo_track_ids()
        if not ids:
            return
        for number, deck_id in enumerate(self.deck_ids):
            self.load_track(deck_id, f"demo://{ids[number % len(ids)]}")

    # ------------------------------------------------------------------

    def bind_panel(self, deck_id: int) -> InputMapper:
        """Die Bedienfeld-Eingaben auf ein Deck legen.

        Die Kommandos gehen an den Empfaenger dieses Decks. Standardmaessig
        ist das der ``DeckStateProvider``. Eine Oberflaeche kann sich
        stattdessen einhaengen (``set_command_sink``) - nur so erreichen
        Taster wie SOURCE, BROWSE oder BACK und der Drehgeber den Bildschirm,
        denn das Deck kennt keine Ansichten.

        Der Weg fuehrt ueber den ``InputRouter``: ausserhalb des
        Performance-Modus erreicht keine Eingabe die Deck-Engine.
        """
        mapper = InputMapper(deck_id, lambda cmd: self.dispatch(cmd))
        self.router.performance_sink = mapper.handle_event
        self.mappers[deck_id] = mapper
        return mapper

    # ------------------------------------------------------------------
    # Anwendungsmodus
    # ------------------------------------------------------------------

    @property
    def mode(self) -> ApplicationMode:
        return self.modes.mode

    def set_mode(self, mode: ApplicationMode) -> ApplicationMode:
        """Anwendungsmodus setzen - Oberflaeche und Eingaben folgen."""
        return self.modes.set_mode(mode)

    @property
    def operating_mode(self) -> OperatingMode:
        return self.mode_manager.current_mode

    def set_operating_mode(self, mode: OperatingMode | str) -> bool:
        """MIDI/CDJ kontrolliert ueber den zentralen ModeManager wechseln."""
        return self.mode_manager.set_mode(mode)

    def attach_jog_scanner(self, scanner: JogScanner | None = None) -> None:
        """Das Jog-Scan-Programm anhaengen.

        Wird gerufen, sobald ein Geraet Sensordaten liefert. Ohne Scanner
        zeigt die Pruefseite ehrlich an, dass es keine Sensorpegel gibt,
        statt Nullen als Messwerte auszugeben.
        """
        self.jog = scanner if scanner is not None else self.jog
        self.hardware.jog_scanner = self.jog

    def dispatch(self, cmd) -> None:
        """Ein Kommando an den Empfaenger seines Decks geben."""
        if cmd.type is CommandType.OPEN_SETTINGS:
            # Nach dem Wechsel blockiert der InputRouter die Release-Flanken
            # der Performance-Taster. Den SHIFT-Modifikator deshalb hier
            # explizit loesen, damit er bei der Rueckkehr nicht haengen bleibt.
            for mapper in self.mappers.values():
                mapper.reset_modifiers()
            self.modes.set_mode(ApplicationMode.SETTINGS)
            return
        sink = self._sinks.get(cmd.deck_id)
        if sink is None:
            controller = self.controllers.get(cmd.deck_id)
            if controller is None:
                return
            controller.send(cmd)
            return
        sink(cmd)

    def _suppress_default_button_output(self, cmd) -> bool:
        """Bei individuellem Text nicht zusaetzlich den Mocktext drucken."""
        return bool(
            cmd.control_id
            and self.button_customizations.console_output(cmd.control_id)
            is not None
        )

    def set_command_sink(
        self, deck_id: int, sink: Callable[[object], None] | None
    ) -> None:
        """Empfaenger der Kommandos eines Decks setzen.

        Die Oberflaeche uebergibt hier ihr ``send_command``. Sie behandelt
        Anzeigebefehle selbst und schickt alles andere weiter an das Deck -
        derselbe Weg, den auch ihre eigenen Beruehrungen nehmen.
        """
        if sink is None:
            self._sinks.pop(deck_id, None)
            return
        self._sinks[deck_id] = sink

    def start_audio(self) -> bool:
        """Ausgabestream oeffnen und die Decks daran haengen.

        Ohne Ausgabegeraet bleibt bewusst der ``NullPlaybackPort`` aktiv: die
        Decks laufen dann ueber die Wanduhr weiter, damit die Oberflaeche
        bedienbar bleibt. Sie geben aber keinen Ton aus, und
        ``DeckState.audio_status`` meldet das.
        """
        self.audio_started = self.audio.start()
        if not self.audio_started:
            return False
        for deck_id, deck in self.decks.items():
            voice = self.audio.voice(deck_id)
            deck.attach_playback(voice)
            # Bereits geladene Tracks nachreichen, falls das Geraet erst
            # jetzt verfuegbar wurde.
            samples = self._samples.get(deck_id)
            if samples is not None:
                voice.load(samples)
                deck.load_track(deck.state.track, samples)
        return True

    @property
    def audio_status_text(self) -> str:
        if self.audio_started:
            return f"{self.audio.sample_rate} Hz / {self.audio.block_frames}"
        return self.audio.last_error or "kein Ausgabegeraet"

    # ------------------------------------------------------------------

    def load_track(self, deck_id: int, path: str | Path) -> None:
        """Track im Hintergrund laden. Die GUI blockiert nicht."""
        self.worker.request(deck_id, path)

    def load_track_now(self, deck_id: int, path: str | Path) -> LoadResult:
        """Synchron laden - fuer Tests und Skripte."""
        result = self.worker.load_now(deck_id, path)
        self._apply(result)
        return result

    def poll_worker(self) -> list[LoadResult]:
        """Fertige Ladeauftraege uebernehmen. Aus dem GUI-Thread aufrufen."""
        results = self.worker.drain()
        for result in results:
            self._apply(result)
        return results

    def _apply(self, result: LoadResult) -> None:
        if not result.ok or result.track is None:
            return
        deck = self.decks.get(result.request.deck_id)
        if deck is None:
            return
        samples = result.track.samples
        self._samples[result.request.deck_id] = samples
        # Dateitracks in die Dateiquelle aufnehmen, damit der Browser sie
        # findet. Demo-Tracks kommen aus ihrer eigenen Quelle.
        info = result.track.info
        if info.source != "DEMO" and all(
            existing.track_id != info.track_id for existing in self._file_tracks
        ):
            self._file_tracks.append(info)
            self._register_file_source()
        deck.load_track(
            result.track.info, samples if samples.size else None
        )
        # Tracknummer: Position in der Browse-Liste, damit die Anzeige einen
        # echten Wert hat statt einer Erfindung.
        number = self._track_number(result.track.info.track_id)
        if number:
            deck._update(track_number=number)  # noqa: SLF001
        self.audio.memory_mb()
        for hook in self.on_track_loaded:
            hook(result)

    # ------------------------------------------------------------------

    def _track_number(self, track_id: str) -> int:
        """Position des Tracks in seiner Quelle, 1-basiert."""
        for library in self.library.libraries:
            for number, info in enumerate(library.tracks(), start=1):
                if info.track_id == track_id:
                    return number
        return 0

    # ------------------------------------------------------------------

    def update_history(self, elapsed_s: float) -> None:
        """Verlauf fortschreiben (Handbuch S. 42).

        Ein Track wird aufgezeichnet, wenn er ungefaehr eine Minute lang
        gespielt wurde - gezaehlt wird echte Spielzeit, nicht die Position.
        """
        for deck_id, controller in self.controllers.items():
            state = controller.get_state()
            track_id = state.track.track_id if state.track else ""
            if track_id != self._history_track.get(deck_id):
                self._history_track[deck_id] = track_id
                self._played_s[deck_id] = 0.0
            if not track_id or not state.is_playing:
                continue
            played = self._played_s.get(deck_id, 0.0) + max(0.0, elapsed_s)
            self._played_s[deck_id] = played
            if played >= HISTORY_AFTER_S and track_id not in self.history:
                self.history.append(track_id)

    def _sync_pad_mode_leds(self) -> None:
        """Deck-Schalter auf die LEDs spiegeln (Abschnitt 21).

        **Eine** Quelle: der ``DeckState``. Die LED-Zustaende gehen in die
        Input-Schicht; das virtuelle Bedienfeld zeichnet sie von dort, und
        derselbe Aufruf schreibt spaeter ueber die Hardware-Zuordnung auf
        einen MCP23017-Ausgang. Es gibt bewusst keinen getrennten Zustand
        fuer Bildschirm und Hardware.

        Gespiegelt werden der Pad-Modus sowie QUANTIZE, SLIP und JOG MODE.
        Gezeigt wird bei SLIP der **Schalter** (``slip``), nicht die gerade
        laufende Aktion (``slip_active``) - die LED-Schicht kennt nur an und
        aus, ein Blinken gibt es hier noch nicht.
        """
        deck_id = self.deck_ids[0] if self.deck_ids else None
        controller = self.controllers.get(deck_id) if deck_id else None
        if controller is None:
            return
        state = controller.get_state()
        for control_id, active in PAD_MODE_LEDS.items():
            self.input_layer.set_led(control_id, state.pad_mode is active)
        for control_id, is_on in STATE_LEDS.items():
            self.input_layer.set_led(control_id, is_on(state))

    def tick(self) -> None:
        """Regelmaessig aus dem GUI-Thread aufrufen."""
        self._sync_pad_mode_leds()
        # Jog zuerst: die gesammelte Bewegung des virtuellen Jogwheels wird
        # als **ein** Ereignis je Bild weitergegeben - derselbe Weg, den
        # ``HardwareSource.poll_jog()`` fuer das echte Jogwheel nimmt. So
        # kann sich keine Warteschlange alter Teilbewegungen bilden.
        self.virtual_source.poll_jog()
        self.poll_worker()
        if self.usb is not None:
            # Billig: echte Laufwerksabfragen sind im Dienst gedrosselt,
            # dazwischen ist das ein Vergleich auf dem letzten Stand.
            self.usb.poll()
        self.mode_manager.tick()
        now = time.monotonic()
        self.update_history(now - self._last_history_tick)
        self._last_history_tick = now

    def close(self) -> None:
        self._unsubscribe_button_output()
        if self.usb is not None:
            self.usb.close()
        self.worker.stop()
        for controller in self.controllers.values():
            controller.close()
        self.mode_manager.close()
        for deck in self.decks.values():
            deck.detach_playback()
        self.audio.close()
