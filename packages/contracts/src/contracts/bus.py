"""El bus: un `asyncio.Queue` por suscriptor y un journal JSONL.

Estas tres firmas se escriben en la ventana de contrato y no cambian. El cuerpo
lo pone P1: el writer del journal se inyecta (`contracts` no importa de nadie,
tampoco de `journal`).

`publish` hace dos cosas EN ESTE ORDEN: escribe la línea en `runs/<run_id>.jsonl`
y luego reparte. Si algo revienta a mitad de demo, el journal ya tiene el evento.
Sin journal no hay replay.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Protocol

from pydantic import ValidationError

from contracts.events import PAYLOAD_MODELS, Event, EventType, Malformed
from contracts.settings import settings


class Writer(Protocol):
    """La forma del `JournalWriter` de `journal`. El bus depende de la forma, no
    del paquete: `contracts` no importa de nadie."""

    def write(self, ev: Event) -> None: ...

    def close(self) -> None: ...


class _Subscriber:
    __slots__ = ("queue", "types")

    def __init__(self, types: frozenset[EventType]) -> None:
        self.queue: asyncio.Queue[Event] = asyncio.Queue()
        self.types = types  # vacío = todo

    def wants(self, etype: EventType) -> bool:
        return not self.types or etype in self.types


# --- Estado del proceso (un solo run vivo a la vez) ------------------------

_run_id: str = ""
_seq: int = 0
_writer: Writer | None = None
_subscribers: list[_Subscriber] = []


def start_run(run_id: str, writer: Writer) -> None:
    """Arranca un run: fija el `run_id`, inyecta el writer y reinicia el `seq`.
    Lo llama el lifespan del gateway (o un test)."""
    global _run_id, _seq, _writer
    _run_id = run_id
    _seq = 0
    _writer = writer


def reset() -> None:
    """Cierra el writer y limpia suscriptores y estado. Fin de run o teardown."""
    global _run_id, _seq, _writer
    if _writer is not None:
        _writer.close()
    _writer = None
    _run_id = ""
    _seq = 0
    _subscribers.clear()


def current_run_id() -> str:
    """El uuid del run en curso."""
    return _run_id


async def publish(ev: Event) -> None:
    """Sella `seq`, valida el payload contra PAYLOAD_MODELS, escribe al journal y
    reparte. Un payload que no valida lanza en desarrollo y se publica como
    `event.malformed` en la demo: nunca tumba el proceso."""
    global _seq
    _seq += 1
    ev = ev.model_copy(update={"seq": _seq, "run_id": _run_id or ev.run_id})

    model = PAYLOAD_MODELS.get(ev.type)
    if model is None:
        ev = _to_malformed(ev, f"tipo fuera del catálogo: {ev.type}")
    else:
        try:
            model.model_validate(ev.payload)
        except ValidationError as exc:
            # En desarrollo, que salte y se vea. En demo/replay, nunca tumba el
            # proceso: se degrada a un `event.malformed` y el run sigue.
            if settings.vela_mode == "dev":
                raise
            ev = _to_malformed(ev, str(exc))

    # EL ORDEN ES EL INVARIANTE: primero a disco, luego a los suscriptores.
    if _writer is not None:
        _writer.write(ev)
    for sub in _subscribers:
        if sub.wants(ev.type):
            sub.queue.put_nowait(ev)


def _to_malformed(ev: Event, error: str) -> Event:
    """Reetiqueta un evento inválido como `event.malformed` conservando su sobre."""
    payload = Malformed(type=str(ev.type), error=error, raw=ev.payload)
    return ev.model_copy(
        update={
            "type": EventType.EVENT_MALFORMED,
            "payload": payload.model_dump(mode="json"),
            "t_wall": datetime.now(UTC),
        }
    )


async def _drain(sub: _Subscriber) -> AsyncIterator[Event]:
    try:
        while True:
            yield await sub.queue.get()
    finally:
        if sub in _subscribers:
            _subscribers.remove(sub)


def subscribe(*types: EventType) -> AsyncIterator[Event]:
    """Cola propia del suscriptor, filtrada por tipo. Sin tipos, todo."""
    sub = _Subscriber(frozenset(types))
    _subscribers.append(sub)
    return _drain(sub)
