"""Analyse- und Datei-Worker.

Ein eigener Thread laedt und analysiert Tracks. Damit blockiert das Laden
weder die Oberflaeche noch die Audioausgabe (Abschnitt 8 und 9 der Vorgabe).

Das Ergebnis kommt nicht per Rueckruf in den Worker-Thread zurueck, sondern
landet in einer Ergebnis-Queue. Der GUI-Thread holt es dort ab. So gibt es
keinen GUI-Aufruf aus einem fremden Thread.
"""

from __future__ import annotations

import queue
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .loader import LoadedTrack, TrackLoader
from .metadata import TrackTags


@dataclass(frozen=True)
class LoadRequest:
    """Auftrag an den Worker."""

    deck_id: int
    path: str
    #: Frei nutzbare Zusatzdaten, kommen im Ergebnis unveraendert zurueck.
    context: dict[str, Any] = field(default_factory=dict)
    #: Bereits bekannte Metadaten, etwa aus ``export.pdb``. Sie haben
    #: Vorrang; sind sie vollstaendig, wird die Datei fuer die Tags nicht
    #: mehr angefasst (siehe ``TrackLoader._metadata``).
    known_tags: TrackTags | None = None


@dataclass(frozen=True)
class LoadResult:
    """Ergebnis eines Auftrags. Genau eines von ``track`` / ``error``."""

    request: LoadRequest
    track: LoadedTrack | None = None
    error: str = ""
    traceback_text: str = ""

    @property
    def ok(self) -> bool:
        return self.track is not None


class AnalysisWorker:
    """Hintergrundthread fuer Laden und Analysieren."""

    def __init__(
        self,
        loader: TrackLoader | None = None,
        *,
        name: str = "analysis-worker",
    ) -> None:
        self.loader = loader if loader is not None else TrackLoader()
        #: Zusaetzliche Quellen, angesprochen ueber ``schema://kennung``.
        #: Damit kann z. B. der Demo-Provider im selben Worker-Thread laden,
        #: ohne dass die Audioschicht ihn kennen muss.
        self.resolvers: dict[str, Callable[[str], LoadedTrack]] = {}
        self.requests: queue.Queue[LoadRequest | None] = queue.Queue()
        self.results: queue.Queue[LoadResult] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._name = name
        self._stopping = threading.Event()

    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._run, name=self._name, daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        if not self.is_running:
            return
        self._stopping.set()
        self.requests.put(None)
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=timeout)

    # ------------------------------------------------------------------

    def request(
        self,
        deck_id: int,
        path: str | Path,
        *,
        known_tags: TrackTags | None = None,
        **context: Any,
    ) -> LoadRequest:
        """Track laden lassen. Kehrt sofort zurueck."""
        job = LoadRequest(
            deck_id=deck_id,
            path=str(path),
            context=context,
            known_tags=known_tags,
        )
        self.requests.put(job)
        self.loader.metrics.queue_length = self.requests.qsize()
        return job

    def poll(self) -> LoadResult | None:
        """Fertiges Ergebnis abholen. Blockiert nicht."""
        try:
            return self.results.get_nowait()
        except queue.Empty:
            return None

    def drain(self) -> list[LoadResult]:
        """Alle fertigen Ergebnisse abholen."""
        collected: list[LoadResult] = []
        while True:
            result = self.poll()
            if result is None:
                return collected
            collected.append(result)

    # ------------------------------------------------------------------

    def load_now(
        self,
        deck_id: int,
        path: str | Path,
        *,
        known_tags: TrackTags | None = None,
    ) -> LoadResult:
        """Synchron laden - fuer Tests und Skripte, nicht fuer die GUI."""
        job = LoadRequest(
            deck_id=deck_id, path=str(path), known_tags=known_tags
        )
        return self._handle(job)

    def register_resolver(
        self, scheme: str, resolver: Callable[[str], LoadedTrack]
    ) -> None:
        """Quelle fuer ``schema://kennung`` anmelden."""
        self.resolvers[scheme] = resolver

    def _handle(self, job: LoadRequest) -> LoadResult:
        try:
            scheme, separator, identifier = job.path.partition("://")
            if separator and scheme in self.resolvers:
                track = self.resolvers[scheme](identifier)
            else:
                track = self.loader.load(job.path, known=job.known_tags)
        except Exception as error:
            self.loader.metrics.note_failure()
            return LoadResult(
                request=job,
                error=f"{type(error).__name__}: {error}",
                traceback_text=traceback.format_exc(),
            )
        return LoadResult(request=job, track=track)

    def _run(self) -> None:
        while not self._stopping.is_set():
            job = self.requests.get()
            if job is None:
                return
            self.loader.metrics.queue_length = self.requests.qsize()
            self.results.put(self._handle(job))
