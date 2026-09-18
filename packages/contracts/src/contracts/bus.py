"""El bus: un `asyncio.Queue` por suscriptor y un journal JSONL.

Estas tres firmas se escriben en la ventana de contrato y no cambian. El cuerpo
lo pone P1: el writer del journal se inyecta (`contracts` no importa de nadie,
tampoco de `journal`).

`publish` hace dos cosas EN ESTE ORDEN: escribe la línea en `runs/<run_id>.jsonl`
y luego reparte. Si algo revienta a mitad de demo, el journal ya tiene el evento.
Sin journal no hay replay.
"""

from collections.abc import AsyncIterator

from contracts.events import Event, EventType


async def publish(ev: Event) -> None:
    """Sella `seq`, valida el payload contra PAYLOAD_MODELS, escribe al journal y
    reparte. Un payload que no valida lanza en desarrollo y se publica como
    `event.malformed` en la demo: nunca tumba el proceso."""
    raise NotImplementedError


def subscribe(*types: EventType) -> AsyncIterator[Event]:
    """Cola propia del suscriptor, filtrada por tipo. Sin tipos, todo."""
    raise NotImplementedError


def current_run_id() -> str:
    """El uuid del run en curso."""
    raise NotImplementedError
