"""HappyRobot: el sistema llama.

Cada workflow empieza por un trigger *incoming hook*: un POST a una URL propia
arranca el workflow y el cuerpo que enviáis define las variables disponibles para
las acciones siguientes. POST con `Content-Type: application/json`.

Meted el `task_id` en `metadata.custom`: es lo que permite casar la llamada con la
tarea sin mantener estado en la plataforma.
"""

from contracts.calls import CallRequest

WORKFLOW_EVACUATION = "evacuation_order"
WORKFLOW_RESOURCE = "resource_request"
WORKFLOW_BROADCAST = "status_broadcast"


async def trigger(req: CallRequest, run_id: str) -> str:
    """Dispara el incoming hook y devuelve el `call_id` en cuanto la plataforma
    acepta, no cuando la llamada termina.

    El cuerpo lleva siempre los mismos campos: `to`, `run_id`, `task_id`,
    `poi_name`, `route_name`, `deadline_min`, `severity`."""
    raise NotImplementedError


def workflow_for(req: CallRequest) -> str:
    """`intent` → workflow. Un intent sin workflow es un error de configuración,
    no de runtime."""
    raise NotImplementedError
