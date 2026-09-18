"""Nivel 4 · verificación: Plan → list[Violation]. Código puro, determinista.

Cuatro funciones, cada una devuelve None o un `Violation` con texto legible. Si
alguna falla, el `Violation` vuelve al planner como crítica. Dos intentos. A la
tercera se cae al plan del solver con pesos neutros.

Verificadores externos, no el LLM criticándose: iterar con el LLM como su propio
crítico llega a degradar el rendimiento.
"""

from contracts.plan import Plan, Violation
from contracts.world import WorldState


def verify(state: WorldState, plan: Plan) -> list[Violation]:
    """Corre los cuatro. Lista vacía = plan factible."""
    raise NotImplementedError


def route_feasible(state: WorldState, plan: Plan) -> Violation | None:
    """Toda ruta asignada existe en el grafo y no cruza aristas `cut` ni celdas en
    llamas."""
    raise NotImplementedError


def coverage_maintained(state: WorldState, plan: Plan) -> Violation | None:
    """Ningún POI crítico se queda por debajo de su `min_coverage`."""
    raise NotImplementedError


def no_double_booking(state: WorldState, plan: Plan) -> Violation | None:
    """Ninguna unidad en dos tareas."""
    raise NotImplementedError


def civilian_reachable(state: WorldState, plan: Plan) -> Violation | None:
    """Cada grupo de civiles tiene al menos una ruta viva al refugio asignado."""
    raise NotImplementedError
