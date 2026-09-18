"""Los routers FastAPI de telefonía. P3 los registra; no toca `main.py`.

Dos rutas, las dos de HappyRobot:

- `POST /webhooks/happyrobot/fact`: el tool `report_fact` del agente, **durante**
  la llamada. Publica los hechos, luego pide a Humalike el ack refinado (1,5 s),
  y responde. El replan arranca con los hechos; nunca espera al ack.
- `POST /webhooks/happyrobot/call`: inicio y fin de llamada (nodo Webhook del
  workflow o outbound webhook de la plataforma). Al colgar: `semantic.extract`
  como red de seguridad, `analyze` de Humalike, `call.ended`.

Autenticación: token compartido en la cabecera, porque estas dos rutas van por un
túnel y un escaneo aleatorio no debe disparar nada a mitad del pitch.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from contracts.bus import current_run_id, current_t_sim, make_event, publish
from contracts.calls import CallFacts
from contracts.events import EventType
from contracts.settings import settings
from voice import humanlike, pois

log = logging.getLogger("voice.webhooks")

router = APIRouter(prefix="/webhooks", tags=["voice"])

TOKEN_HEADER = "X-Vela-Token"


def _check_token(token: str) -> None:
    expected = settings.webhook_shared_token
    if expected and token != expected:
        raise HTTPException(status_code=401, detail="token")


def _gateway():
    from voice import VoiceGateway

    return VoiceGateway()


def ack_draft(cf: CallFacts, poi_name: str | None) -> str:
    """Lo que el agente le repite al vecino. Humalike lo refina si llega a tiempo."""
    parts: list[str] = ["Anotado"]
    if poi_name:
        parts.append(poi_name)
    elif cf.location_hint:
        parts.append(cf.location_hint)
    if cf.road_blocked:
        parts.append(f"{cf.road_blocked} cortada")
    if cf.people_immobile:
        parts.append(f"{cf.people_immobile} personas sin movilidad")
    if cf.injuries:
        parts.append(f"{cf.injuries} heridos")
    return ", ".join(parts) + ". Estoy avisando a los equipos, no cuelgue."


@router.post("/happyrobot/fact")
async def happyrobot_fact(
    request: Request, x_vela_token: str = Header(default="")
) -> dict:
    """El tool `report_fact`, en llamada. Cuerpo del nodo Webhook del tool:
    `{"session_id": "{{session_id}}", "run_id": "{{run_id}}", "params": {...}}`.
    Si `params` no viene, el cuerpo entero son los parámetros."""
    _check_token(x_vela_token)
    body = await request.json()
    session_id = str(body.get("session_id") or body.get("call_id") or "")
    if not session_id:
        raise HTTPException(status_code=422, detail="session_id")
    run_id = str(body.get("run_id") or current_run_id())
    params = body.get("params")
    if not isinstance(params, dict):
        params = {
            k: v for k, v in body.items() if k not in ("session_id", "run_id", "call_id")
        }

    mon = humanlike.get_or_start(session_id, run_id)
    cb = params.pop("callback_number", None) or body.get("caller_number")
    if cb and str(cb).strip() and str(cb) != "web":
        mon.state.callback_number = str(cb).strip()
    h = mon.tool_hash(params)
    cached = mon.state.seen_tool_hashes.get(h)
    if cached is not None:
        return cached

    try:
        cf = CallFacts.model_validate(_coerce(params))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    cf.resolved_poi_id = pois.resolve_poi_local(cf.location_hint)
    name = pois.poi_name(cf.resolved_poi_id)

    t0 = time.perf_counter()
    # 1. Los hechos, todos publicados antes de tocar Humalike.
    facts = _gateway().to_facts(cf, current_t_sim(), session_id)
    seqs: list[int] = []
    for f in facts:
        ev = make_event(
            EventType.WORLD_FACT_ASSERTED,
            {
                "key": f.key,
                "value": f.value,
                "confidence": f.confidence,
                "source": f.source,
                "severity": f.severity,
            },
            source=f"call:{session_id}",
            t_sim=f.t_sim,
        )
        await publish(ev)
        seqs.append(ev.seq)
        mon.state.tool_facts_keys.add(f.key)

    # 2. El ack: Humalike lo refina y, en paralelo, se espera al replan del core.
    #    Si el plan llega a tiempo, el agente dice en la misma frase qué unidad va.
    if not mon.state.transcript:
        # Sin SSE (sin API key de plataforma) el único texto es el del tool.
        mon.add_turn("user", _pseudo_turn(cf))
    draft = ack_draft(cf, name)
    t_facts = time.perf_counter()
    message, res, got_plan = await mon.ack_with_plan(draft)
    log.info(
        "tool %s: %d hechos en %.0f ms, ack en %.0f ms (%s%s)",
        session_id,
        len(facts),
        (t_facts - t0) * 1000,
        (time.perf_counter() - t0) * 1000,
        "refinado" if res else "borrador",
        ", con plan" if got_plan else ", sin plan aún",
    )

    ack = {
        "ack": True,
        "resolved_poi_name": name,
        "message": message,
        "facts_published": len(facts),
        "plan_included": got_plan,
    }
    mon.state.seen_tool_hashes[h] = ack
    return ack


def _pseudo_turn(cf: CallFacts) -> str:
    parts = []
    if cf.location_hint:
        parts.append(f"estoy en {cf.location_hint}")
    if cf.road_blocked:
        parts.append(f"{cf.road_blocked} está cortada")
    if cf.people_immobile:
        parts.append(f"hay {cf.people_immobile} personas que no pueden moverse")
    if cf.injuries:
        parts.append(f"hay {cf.injuries} heridos")
    return ", ".join(parts) or "necesito ayuda"


def _coerce(params: dict) -> dict:
    """Los tools mandan strings; `CallFacts` quiere ints. Vacíos → None."""
    out: dict = {}
    for k, v in params.items():
        if v in ("", None, "null"):
            continue
        if k in ("people_immobile", "injuries") and isinstance(v, str):
            digits = "".join(c for c in v if c.isdigit())
            out[k] = int(digits) if digits else None
        elif k == "confirmed_order" and isinstance(v, str):
            out[k] = v.strip().lower() in ("true", "sí", "si", "yes", "1")
        else:
            out[k] = v
    return out


@router.post("/happyrobot/call")
async def happyrobot_call(
    request: Request, x_vela_token: str = Header(default="")
) -> dict:
    """Inicio y fin de llamada, ambas direcciones. `type` es `start` o `end`.

    Al colgar, idempotente por `call_id`: los webhooks duplicados pasan.
    """
    _check_token(x_vela_token)
    body = await request.json()
    kind = str(body.get("type") or body.get("event") or "end").lower()
    if kind in ("start", "started", "call.started"):
        return await _on_start(body)
    return await _on_end(body)


async def _on_start(body: dict) -> dict:
    session_id = str(body.get("session_id") or body.get("call_id") or "")
    run_id = str(body.get("run_id") or current_run_id())
    if not session_id:
        raise HTTPException(status_code=422, detail="session_id")
    mon = humanlike.get_or_start(session_id, run_id)
    await publish(
        make_event(
            EventType.CALL_STARTED,
            {
                "call_id": mon.state.call_id,
                "task_id": body.get("task_id"),
                "to": str(body.get("to") or body.get("caller_number") or ""),
                "direction": body.get("direction") or "inbound",
            },
            source="voice",
        )
    )
    return {"ok": True}


async def _on_end(body: dict) -> dict:
    result = humanlike.parse_webhook(body)
    if not result.call_id:
        raise HTTPException(status_code=422, detail="session_id")
    if humanlike.is_duplicate(result.call_id):
        return {"dup": True}

    gateway = _gateway()
    mon = humanlike.MONITORS.get(result.call_id)

    # Red de seguridad: lo que el tool no haya asertado ya.
    if result.transcript:
        try:
            result.facts = await asyncio.wait_for(
                gateway.extract(result.transcript), humanlike.EXTRACT_TIMEOUT_S
            )
        except TimeoutError:
            result.facts = None
    if result.facts is not None:
        if result.facts.resolved_poi_id is None:
            result.facts.resolved_poi_id = pois.resolve_poi_local(
                result.facts.location_hint
            )
        already = mon.state.tool_facts_keys if mon else set()
        for f in gateway.to_facts(result.facts, current_t_sim(), result.call_id):
            if f.key in already:
                continue
            await publish(
                make_event(
                    EventType.WORLD_FACT_ASSERTED,
                    {
                        "key": f.key,
                        "value": f.value,
                        "confidence": f.confidence,
                        "source": f.source,
                        "severity": f.severity,
                    },
                    source=f"call:{result.call_id}",
                )
            )

    # Humalike audita la llamada. Si falla, el CallResult va sin nota.
    if mon is not None:
        if not result.transcript and mon.state.transcript:
            result.transcript = mon.transcript_text()
        analysis = await mon.close()
        if analysis:
            result.analysis = analysis
            score = analysis.get("health_score")
            result.health_score = (
                float(score) if isinstance(score, (int, float)) else None
            )
        humanlike.forget(result.call_id)

    await publish(
        make_event(EventType.CALL_ENDED, result.model_dump(mode="json"), source="voice")
    )
    return {"ok": True, "health_score": result.health_score}
