"""Lo que el core le pide a voice: la intención de una llamada y las señales en vivo.

El core decide que hay que llamar, a quién y con qué variables de guion; voice elige
plataforma, número saliente y guion. Aquí solo hay funciones puras que traducen un
plan a esas intenciones: nombres humanos de rutas y unidades, y el `CallRequest`.
"""

from __future__ import annotations

import math
import re
from itertools import pairwise

from contracts.calls import CallRequest, Urgency
from contracts.factkeys import road_bare
from contracts.plan import Assignment
from contracts.world import POI, RoadEdge, Task, Unit

DEADLINE_MARGIN_MIN = 5
"""Minutos que se suman a la ETA de la unidad para el plazo que se dicta al vecino."""

HAZARD_NAMES = {
    "wildfire": "incendio forestal",
    "flood": "inundación",
    "blackout": "apagón",
}

UNIT_NAMES = {
    "fire_truck": "camión",
    "ambulance": "ambulancia",
    "drone": "dron",
    "crew": "brigada",
}

_SEVERITY_URGENCY: dict[str, Urgency] = {
    "low": "low",
    "medium": "medium",
    "high": "medium",
    "critical": "critical",
}


def hazard_name(kind: str) -> str:
    return HAZARD_NAMES.get(kind, kind)


def unit_name(unit: Unit) -> str:
    """`unit_truck2` → "camión 2", `unit_ambulance` → "ambulancia"."""
    base = UNIT_NAMES.get(unit.kind, unit.kind)
    m = re.search(r"(\d+)$", unit.id)
    return f"{base} {m.group(1)}" if m else base


def route_name(route: list[str], roads: dict[str, RoadEdge] | None = None) -> str:
    """El nombre que se dice por teléfono. Si la ruta pasa por el desvío norte o sur
    (waypoints con `nor`/`sur`), "pista norte"/"pista sur", decidido por el tramo más
    cercano al destino. Si no, los ids de las aristas de la ruta, unidos."""
    for wp in reversed(route):
        low = wp.lower()
        if "sur" in low:
            return "pista sur"
        if "nor" in low:
            return "pista norte"
    if len(route) < 2:
        return route[0] if route else ""
    return ", ".join(_edge_id(a, b, roads) for a, b in pairwise(route))


def _edge_id(a: str, b: str, roads: dict[str, RoadEdge] | None) -> str:
    for e in (roads or {}).values():
        if {e.a, e.b} == {a, b}:
            return road_bare(e.id)
    return f"{a}-{b}"


def urgency_of(task: Task) -> Urgency:
    return _SEVERITY_URGENCY.get(task.severity, "medium")


def evacuation_call(
    task: Task,
    poi: POI,
    assignment: Assignment,
    to: str,
    hazard_kind: str,
    roads: dict[str, RoadEdge] | None = None,
) -> CallRequest:
    """La orden de evacuación para el POI de una tarea `evacuate` ya asignada."""
    deadline = math.ceil(assignment.eta_s / 60.0) + DEADLINE_MARGIN_MIN
    return CallRequest(
        task_id=task.id,
        poi_id=poi.id,
        to=to,
        audience="resident",
        intent="evacuation_order",
        urgency=urgency_of(task),
        facts={
            "poi_name": poi.name,
            "route_name": route_name(assignment.route, roads),
            "deadline_min": str(deadline),
            "hazard_kind": hazard_name(hazard_kind),
        },
        expect=["confirmation", "headcount"],
    )


__all__ = ["evacuation_call", "hazard_name", "route_name", "unit_name", "urgency_of"]
