"""Reloj adaptativo del sim: rápido en tiempo muerto, 1× mientras haya llamada viva.

El problema son dos relojes. Un tick es un segundo simulado (`t_sim`) pase lo que
pase; `--speed` solo acorta la espera de pared entre ticks (`sim/runner.py`, D3). Las
retenciones de despacho miden en `t_sim` (`core.loop._expire_holds`), así que a 10×
`DISPATCH_RING_S=45` son 4,5 s de pared. Pero una llamada **real** de HappyRobot tarda
lo que tarda en pared —medido, ~66 s—, y a 10× el hold vence en `t_sim` antes de que
llegue el `call.started`/`call.ended`: la unidad sale con `dispatch_confirmed=False` y
el beat de "pedir antes de mandar" se cae.

La regla: correr a `rt.cruise_speed` en tiempo muerto y bajar a 1× en cuanto hay una
llamada de voz en vuelo —desde que se pide (`call.requested`, el teléfono suena) o
entra una llamada, hasta que cuelga (`call.ended`)—. Con 1× el `t_sim` de la llamada y
el reloj de pared vuelven a coincidir, así que el hold y el presupuesto de percepción
(que va en `time.monotonic`) miden lo mismo que la conversación de verdad.

Solo se monta con llamadas **reales**: con `--mock-calls` el guion enlatado escala solo
con la velocidad (`voice.fake` divide `delay_s / speed`), así que ahí correr rápido no
descuadra nada y no hay que frenar. Habla por el bus y solo toca la superficie pública
`Sim.set_speed`: no importa de nadie por dentro (invariante 4; la excepción del
gateway ni hace falta).
"""

from __future__ import annotations

import inspect
import logging

from contracts.events import EventType
from gateway.runtime import Runtime

log = logging.getLogger("vela.pacing")


def mount_pacing(rt: Runtime) -> None:
    """Arranca el reloj adaptativo si tiene sentido: llamadas reales y crucero > 1×.

    Con `--speed 1` no hay nada que frenar. Con `--mock-calls` el guion ya escala con la
    velocidad, así que frenar sería quitarle al ensayo justo lo que busca. Idempotente.
    """
    if rt.cruise_speed <= 1.0 or rt.calls_mocked or rt.sim is None:
        return
    if "pacing" in rt.tasks:
        return
    rt.spawn("pacing", _run(rt))
    rt.notes["pacing"] = (
        f"reloj adaptativo · {rt.cruise_speed:g}× en tiempo muerto, 1× en llamada"
    )


async def _run(rt: Runtime) -> None:
    from contracts.bus import subscribe

    # Dos ventanas distintas: `_ringing` es el tramo `call.requested`→`call.started`
    # (solo hay `task_id` todavía, el `call_id` lo pone la plataforma al descolgar);
    # `_talking` es `call.started`→`call.ended` (por `call_id`, cubre también la
    # entrante del vecino, que no tiene `call.requested`).
    ringing: set[str] = set()
    talking: set[str] = set()

    async for ev in subscribe(
        EventType.CALL_REQUESTED, EventType.CALL_STARTED, EventType.CALL_ENDED
    ):
        p = ev.payload
        if ev.type == EventType.CALL_REQUESTED:
            if task_id := str(p.get("task_id") or ""):
                ringing.add(task_id)
        elif ev.type == EventType.CALL_STARTED:
            # Telegram entra ya colgado (un pin), no es una conversación que frenar.
            if p.get("channel", "voice") != "voice":
                continue
            ringing.discard(str(p.get("task_id") or ""))
            if call_id := str(p.get("call_id") or ""):
                talking.add(call_id)
        else:  # CALL_ENDED
            ringing.discard(str(p.get("task_id") or ""))
            talking.discard(str(p.get("call_id") or ""))

        await _apply(rt, en_llamada=bool(ringing or talking))


async def _apply(rt: Runtime, *, en_llamada: bool) -> None:
    """1× mientras haya llamada; crucero cuando no queda ninguna. Solo cuando cambia."""
    target = 1.0 if en_llamada else rt.cruise_speed
    sim = rt.sim
    if sim is None or getattr(sim, "speed", None) == target:
        return
    setter = getattr(sim, "set_speed", None)
    if setter is not None:
        result = setter(target)
        if inspect.isawaitable(result):
            await result
    elif isinstance(getattr(sim, "speed", None), int | float):
        sim.speed = target
    else:
        return
    log.info("reloj de sim → %g× (%s)", target, "llamada viva" if en_llamada else "crucero")
    rt.notes["pacing"] = (
        f"reloj adaptativo · {'1× (llamada viva)' if en_llamada else f'{rt.cruise_speed:g}× (crucero)'}"
    )
