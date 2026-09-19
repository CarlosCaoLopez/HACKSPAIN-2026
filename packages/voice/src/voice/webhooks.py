"""Los routers FastAPI de telefonía. P3 los registra; no toca `main.py`.

Dos rutas, las dos de HappyRobot:

- `POST /webhooks/happyrobot/fact`: el tool `report_fact` del agente, **durante**
  la llamada. Es solo un **disparador**: sus valores no se creen (los rellena el LLM
  del agente, texto libre). Fuerza un tick inmediato de Jev sobre la transcripción,
  que publica los hechos; luego pide a Humalike el ack refinado (1,5 s) y responde.
  El replan arranca con los hechos; nunca espera al ack. Con Jev caído (`--no-jev`),
  degrada explícitamente al camino anterior y los hechos salen como `inferred`.
- `POST /webhooks/happyrobot/call`: inicio y fin de llamada (nodo Webhook del
  workflow o outbound webhook de la plataforma). Al colgar: último tick de Jev
  (`fenic` si Jev no está), `analyze` de Humalike, `call.ended`.

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
from contracts.calls import CallFacts, Fact
from contracts.events import Event, EventType
from contracts.settings import settings
from voice import humanlike, pois
from voice.perception import CallPerception

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


def ack_draft(
    poi_name: str | None,
    road: str | None,
    immobile: int | None,
    injuries: int | None = None,
) -> str:
    """Lo que el agente le repite al vecino. Humalike lo refina si llega a tiempo."""
    parts: list[str] = ["Anotado"]
    if poi_name:
        parts.append(poi_name)
    if road:
        parts.append(f"{road} cortada")
    if immobile:
        parts.append(f"{immobile} personas sin movilidad")
    if injuries:
        parts.append(f"{injuries} heridos")
    return ", ".join(parts) + ". Estoy avisando a los equipos, no cuelgue."


def telegram_bot() -> str | None:
    """El bot al que el vecino puede mandar su ubicación, sin `@`, o None si el canal
    no está configurado (`TELEGRAM_BOT_USERNAME` vacío o `VELA_NO_TELEGRAM`)."""
    if settings.vela_no_telegram:
        return None
    name = settings.telegram_bot_username.strip().lstrip("@")
    return name or None


def telegram_hint(bot: str) -> str:
    """La frase que cierra el hueco de `location_hint` por otro canal: la voz da el
    *qué*, Telegram da el *dónde* exacto (use_cases, beats 3:50 y 4:25)."""
    return (
        f"Si tiene Telegram, mande su ubicación al bot @{bot} "
        "y sabremos exactamente dónde está."
    )


def needs_telegram(resolved_poi_id: str | None) -> bool:
    """Cuando la llamada no ha podido ubicar al vecino contra el escenario: sin
    `location_hint`, o con uno que no resuelve (o resuelve por debajo del umbral de
    percepción, que es lo mismo: `resolved_poi_id` queda a None)."""
    return resolved_poi_id is None


def with_telegram_hint(draft: str, bot: str | None, hint: bool) -> str:
    return f"{draft} {telegram_hint(bot)}" if hint and bot else draft


def ensure_bot_mentioned(message: str, bot: str | None, hint: bool) -> str:
    """Humalike refina el tono, no los datos: si el refinado se ha comido el `@bot`,
    la frase se vuelve a añadir tal cual. Sin el nombre del bot la indicación no sirve
    de nada."""
    if hint and bot and f"@{bot}" not in message:
        return f"{message.rstrip()} {telegram_hint(bot)}"
    return message


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

    if mon.perception.active:
        ack = await _fact_by_jev(mon, params)
    else:
        ack = await _fact_without_jev(mon, session_id, params)
    mon.state.seen_tool_hashes[h] = ack
    return ack


async def _ack(
    mon: humanlike.ConversationMonitor, draft: str, t0: float, n_facts: int
) -> tuple[str, bool]:
    """Humalike refina el borrador y, en paralelo, se espera al replan del core: si el
    plan llega a tiempo, el agente dice en la misma frase qué unidad va. Devuelve
    (mensaje, plan incluido)."""
    t_facts = time.perf_counter()
    message, res, got_plan = await mon.ack_with_plan(draft)
    log.info(
        "tool %s: %d hechos en %.0f ms, ack en %.0f ms (%s%s)",
        mon.state.session_id,
        n_facts,
        (t_facts - t0) * 1000,
        (time.perf_counter() - t0) * 1000,
        "refinado" if res else "borrador",
        ", con plan" if got_plan else ", sin plan aún",
    )
    return message, got_plan


async def _fact_by_jev(mon: humanlike.ConversationMonitor, params: dict) -> dict:
    """El tool como disparador: los valores de `params` no entran al estado. Solo si
    no hay SSE (sin transcripción) se usa lo que dijo el agente como texto del vecino,
    y aun así pasa por Jev, que solo puede elegir ids del escenario."""
    if not mon.state.transcript:
        mon.add_turn("user", _speech_from(params))
    t0 = time.perf_counter()
    await mon.perceive(wait=True)
    cf = mon.perception.call_facts()
    n = mon.perception.facts_published
    road = pois.road_label(cf.road_blocked) if cf.road_blocked else None
    bot, hint = telegram_bot(), needs_telegram(cf.resolved_poi_id)
    draft = with_telegram_hint(
        ack_draft(cf.location_hint, road, cf.people_immobile), bot, hint
    )
    message, got_plan = await _ack(mon, draft, t0, n)
    return {
        "ack": True,
        "resolved_poi_name": cf.location_hint,
        "message": ensure_bot_mentioned(message, bot, hint),
        "facts_published": n,
        "plan_included": got_plan,
        "telegram_hint": bool(hint and bot),
        "telegram_bot": bot,
    }


async def _fact_without_jev(
    mon: humanlike.ConversationMonitor, session_id: str, params: dict
) -> dict:
    """Degradación explícita (`--no-jev`, sin clave o Jev caído): los valores del tool
    se resuelven contra los ids del escenario y salen como `inferred`, sin la
    confianza calibrada de Jev. La restricción dura se sostiene en su dirección segura."""
    log.warning("tool %s sin Jev: hechos del tool como `inferred`", session_id)
    try:
        cf = CallFacts.model_validate(_coerce(params))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    cf.resolved_poi_id = pois.resolve_poi_local(cf.location_hint)
    name = pois.poi_name(cf.resolved_poi_id)
    t0 = time.perf_counter()
    facts = _gateway().to_facts(cf, current_t_sim(), session_id)
    for f in facts:
        await publish(_fact_event(f, session_id))
        mon.state.tool_facts_keys.add(f.key)
    if not mon.state.transcript:
        mon.add_turn("user", _pseudo_turn(cf))
    edge = pois.resolve_edge_local(cf.road_blocked)
    road = pois.road_label(edge) if edge else None
    bot, hint = telegram_bot(), needs_telegram(cf.resolved_poi_id)
    draft = with_telegram_hint(
        ack_draft(name, road, cf.people_immobile, cf.injuries), bot, hint
    )
    message, got_plan = await _ack(mon, draft, t0, len(facts))
    return {
        "ack": True,
        "resolved_poi_name": name,
        "message": ensure_bot_mentioned(message, bot, hint),
        "facts_published": len(facts),
        "plan_included": got_plan,
        "telegram_hint": bool(hint and bot),
        "telegram_bot": bot,
    }


def _fact_event(f: Fact, session_id: str) -> Event:
    return make_event(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": f.key,
            "value": f.value,
            "confidence": f.confidence,
            "source": f.source,
            "severity": f.severity,
            "kind": f.kind,
            "call_id": f.call_id or session_id,
        },
        source=f"call:{session_id}",
        t_sim=f.t_sim,
    )


def _speech_from(params: dict) -> str:
    """El texto que el agente reporta, como si lo hubiera dicho el vecino."""
    parts = []
    if params.get("location_hint"):
        parts.append(f"estoy en {params['location_hint']}")
    if params.get("road_blocked"):
        parts.append(f"{params['road_blocked']} está cortada")
    if params.get("people_immobile"):
        parts.append(f"hay {params['people_immobile']} personas que no pueden moverse")
    return ", ".join(parts) or "necesito ayuda"


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

    mon = humanlike.MONITORS.get(result.call_id)

    # Último tick sobre la transcripción completa: cierra el catálogo. Con monitor en
    # vivo el estado ya viene de los ticks; sin él, un tick único sobre la transcripción.
    if mon is not None and mon.perception.active:
        if not result.transcript and mon.state.transcript:
            result.transcript = mon.transcript_text()
        await mon.perceive(final=True)
        result.facts = mon.perception.call_facts()
    elif result.transcript and CallPerception(result.call_id).active:
        cp = CallPerception(result.call_id)
        await cp.tick(humanlike.turns_from_text(result.transcript), final=True)
        result.facts = cp.call_facts()
    elif result.transcript:
        result.facts = await _extract_without_jev(result, mon)

    # Humalike audita la llamada. Si falla, el CallResult va sin nota.
    analysis = None
    if mon is not None:
        if not result.transcript and mon.state.transcript:
            result.transcript = mon.transcript_text()
        analysis = await mon.close()
        humanlike.forget(result.call_id)
    elif result.transcript:
        # Saliente (sin monitor): se audita igual sobre la transcripción del webhook.
        hl, _ = humanlike.clients()
        analysis = await hl.analyze(humanlike.turns_from_text(result.transcript))
    if analysis:
        result.analysis = analysis
        score = analysis.get("health_score")
        result.health_score = float(score) if isinstance(score, (int, float)) else None

    await publish(
        make_event(EventType.CALL_ENDED, result.model_dump(mode="json"), source="voice")
    )
    return {"ok": True, "health_score": result.health_score}


async def _extract_without_jev(result, mon) -> CallFacts | None:
    """`--no-jev`: `fenic.semantic.extract` con `Literal` sobre las opciones del
    escenario. Se conserva el espacio cerrado y se pierden la confianza calibrada y el
    bucle en llamada; los hechos salen `inferred`."""
    gateway = _gateway()
    try:
        facts = await asyncio.wait_for(
            gateway.extract(result.transcript), humanlike.EXTRACT_TIMEOUT_S
        )
    except TimeoutError:
        return None
    if facts is None:
        return None
    if facts.resolved_poi_id is None:
        facts.resolved_poi_id = pois.resolve_poi_local(facts.location_hint)
    already = mon.state.tool_facts_keys if mon else set()
    for f in gateway.to_facts(facts, current_t_sim(), result.call_id):
        if f.key not in already:
            await publish(_fact_event(f, result.call_id))
    return facts
