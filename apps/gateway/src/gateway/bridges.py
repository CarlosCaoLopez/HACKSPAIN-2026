"""Los dos puentes que solo el gateway puede montar. P4. **Apagados por defecto.**

`docs/interfaces.md` dice que el core publica `action.*` y que «sim y voice ejecutan»,
pero ni `Sim.start` ni `VoiceGateway` declaran suscripción, y `Sim.execute` es un
método público que alguien tiene que llamar. El único que puede importar de sim y de
voice a la vez es el gateway, así que el puente cabe aquí.

**Y precisamente por eso es peligroso.** Si P2 se suscribe dentro de `sim` y además
monto el puente, cada acción se ejecuta dos veces: el camión sale dos veces en
Minecraft y eso se ve en el pitch. Hasta que Luis y Hugo lo confirmen, esto solo se
monta con `VELA_BRIDGES=true`, y mientras tanto se cuenta cada `action_id` repetido
en `GET /api/health` — si el contador sube, el puente está duplicado.

Cuando se confirme: o se borra este fichero, o se quita el flag. No las dos.
"""

from __future__ import annotations

import logging

from contracts.calls import CallRequest
from contracts.events import ActionRequested, Event, EventType
from contracts.settings import settings

from gateway.runtime import Runtime

log = logging.getLogger("vela.bridges")


def mount_bridges(rt: Runtime) -> None:
    """Suscribe los puentes si el flag está encendido. Idempotente."""
    if not settings.vela_bridges:
        rt.notes["bridges"] = "apagados (VELA_BRIDGES=false) · pendiente de P2 y P3"
        return
    if "bridges" in rt.tasks:
        return
    rt.spawn("bridges", _run(rt))
    rt.notes["bridges"] = "ENCENDIDOS · si sim o voice también suscriben, doble ejecución"


async def _run(rt: Runtime) -> None:
    from contracts.bus import subscribe

    seen: set[str] = set()
    async for ev in subscribe(EventType.ACTION_REQUESTED, EventType.CALL_REQUESTED):
        if ev.type == EventType.ACTION_REQUESTED:
            await _execute(rt, ev, seen)
        else:
            await _place_call(rt, ev)


async def _execute(rt: Runtime, ev: Event, seen: set[str]) -> None:
    if rt.sim is None:
        return
    try:
        req = ActionRequested.model_validate(ev.payload)
    except Exception as exc:  # noqa: BLE001 — un payload malo no para el puente
        log.warning("action.requested no valida en seq=%s: %r", ev.seq, exc)
        return
    if req.action_id in seen:
        # La señal de que el puente está duplicado. Se cuenta, no se oculta.
        rt.duplicate_actions[req.action_id] = rt.duplicate_actions.get(req.action_id, 0) + 1
        log.error("action_id repetido: %s · ¿puente duplicado?", req.action_id)
        return
    seen.add(req.action_id)
    await rt.sim.execute(req.action_id, req.verb, req.args)


async def _place_call(rt: Runtime, ev: Event) -> None:
    if rt.voice is None:
        return
    try:
        req = CallRequest.model_validate(ev.payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("call.requested no valida en seq=%s: %r", ev.seq, exc)
        return
    # `place_call` devuelve en cuanto la plataforma acepta: nadie espera a una
    # llamada de forma bloqueante, el resultado llega por evento.
    await rt.voice.place_call(req)
