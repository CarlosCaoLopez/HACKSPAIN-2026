"""Replay de un journal. Depurar, grabar y trabajar sin los demás.

`make replay RUN=<id>` reproduce a velocidad real usando `t_sim`. `make dev-core`
y `make dev-dash` leen `fixtures/run_golden.jsonl` por aquí.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

from pydantic import ValidationError

from contracts.events import PAYLOAD_MODELS, Event


def read(path: Path) -> Iterator[Event]:
    """Los eventos del fichero, en orden de `seq`. Síncrono y a toda velocidad:
    es lo que usa el test de `apply`."""
    with Path(path).open(encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            yield Event.model_validate_json(line)


async def stream(path: Path, speed: float = 1.0) -> AsyncIterator[Event]:
    """Respeta los deltas de `t_sim`. `speed=0` va tan rápido como pueda."""
    prev_t: float | None = None
    for ev in read(path):
        if speed > 0 and prev_t is not None:
            delta = ev.t_sim - prev_t
            if delta > 0:
                await asyncio.sleep(delta / speed)
        prev_t = ev.t_sim
        yield ev


def validate(path: Path) -> list[str]:
    """Replaya el journal entero comprobando que ningún payload revienta la
    validación. Devuelve la lista de errores; vacía es verde.

    Es uno de los tres tests de `make check`."""
    errors: list[str] = []
    for ev in read(path):
        model = PAYLOAD_MODELS.get(ev.type)
        if model is None:
            errors.append(f"seq={ev.seq}: tipo fuera del catálogo: {ev.type}")
            continue
        try:
            model.model_validate(ev.payload)
        except ValidationError as exc:
            errors.append(f"seq={ev.seq} ({ev.type}): {exc}")
    return errors


def _main(argv: list[str] | None = None) -> int:
    """`make replay RUN=<id>` → `python -m journal.replay runs/<id>.jsonl`.
    Reproduce a velocidad real, respetando los deltas de `t_sim`."""
    import argparse

    parser = argparse.ArgumentParser(prog="journal.replay")
    parser.add_argument("path", type=Path, help="runs/<run_id>.jsonl")
    parser.add_argument("--speed", type=float, default=1.0, help="0 = a tope")
    args = parser.parse_args(argv)

    if not args.path.exists():
        parser.error(f"no existe {args.path}")

    async def _run() -> None:
        async for ev in stream(args.path, speed=args.speed):
            print(f"{ev.seq:>5}  t={ev.t_sim:>8.2f}  {ev.type:<24}  {ev.source}")

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
