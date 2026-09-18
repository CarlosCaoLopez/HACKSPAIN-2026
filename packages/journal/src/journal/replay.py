"""Replay de un journal. Depurar, grabar y trabajar sin los demás.

`make replay RUN=<id>` reproduce a velocidad real usando `t_sim`. `make dev-core`
y `make dev-dash` leen `fixtures/run_golden.jsonl` por aquí.
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

from contracts.events import Event


def read(path: Path) -> Iterator[Event]:
    """Los eventos del fichero, en orden de `seq`. Síncrono y a toda velocidad:
    es lo que usa el test de `apply`."""
    raise NotImplementedError


async def stream(path: Path, speed: float = 1.0) -> AsyncIterator[Event]:
    """Respeta los deltas de `t_sim`. `speed=0` va tan rápido como pueda."""
    raise NotImplementedError


def validate(path: Path) -> list[str]:
    """Replaya el journal entero comprobando que ningún payload revienta la
    validación. Devuelve la lista de errores; vacía es verde.

    Es uno de los tres tests de `make check`."""
    raise NotImplementedError
