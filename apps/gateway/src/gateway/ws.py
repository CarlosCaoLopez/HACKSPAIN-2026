"""El canal al dashboard. Un WebSocket que solo empuja eventos.

Al conectar, el servidor envía
`{"kind":"snapshot", "state": WorldState, "plan": Plan|null, "seq": n}`
y a partir de ahí solo `{"kind":"event", "event": Event}` en orden de `seq`.

Si el cliente detecta un hueco en `seq`, pide `GET /api/state` y reinicia. Nada de
reconciliación fina. El dashboard no hace polling de nada.

**No hay un tercer tipo de frame.** El keepalive son los ping de protocolo que ya
manda uvicorn cada 20 s (`--ws-ping-interval`, su default), no un mensaje nuestro:
inventarse un `{"kind":"ping"}` obligaría al cliente a conocer una forma más.

La mecánica del reparto vive en `hub.py`; aquí solo está el router y el orden del
handshake, que es lo que tiene trampa.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from contracts.events import Event

from gateway.hub import CLOSE_RUN_CHANGED, Client
from gateway.runtime import Runtime

log = logging.getLogger("vela.ws")

router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    """Snapshot y luego el chorro. Una cola por cliente: un dashboard lento no
    frena el bus.

    EL ORDEN DE LOS CUATRO PRIMEROS PASOS ES EL REQUISITO, no un detalle:
    registrar la cola después de capturar el estado pierde los eventos de en medio,
    y capturar sin descartar luego los `seq <=` los duplica. Es el bug clásico de
    este patrón y tiene su propio test (`tests/test_gateway_ws.py`).
    """
    rt: Runtime = ws.app.state.runtime

    await ws.accept()
    client = rt.hub.register()  # 1. la cola ya recoge eventos
    try:
        snap = snapshot_frame(rt)  # 2. capturar estado y plan
        snap_seq: int = snap["seq"]
        snap_run: str | None = rt.hub.run_id
        await ws.send_json(snap)  # 3. enviar el snapshot

        while True:  # 4. el chorro, descartando lo ya contado por el snapshot
            ev = await _next_event(client)
            if ev is None:  # marcado para cierre (backpressure)
                break
            if ev.seq <= snap_seq:
                continue
            if snap_run is not None and ev.run_id != snap_run:
                # Otro run: el snapshot ya no vale. Se cierra y el cliente
                # reconecta pidiendo uno nuevo, que es lo que dice el contrato.
                client.mark_close(CLOSE_RUN_CHANGED, "run_changed")
                break
            await ws.send_json(event_frame(ev))
            client.sent += 1
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 — un cliente no puede tumbar el proceso
        log.exception("cliente del WS caído")
    finally:
        rt.hub.unregister(client)
        # Cerrar puede fallar si el cliente ya se fue: da igual, el hub ya lo soltó.
        with contextlib.suppress(Exception):
            if client.close_code is not None:
                await ws.close(code=client.close_code, reason=client.close_reason)
            else:
                await ws.close()


async def _next_event(client: Client) -> Event | None:
    """El siguiente evento de la cola, o None si hay que cerrar el cliente.

    Espera en la cola y en la bandera de cierre a la vez: un cliente al que el hub
    acaba de echar por backpressure no puede quedarse dormido en `queue.get()`.
    """
    while True:
        if client.closing:
            return None
        try:
            return client.queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        client.wake.clear()
        # Re-chequear tras limpiar la bandera: si algo llegó entre el get_nowait y
        # el clear, el aviso no se pierde.
        if not client.queue.empty() or client.closing:
            continue
        await client.wake.wait()


def snapshot_frame(rt: Runtime) -> dict:
    """El primer mensaje, con el estado y el plan vigente.

    Delega en `Runtime.snapshot`, que es el único sitio donde se construye esta
    forma: `GET /api/state` devuelve exactamente lo mismo.

    (Lleva `rt` explícito en vez de leer un global: así los tests montan su propio
    `Runtime` y el hub se prueba sin levantar un servidor.)
    """
    return rt.snapshot()


def event_frame(ev: Event) -> dict:
    """`{"kind":"event", "event": ...}`, tal cual el sobre del bus.

    Sin añadir ni quitar campos. `causes` se conserva siempre: es la materia prima
    de la cadena llamada → hecho → violación → replan → orden que dibuja el H3.
    """
    return {"kind": "event", "event": ev.model_dump(mode="json")}
