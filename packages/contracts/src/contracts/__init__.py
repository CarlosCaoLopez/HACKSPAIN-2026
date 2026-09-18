"""`contracts` no es de nadie: se cambia con los cuatro delante de la pantalla.

Todo el mundo importa de aquí. Nadie importa de nadie más. Excepción única:
`apps/gateway` importa de todos, es su trabajo.

Añadir un campo opcional con valor por defecto es libre (commit + aviso).
Renombrar, cambiar un tipo o hacer obligatorio un campo necesita a los cuatro.
Borrar está prohibido.
"""

from contracts.bus import current_run_id, publish, subscribe
from contracts.calls import CallFacts, CallRequest, CallResult, Fact
from contracts.events import Event, EventType, Verb
from contracts.plan import Assignment, Plan, PlanContext, Policy, Violation
from contracts.scenario import Scenario
from contracts.world import (
    POI,
    Cell,
    CivilianGroup,
    RoadEdge,
    Task,
    Unit,
    Wind,
    WorldState,
)

__all__ = [
    "POI",
    "Assignment",
    "CallFacts",
    "CallRequest",
    "CallResult",
    "Cell",
    "CivilianGroup",
    "Event",
    "EventType",
    "Fact",
    "Plan",
    "PlanContext",
    "Policy",
    "RoadEdge",
    "Scenario",
    "Task",
    "Unit",
    "Verb",
    "Violation",
    "Wind",
    "WorldState",
    "current_run_id",
    "publish",
    "subscribe",
]
