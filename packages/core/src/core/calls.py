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


def route_name(
    route: list[str],
    roads: dict[str, RoadEdge] | None = None,
    aliases: dict[str, str] | None = None,
) -> str:
    """El nombre que se dice por teléfono. Si la ruta pasa por el desvío norte o sur
    (waypoints con `nor`/`sur`), "pista norte"/"pista sur": la pista por la que se
    recorren más waypoints, sin contar el de salida (la unidad puede estar parada en
    una pista cortada y salir por la otra; al molino se llega por la norte y Pueblo A
    aunque su waypoint sea `wp_sur_02`). Empate: el tramo más cercano al destino. Si
    no, cada arista de la ruta por su nombre de calle (`Scenario.road_aliases`,
    alias → id; se usa el primero que apunte a la arista: «pista de pueblo b») y, sin
    alias, su id."""
    hops = route[1:] if len(route) > 1 else route
    sur = sum("sur" in wp.lower() for wp in hops)
    nor = sum("nor" in wp.lower() for wp in hops)
    if sur or nor:
        if sur != nor:
            return "pista sur" if sur > nor else "pista norte"
        for wp in reversed(hops):
            low = wp.lower()
            if "sur" in low:
                return "pista sur"
            if "nor" in low:
                return "pista norte"
    if len(route) < 2:
        return route[0] if route else ""
    names = _alias_by_edge(aliases)
    return ", ".join(_edge_name(a, b, roads, names) for a, b in pairwise(route))


def _alias_by_edge(aliases: dict[str, str] | None) -> dict[str, str]:
    """Mapa inverso arista → PRIMER alias del escenario (el YAML lista primero el
    nombre más natural)."""
    out: dict[str, str] = {}
    for alias, edge_id in (aliases or {}).items():
        out.setdefault(edge_id, alias)
        out.setdefault(road_bare(edge_id), alias)
    return out


def _edge_name(
    a: str, b: str, roads: dict[str, RoadEdge] | None, names: dict[str, str]
) -> str:
    for e in (roads or {}).values():
        if {e.a, e.b} == {a, b}:
            return names.get(e.id) or names.get(road_bare(e.id)) or road_bare(e.id)
    bare = f"{a}-{b}"
    return names.get(bare) or names.get(f"{b}-{a}") or bare


def urgency_of(task: Task) -> Urgency:
    return _SEVERITY_URGENCY.get(task.severity, "medium")


def evacuation_call(
    task: Task,
    poi: POI,
    assignment: Assignment,
    to: str,
    hazard_kind: str,
    roads: dict[str, RoadEdge] | None = None,
    aliases: dict[str, str] | None = None,
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
            "route_name": route_name(assignment.route, roads, aliases),
            "deadline_min": str(deadline),
            "hazard_kind": hazard_name(hazard_kind),
        },
        expect=["confirmation", "headcount"],
    )


__all__ = ["evacuation_call", "hazard_name", "route_name", "unit_name", "urgency_of"]
