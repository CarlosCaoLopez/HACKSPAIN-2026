"""Escritura append-only a `runs/<run_id>.jsonl`.

`publish` escribe la línea y LUEGO reparte. El orden importa: si algo revienta a
mitad de demo, el journal ya tiene el evento.

Nada se borra ni se edita. Si algo cambia, se emite otro evento.
"""

from pathlib import Path

from contracts.events import Event

RUNS_DIR = Path("runs")


class JournalWriter:
    def __init__(self, run_id: str, directory: Path = RUNS_DIR) -> None:
        raise NotImplementedError

    def write(self, ev: Event) -> None:
        """Una línea JSON por evento. Flush inmediato: un buffer perdido es un
        replay perdido."""
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    @property
    def path(self) -> Path:
        raise NotImplementedError
