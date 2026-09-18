"""El detector de divergencia. La pieza que puntúa en Adaptación.

Cada tick se reevalúan las suposiciones del plan vigente contra el estado real:
`divergence = suposiciones_rotas_ponderadas / total_ponderado`.

Por encima de 0,25, replan. Además hay dos disparadores inmediatos: violación de
restricción dura, y llegada de un hecho con `severity: critical`.

El valor va al dashboard como una línea que sube y cruza el umbral justo antes del
banner rojo.
"""

from contracts.calls import Fact
from contracts.plan import PlanContext
from contracts.world import WorldState


def divergence(state: WorldState, ctx: PlanContext) -> tuple[float, list[str]]:
    """(valor, claves de las suposiciones rotas). Puro."""
    raise NotImplementedError


def should_replan(
    value: float, violations_hard: int, critical_facts: list[Fact]
) -> tuple[bool, str]:
    """(bandera, motivo). El motivo va al banner y al prompt del planner tal cual.

    Si esto devuelve False no se llama al modelo, y eso es lo que mantiene el coste
    y la latencia bajo control."""
    raise NotImplementedError


def evaluate_assumption(state: WorldState, key: str) -> str | float | bool | None:
    """El valor actual de una suposición ("road:wp_sur_03-wp_sur_04:open")."""
    raise NotImplementedError
