"""Los dos puentes que solo el gateway puede montar. P4. **Encendidos por defecto.**

`docs/interfaces.md` dice que el core publica `action.*` y que «sim y voice ejecutan»,
pero ni `Sim.start` ni `VoiceGateway` declaran suscripción, y `Sim.execute` es un
método público que alguien tiene que llamar. El único que puede importar de sim y de
voice a la vez es el gateway, así que el puente cabe aquí.

Verificado contra `sim/runner.py` y `voice/__init__.py`: `Sim` solo se suscribe a
`world.road.changed` (para repintar) y `VoiceGateway` no se suscribe a nada, así que
sin estos dos puentes ninguna orden del core llega al mundo ni sale ninguna llamada.
Por eso van encendidos; `VELA_BRIDGES=false` los apaga si algún día P2 o P3 suscriben
ellos. La tercera ruta, `call.signal.requested`, **no es un puente**: la consume
`voice.humanlike.signal_dispatcher`, que el gateway arranca como task en `main`.

El seguro sigue puesto: cada `action_id` repetido se cuenta en `GET /api/health` — si
el contador sube, alguien más está ejecutando y hay que apagar uno de los dos.
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
        rt.notes["bridges"] = (
            "APAGADOS (VELA_BRIDGES=false): ninguna acción llega al sim ni sale llamada"
        )
        return
    if "bridges" in rt.tasks:
        return
    rt.spawn("bridges", _run(rt))
    rt.notes["bridges"] = "encendidos · action.requested → sim · call.requested → voice"


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
    try:
        await rt.sim.execute(req.action_id, req.verb, req.args)
    except Exception as exc:
        # `Sim.execute` ya convierte sus fallos en `action.failed`; esto es para lo
        # que se le escape. Si muriera la task, ninguna orden posterior llegaría al
        # mundo y nadie se enteraría hasta el pitch.
        rt.notes["bridges"] = f"sim.execute reventó en {req.action_id}: {exc!r}"
        log.exception("sim.execute reventó en %s", req.action_id)


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
    try:
        await rt.voice.place_call(req)
    except Exception as exc:  # noqa: BLE001 — sin hook o sin red se anota, no se muere
        # Sin `HAPPYROBOT_HOOK_EVACUATION` `happyrobot.trigger` lanza `ValueError`: la
        # llamada no sale, se dice en `/api/health`, y la siguiente lo vuelve a intentar.
        rt.notes["calls"] = f"call.requested {req.task_id} sin salir: {exc!r}"
        log.warning("place_call falló para %s: %r", req.task_id, exc)
