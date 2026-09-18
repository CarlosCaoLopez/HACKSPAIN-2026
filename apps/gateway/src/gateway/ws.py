"""El canal al dashboard. Un WebSocket que solo empuja eventos.

Al conectar, el servidor envía
`{"kind":"snapshot", "state": WorldState, "plan": Plan|null, "seq": n}`
y a partir de ahí solo `{"kind":"event", "event": Event}` en orden de `seq`.

Si el cliente detecta un hueco en `seq`, pide `GET /api/state` y reinicia. Nada de
reconciliación fina. El dashboard no hace polling de nada.
"""

from fastapi import APIRouter, WebSocket

router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    """Snapshot y luego el chorro. Una cola por cliente: un dashboard lento no
    frena el bus."""
    raise NotImplementedError


def snapshot_frame() -> dict:
    """El primer mensaje, con el estado y el plan vigente."""
    raise NotImplementedError


def event_frame(ev) -> dict:
    """`{"kind":"event", "event": ...}`, tal cual el sobre del bus."""
    raise NotImplementedError
