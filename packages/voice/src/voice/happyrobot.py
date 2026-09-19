"""HappyRobot: el sistema llama.

Cada workflow empieza por un trigger *incoming hook*: un POST a una URL propia
arranca el workflow y el cuerpo que enviáis define las variables disponibles para
las acciones siguientes. POST con `Content-Type: application/json`.

Meted el `task_id` en `metadata.custom`: es lo que permite casar la llamada con la
tarea sin mantener estado en la plataforma.
"""

import httpx

from contracts.calls import CallRequest
from contracts.settings import settings

# `intent` (CallRequest) → nombre del workflow saliente en HappyRobot.
WORKFLOW_EVACUATION = "evacuation_order"
WORKFLOW_RESOURCE = "resource_request"
WORKFLOW_BROADCAST = "status_broadcast"

_INTENT_WORKFLOW: dict[str, str] = {
    "evacuation_order": WORKFLOW_EVACUATION,
    # El aviso al pueblo vecino sale por el mismo workflow y el mismo número que la
    # orden de evacuación: solo hay un hook y un número saliente configurados. El
    # guion se distingue por la variable `role` que lleva el cuerpo del hook, no por
    # el workflow (ver `core.calls`: ahí vive la rama, no en la plataforma).
    "neighbor_alert": WORKFLOW_EVACUATION,
    "resource_request": WORKFLOW_RESOURCE,
    "status_check": WORKFLOW_BROADCAST,
    "shelter_confirm": WORKFLOW_BROADCAST,
}

# Solo hay hook de evacuación en el entorno; los demás workflows se cablean cuando
# tengan su propia URL. Un workflow sin hook es error de configuración.
_WORKFLOW_HOOK: dict[str, str] = {
    WORKFLOW_EVACUATION: settings.happyrobot_hook_evacuation,
}

# El token compartido va en la misma cabecera que valida el webhook entrante
# (`voice/webhooks.py`), para que un escaneo aleatorio no dispare el workflow.
TOKEN_HEADER = "X-Vela-Token"

TRIGGER_TIMEOUT_S = 10.0


def workflow_for(req: CallRequest) -> str:
    """`intent` → workflow. Un intent sin workflow es un error de configuración,
    no de runtime."""
    try:
        return _INTENT_WORKFLOW[req.intent]
    except KeyError as exc:
        raise ValueError(f"intent sin workflow configurado: {req.intent!r}") from exc


async def trigger(req: CallRequest, run_id: str) -> str:
    """Dispara el incoming hook y devuelve el `call_id` en cuanto la plataforma
    acepta, no cuando la llamada termina.

    El cuerpo lleva siempre los mismos campos: `to`, `run_id`, `task_id`,
    `poi_name`, `route_name`, `deadline_min`, `severity`."""
    workflow = workflow_for(req)
    hook_url = _WORKFLOW_HOOK.get(workflow, "")
    if not hook_url:
        raise ValueError(f"workflow sin incoming hook configurado: {workflow!r}")

    # Las variables del guion salen de `req.facts`; el resto, de la intención.
    # `situation_brief` y `checklist` llevan la rama condicional ya resuelta (quién
    # es, qué pueblo arde, qué hay que preguntar): el prompt de la plataforma es el
    # mismo para una orden de evacuación y para un aviso al vecino, y la diferencia
    # la trae el cuerpo del hook, no dos workflows.
    body = {
        "run_id": run_id,
        "task_id": req.task_id,
        "poi_id": req.poi_id,
        "to": req.to,
        "role": req.facts.get("role", "evacuation"),
        "poi_name": req.facts.get("poi_name", ""),
        "source_poi_name": req.facts.get("source_poi_name", ""),
        "route_name": req.facts.get("route_name", ""),
        "deadline_min": req.facts.get("deadline_min", ""),
        "hazard_kind": req.facts.get("hazard_kind", ""),
        "situation_brief": req.facts.get("situation_brief", ""),
        "checklist": req.facts.get("checklist", ""),
        "advice_rules": req.facts.get("advice_rules", ""),
        # El estado del mundo en el segundo en que se pide la llamada: es lo que
        # convierte al agente en un operador que recomienda con datos y no en un
        # contestador con un guion.
        "resources": req.facts.get("resources", ""),
        "fire_status": req.facts.get("fire_status", ""),
        "roads_status": req.facts.get("roads_status", ""),
        "unit_eta": req.facts.get("unit_eta", ""),
        "incoming_people": req.facts.get("incoming_people", ""),
        "severity": req.urgency,
        # Casa la llamada con la tarea sin estado en la plataforma: es de donde el
        # webhook de retorno saca el `task_id`.
        "metadata": {"custom": {"task_id": req.task_id, "poi_id": req.poi_id}},
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.happyrobot_api_key}",
        TOKEN_HEADER: settings.webhook_shared_token,
    }

    async with httpx.AsyncClient(timeout=TRIGGER_TIMEOUT_S) as client:
        resp = await client.post(hook_url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # La plataforma devuelve el `run_id` del workflow recién arrancado (medido el
    # sábado: `{"run_id": ..., "queued_run_ids": [...], "status": "workflow started"}`).
    # Es lo que casa con el webhook de fin de llamada vía `trigger.run_id`.
    call_id = data.get("call_id") or data.get("id") or data.get("run_id")
    if not call_id:
        raise ValueError(f"el hook no devolvió call_id: {data!r}")
    return str(call_id)
