"""Pausa, override e inject manual. P4 es el único que publica `human.override`.

La intervención humana es requisito obligatorio del reto y la prueba visible del
criterio *Control*. Usadla en la demo al menos una vez, en directo.

Tres reglas que explican por qué este fichero es como es:

1. **Los tres endpoints devuelven la misma forma** (`ControlResponse`). Un solo parser
   en el dashboard y un solo sitio donde mirar cuando un botón no hace lo que esperaba.
2. **El gateway no fabrica eventos del mundo.** `world.inject` lo emite el sim
   (catálogo de `docs/interfaces.md`): sin sim esto responde 503 y no se inventa un
   evento con `source: "sim"`. Lo único que P4 emite por su cuenta es `human.override`,
   que es suyo por contrato, y —con `VELA_FEEDS≠off`— observaciones de fuentes externas
   (`gateway/feeds`, SPEC-007), publicadas con su propio `source` y nunca como `sim`.
3. **En replay, el override no entra en el chorro.** Se acepta, se contesta con
   `echo: true` y ahí se queda: el hub lo numeraría con `last_seq + 1`, que es el `seq`
   que trae el siguiente evento del journal, y el cliente lo leería como hueco, pediría
   `GET /api/state` y se quedaría sin historia en pantalla — justo al pulsar el botón.
   En replay, por el WS solo viaja el journal.
"""

from __future__ import annotations

import inspect
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from contracts.events import EventType, HumanOverride, OverrideKind
from gateway.runtime import Rt

log = logging.getLogger("vela.control")

router = APIRouter(prefix="/control", tags=["control"])


class InjectBody(BaseModel):
    inject_type: str
    payload: dict = {}


class OverrideBody(BaseModel):
    kind: OverrideKind
    target: str  # "unit_truck1" | "task_evac_a" | "road:rd_sur01_sur02"
    value: str | float | bool | None = None
    note: str = ""


class ControlResponse(BaseModel):
    """La forma única de los tres endpoints.

    `published` es si el evento salió de verdad (bus o hub); `echo` es si solo se ha
    reconocido en local (replay). Los dos a la vez nunca son ciertos.
    """

    accepted: bool
    published: bool = False
    echo: bool = False
    run_id: str | None = None
    seq: int | None = None
    detail: str = ""


@router.post("/override")
async def post_override(body: OverrideBody, rt: Rt) -> ControlResponse:
    """Se publica como `human.override` y el core lo trata con prioridad máxima.

    Un `kind` fuera de los cinco de `OverrideKind` lo rechaza Pydantic con un 422 antes
    de llegar aquí, que es exactamente lo que se quiere: la lista de kinds es contrato.
    """
    hint = _target_hint(body.kind, body.target)

    if rt.mode == "replay":
        # Ni `publish` ni `hub.dispatch`: ver la regla 3 de la cabecera.
        return ControlResponse(
            accepted=True,
            echo=True,
            run_id=rt.run_id,
            detail=_join("eco local (replay): no se publica ni se journalea", hint),
        )

    ev = await rt.publish(
        EventType.HUMAN_OVERRIDE,
        HumanOverride(
            kind=body.kind, target=body.target, value=body.value, note=body.note
        ),
        "human",
    )
    log.info("override %s sobre %s · seq=%s", body.kind, body.target, ev.seq)
    return ControlResponse(
        accepted=True, published=True, run_id=ev.run_id, seq=ev.seq, detail=hint
    )


@router.post("/inject")
async def post_inject(body: InjectBody, rt: Rt) -> ControlResponse:
    """Dispara un inject a mano. En el ensayo se usa constantemente; en el pitch
    es el botón de emergencia si algo se retrasa."""
    if rt.mode == "replay":
        raise HTTPException(409, "en modo replay no hay sim al que inyectar")
    if rt.sim is None:
        # `world.inject` es del sim. Sin sim, esto falla a la vista y no se falsifica:
        # un mundo inventado en pantalla es peor que un botón que dice que no puede.
        raise HTTPException(503, "sim ausente: no hay quien ejecute el inject")

    await rt.sim.inject(body.inject_type, body.payload)
    log.info("inject %s · %s", body.inject_type, body.payload)
    return ControlResponse(
        accepted=True,
        published=True,
        run_id=rt.run_id,
        detail=f"{body.inject_type} entregado al sim; el evento lo emite él",
    )


@router.post("/pause")
async def post_pause(rt: Rt, paused: bool = True) -> ControlResponse:
    """Congela el tick, para explicar algo en el pitch. Idempotente.

    En replay la pausa es completa: `replay_source` deja de emitir mientras la bandera
    esté puesta, sin saltarse eventos ni comprimir el tiempo al reanudar. En demo hace
    falta que el sim sepa pausar, y hoy `Sim` no expone `pause()`.
    """
    rt.paused = paused
    detail = "replay detenido"

    if rt.mode != "replay":
        pause_sim = getattr(rt.sim, "pause", None)
        if rt.sim is None:
            detail = "sin sim: solo se marca la bandera"
        elif pause_sim is None:
            # Degradación explícita: `Sim` no expone `pause()` todavía (pedido a P2).
            # Se dice en la respuesta en vez de fingir que el mundo se ha parado.
            detail = "sim sin pause(): solo se marca la bandera"
        else:
            result = pause_sim(paused)
            if inspect.isawaitable(result):  # P2 decidirá si es async; las dos valen
                await result
            detail = "sim pausado"

    log.info("pausa = %s · %s", paused, detail)
    return ControlResponse(
        accepted=True, published=False, run_id=rt.run_id, detail=f"paused={paused} · {detail}"
    )


# --- la forma del target ---------------------------------------------------------


def _target_hint(kind: OverrideKind, target: str) -> str:
    """Un aviso si el `target` no tiene la forma que espera ese `kind`. **Nunca rechaza.**

    `docs/interfaces.md` da tres ejemplos de `target` y ninguno es un par unidad-tarea,
    pero vetar una asignación *es* vetar un par: aquí se espera `unit:task` y se avisa
    si no lo parece. Avisar y no rechazar es deliberado — un 400 en mitad del pitch por
    un id mal escrito es peor que un override que el core acabe descartando, y quien
    decide qué targets entiende es el core, no yo.
    """
    if kind in ("veto_assignment", "force_assignment"):
        unit, _, task = target.partition(":")
        if not (unit.startswith("unit_") and task.startswith("task_")):
            return f"aviso: se esperaba unit_*:task_*, llegó «{target}»"
    elif kind == "set_priority":
        if not target.startswith("task_"):
            return f"aviso: se esperaba task_*, llegó «{target}»"
    elif kind == "assert_fact":
        if ":" not in target:
            return f"aviso: se esperaba una clave de hecho con «:», llegó «{target}»"
    return ""


def _join(*parts: str) -> str:
    return " · ".join(p for p in parts if p)
