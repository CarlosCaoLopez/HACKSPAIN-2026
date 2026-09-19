"""Escritura append-only a `runs/<run_id>.jsonl`.

`publish` escribe la línea y LUEGO reparte. El orden importa: si algo revienta a
mitad de demo, el journal ya tiene el evento.

Nada se borra ni se edita. Si algo cambia, se emite otro evento.
"""

from pathlib import Path
from typing import TextIO

from contracts.events import Event

RUNS_DIR = Path("runs")


class JournalWriter:
    def __init__(self, run_id: str, directory: Path = RUNS_DIR) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._path = directory / f"{run_id}.jsonl"
        # Append: un run se reanuda sin pisar lo ya escrito. Line-buffered.
        self._fh: TextIO | None = self._path.open("a", encoding="utf-8", buffering=1)

    def write(self, ev: Event) -> None:
        """Una línea JSON por evento. Flush inmediato: un buffer perdido es un
        replay perdido."""
        if self._fh is None:
            raise ValueError("JournalWriter cerrado")
        self._fh.write(ev.model_dump_json() + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    @property
    def path(self) -> Path:
        return self._path
