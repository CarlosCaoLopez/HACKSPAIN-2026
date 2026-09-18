"""Nivel 4 · verificación: Plan → list[Violation]. Código puro, determinista.

Cuatro funciones, cada una devuelve None o un `Violation` con texto legible. Si
alguna falla, el `Violation` vuelve al planner como crítica. Dos intentos. A la
tercera se cae al plan del solver con pesos neutros.

Verificadores externos, no el LLM criticándose: iterar con el LLM como su propio
crítico llega a degradar el rendimiento.

`graph` es opcional (precedente de `solve(state, policy, graph=None)`): con él se
comprueba el cruce de celdas en llamas y la ubicación de unidades por waypoint más
cercano; sin él, se degrada a lo que `state.roads` permite (existencia de arista y
cortes), que es puro. La firma documentada `verify(state, plan)` sigue siendo válida.
"""

import math
from collections import deque
from itertools import pairwise

from contracts.plan import Plan, Violation
from contracts.world import WorldState

from core.solver import RoadGraph, _route_crosses_burning


def _live_adj(state: WorldState) -> dict[str, set[str]]:
    """Adyacencia de waypoints con solo las aristas vivas de `state.roads`. Puro,
    no necesita coordenadas."""
    adj: dict[str, set[str]] = {}
    for e in state.roads.values():
        if e.cut:
            continue
        adj.setdefault(e.a, set()).add(e.b)
        adj.setdefault(e.b, set()).add(e.a)
    return adj


def verify(
    state: WorldState, plan: Plan, graph: RoadGraph | None = None
) -> list[Violation]:
    """Corre los cuatro. Lista vacía = plan factible."""
    checks = (
        route_feasible(state, plan, graph),
        coverage_maintained(state, plan, graph),
        no_double_booking(state, plan, graph),
        civilian_reachable(state, plan, graph),
    )
    return [v for v in checks if v is not None]


def route_feasible(
    state: WorldState, plan: Plan, graph: RoadGraph | None = None
) -> Violation | None:
    """Toda ruta asignada existe en el grafo y no cruza aristas `cut` ni celdas en
    llamas."""
    adj = _live_adj(state)
    for a in plan.assignments:
        for x, y in pairwise(a.route):
            if y not in adj.get(x, set()):
                return Violation(
                    verifier="route_feasible",
                    severity="hard",
                    message=f"ruta {x}→{y} cortada (unidad {a.unit_id})",
                    involved=[a.unit_id, x, y],
                )
        if graph is not None and _route_crosses_burning(a.route, state, graph):
            return Violation(
                verifier="route_feasible",
                severity="hard",
                message=f"la ruta de {a.unit_id} cruza una celda en llamas",
                involved=[a.unit_id],
            )
    return None


def coverage_maintained(
    state: WorldState, plan: Plan, graph: RoadGraph | None = None
) -> Violation | None:
    """Ningún POI crítico se queda por debajo de su `min_coverage`."""
    # Objetivo (POI) de cada tarea asignada, para saber a dónde se lleva cada unidad.
    task_poi = {
        a.unit_id: state.tasks[a.task_id].target_poi
        for a in plan.assignments
        if a.task_id in state.tasks
    }

    for poi in state.pois.values():
        if poi.min_coverage <= 0:
            continue
        remaining = 0
        for u in state.units.values():
            if _unit_at_poi(u, poi, graph):
                # Sigue cubriendo salvo que el plan la mande a otro POI.
                dest = task_poi.get(u.id)
                if dest is None or dest == poi.id:
                    remaining += 1
            elif task_poi.get(u.id) == poi.id:
                # Unidad que el plan trae a este POI.
                remaining += 1
        if remaining < poi.min_coverage:
            return Violation(
                verifier="coverage_maintained",
                severity="hard",
                message=(
                    f"cobertura de {poi.name} baja de {poi.min_coverage} a {remaining}"
                ),
                involved=[poi.id],
            )
    return None


def no_double_booking(
    state: WorldState, plan: Plan, graph: RoadGraph | None = None
) -> Violation | None:
    """Ninguna unidad en dos tareas."""
    seen: set[str] = set()
    for a in plan.assignments:
        if a.unit_id in seen:
            return Violation(
                verifier="no_double_booking",
                severity="hard",
                message=f"unidad {a.unit_id} asignada a dos tareas",
                involved=[a.unit_id],
            )
        seen.add(a.unit_id)
    return None


def civilian_reachable(
    state: WorldState, plan: Plan, graph: RoadGraph | None = None
) -> Violation | None:
    """Cada grupo de civiles tiene al menos una ruta viva al refugio asignado."""
    adj = _live_adj(state)
    shelters = {
        p.waypoint_id for p in state.pois.values() if p.kind == "shelter"
    }
    if not shelters:
        return None

    for c in state.civilians.values():
        if c.state not in ("exposed", "warned", "evacuating"):
            continue
        poi = state.pois.get(c.poi_id)
        if poi is None:
            continue
        if not _reaches(adj, poi.waypoint_id, shelters):
            return Violation(
                verifier="civilian_reachable",
                severity="hard",
                message=f"grupo {c.id} sin ruta viva a un refugio",
                involved=[c.id, c.poi_id],
            )
    return None


def _unit_at_poi(u, poi, graph: RoadGraph | None) -> bool:
    """La unidad está en el POI si su waypoint más cercano es el del POI (con grafo)
    o si está pegada a sus coordenadas (sin grafo)."""
    if graph is not None:
        return graph.nearest_waypoint(u.x, u.z) == poi.waypoint_id
    return math.hypot(u.x - poi.x, u.z - poi.z) < 1.0


def _reaches(adj: dict[str, set[str]], start: str, targets: set[str]) -> bool:
    """BFS: ¿alcanza `start` alguno de `targets` por aristas vivas?"""
    if start in targets:
        return True
    seen = {start}
    q: deque[str] = deque([start])
    while q:
        node = q.popleft()
        for nb in adj.get(node, set()):
            if nb in targets:
                return True
            if nb not in seen:
                seen.add(nb)
                q.append(nb)
    return False
