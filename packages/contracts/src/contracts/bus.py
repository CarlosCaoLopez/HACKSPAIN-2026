"""El bus: un `asyncio.Queue` por suscriptor y un journal JSONL.

Estas tres firmas se escriben en la ventana de contrato y no cambian. El cuerpo
lo pone P1: el writer del journal se inyecta (`contracts` no importa de nadie,
tampoco de `journal`).

`publish` hace dos cosas EN ESTE ORDEN: escribe la línea en `runs/<run_id>.jsonl`
y luego reparte. Si algo revienta a mitad de demo, el journal ya tiene el evento.
Sin journal no hay replay.

Cuerpo mínimo escrito el viernes por Hugo para poder probar `voice` y `belief`
sin esperar a nadie. Si P1 lo reescribe, las firmas públicas de abajo se quedan:
`publish`, `subscribe`, `current_run_id`, y los añadidos `configure`,
`current_t_sim`, `make_event`, `reset`.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from pydantic import ValidationError

from contracts.events import PAYLOAD_MODELS, Event, EventType

Writer = Callable[[Event], None]

_run_id: str = ""
_seq: int = 0
_t_sim: float = 0.0
_subs: list[tuple[frozenset[EventType], asyncio.Queue[Event]]] = []
_writer: Writer | None = None
_journal: IO[str] | None = None


def _strict() -> bool:
    """En desarrollo un payload malo lanza; en demo se registra como malformado."""
    return os.environ.get("VELA_MODE", "dev") != "demo"


def configure(
    run_id: str | None = None,
    writer: Writer | None = None,
    journal_dir: Path | str = "runs",
) -> str:
    """Arranca un run: fija `run_id`, reinicia `seq` y abre el journal.

    `writer` inyectado gana; si no hay, se escribe a `<journal_dir>/<run_id>.jsonl`.
    Devuelve el `run_id`."""
    global _run_id, _seq, _t_sim, _writer, _journal
    close()
    _run_id = run_id or str(uuid.uuid4())
    _seq = 0
    _t_sim = 0.0
    if writer is not None:
        _writer = writer
    else:
        d = Path(journal_dir)
        d.mkdir(parents=True, exist_ok=True)
        _journal = (d / f"{_run_id}.jsonl").open("a", encoding="utf-8")
        _writer = _file_writer
    return _run_id


def _file_writer(ev: Event) -> None:
    assert _journal is not None
    _journal.write(ev.model_dump_json())
    _journal.write("\n")
    _journal.flush()


def close() -> None:
    global _journal, _writer
    if _journal is not None:
        _journal.close()
        _journal = None
    _writer = None


def reset() -> None:
    """Solo tests: borra suscriptores y estado."""
    global _run_id, _seq, _t_sim
    close()
    _subs.clear()
    _run_id = ""
    _seq = 0
    _t_sim = 0.0


def current_run_id() -> str:
    """El uuid del run en curso."""
    return _run_id


def current_t_sim() -> float:
    """El último `t_sim` visto en un `world.tick`. Lo usan los paquetes que no
    tienen reloj propio (voice) para sellar sus eventos."""
    return _t_sim


def make_event(
    type: EventType,
    payload: dict,
    source: str,
    causes: list[int] | None = None,
    t_sim: float | None = None,
) -> Event:
    """El sobre relleno con lo que el bus sabe. `seq` lo sella `publish`."""
    return Event(
        run_id=_run_id,
        seq=0,
        t_wall=datetime.now(UTC),
        t_sim=_t_sim if t_sim is None else t_sim,
        type=type,
        source=source,
        payload=payload,
        causes=list(causes or []),
    )


async def publish(ev: Event) -> None:
    """Sella `seq`, valida el payload contra PAYLOAD_MODELS, escribe al journal y
    reparte. Un payload que no valida lanza en desarrollo y se publica como
    `event.malformed` en la demo: nunca tumba el proceso."""
    global _seq, _t_sim
    model = PAYLOAD_MODELS.get(ev.type)
    if model is not None:
        try:
            model.model_validate(ev.payload)
        except ValidationError as exc:
            if _strict():
                raise
            ev.payload = {"type": str(ev.type), "error": str(exc), "raw": ev.payload}
            ev.type = EventType.EVENT_MALFORMED
    _seq += 1
    # Se sella en el mismo objeto: quien publica ve el `seq` y puede usarlo en `causes`.
    ev.seq = _seq
    ev.run_id = ev.run_id or _run_id
    if ev.type == EventType.WORLD_TICK:
        _t_sim = float(ev.payload.get("t_sim", _t_sim))
    if _writer is not None:
        _writer(ev)
    for types, queue in _subs:
        if not types or ev.type in types:
            queue.put_nowait(ev)


class _Subscription:
    """Se registra al crearse, no al iterar: no se pierde nada entre medias."""

    def __init__(self, types: frozenset[EventType]) -> None:
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._entry = (types, self._queue)
        _subs.append(self._entry)

    def __aiter__(self) -> _Subscription:
        return self

    async def __anext__(self) -> Event:
        return await self._queue.get()

    def close(self) -> None:
        if self._entry in _subs:
            _subs.remove(self._entry)


def subscribe(*types: EventType) -> AsyncIterator[Event]:
    """Cola propia del suscriptor, filtrada por tipo. Sin tipos, todo."""
    return _Subscription(frozenset(types))


__all__ = [
    "close",
    "configure",
    "current_run_id",
    "current_t_sim",
    "make_event",
    "publish",
    "reset",
    "subscribe",
]
