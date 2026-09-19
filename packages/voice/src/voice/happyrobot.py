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
    body = {
        "run_id": run_id,
        "task_id": req.task_id,
        "to": req.to,
        "poi_name": req.facts.get("poi_name", ""),
        "route_name": req.facts.get("route_name", ""),
        "deadline_min": req.facts.get("deadline_min", ""),
        "hazard_kind": req.facts.get("hazard_kind", ""),
        "severity": req.urgency,
        # Casa la llamada con la tarea sin estado en la plataforma: es de donde el
        # webhook de retorno saca el `task_id`.
        "metadata": {"custom": {"task_id": req.task_id}},
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

    # La plataforma devuelve el identificador de la llamada recién arrancada.
    call_id = data.get("call_id") or data.get("id")
    if not call_id:
        raise ValueError(f"el hook no devolvió call_id: {data!r}")
    return str(call_id)
