"""Ciclo de vida de las tareas: qué nace, qué cambia y qué se cierra. P1.

`belief` es un fold y no decide nada; aquí vive la política de creación. `sync` es
pura: mira el estado (ya con el evento aplicado) y devuelve las tareas que hay que
publicar como `task.changed`. El loop las publica y las pliega con `belief.apply`,
así el journal y el dashboard ven cada alta y cada cierre.

Reglas, todas deterministas y sin LLM:
- `extinguish`: una por celda `burning`/`at_risk` (dedupe por celda). Se cierra
  cuando la celda deja de arder (`burnt` o de vuelta a `intact`).
- `evacuate`: una por POI de tipo `village` con civiles `exposed`/`warned` mientras
  haya fuego. Severidad por distancia al frente y por sotavento. Se cierra cuando
  una unidad llega a su waypoint o cuando todos sus grupos están `safe`.
- `rescue`: una por POI con un hecho `poi:<id>:immobile > 0` de `kind` `observed` o
  `inferred` (un `assumed_default` no funda una tarea: regla 4). Crítica. Se cierra
  cuando una unidad llega a su waypoint.
"""

from __future__ import annotations

import logging
import math

from contracts.calls import Fact
from contracts.events import Event, EventType, UnitArrived
from contracts.world import POI, Cell, Task, TaskSeverity, Wind, WorldState
from core.solver import RoadGraph

log = logging.getLogger("core.tasks")

HOT: frozenset[str] = frozenset({"burning", "at_risk"})
"""Estados de celda que piden una tarea de extinción."""

EVAC_STATES: frozenset[str] = frozenset({"exposed", "warned"})
"""Civiles que todavía hay que sacar. `evacuating`/`safe` ya van; `trapped` es rescate."""

CRITICAL_DISTANCE_M = 60.0
"""A menos de esto de una celda en llamas, la evacuación es crítica."""

DOWNWIND_DISTANCE_M = 200.0
DOWNWIND_HALF_ANGLE_DEG = 45.0
"""A sotavento (el viento empuja el frente hacia el POI) el radio crítico se amplía."""

WIND_CRITICAL_SPEED = 1.5
"""Celdas por minuto a partir de las cuales una celda en llamas es crítica, no alta."""

ARRIVAL_CLOSES: frozenset[str] = frozenset({"evacuate", "rescue"})
"""Tareas que se cierran al llegar una unidad al waypoint del POI."""


def ext_task_id(cell_id: str) -> str:
    return f"task_ext_{cell_id}"


def evac_task_id(poi_id: str) -> str:
    return f"task_evac_{poi_id}"


def rescue_task_id(poi_id: str) -> str:
    return f"task_rescue_{poi_id}"


def sync(state: WorldState, graph: RoadGraph, ev: Event | None = None) -> list[Task]:
    """Las tareas que hay que crear, actualizar o cerrar dado el estado tras `ev`.
    Devuelve solo lo que cambia respecto a `state.tasks`; lista vacía = nada que
    publicar. Determinista: mismo estado, mismas tareas, en el mismo orden."""
    changes: list[Task] = []
    changes.extend(_extinguish(state))
    changes.extend(_evacuate(state, graph))
    changes.extend(_rescue(state))
    if ev is not None and ev.type == EventType.WORLD_UNIT_ARRIVED:
        changes.extend(_arrivals(state, ev))
    return changes


# --- extinguish -------------------------------------------------------------


def _extinguish(state: WorldState) -> list[Task]:
    out: list[Task] = []
    for cell in sorted(state.cells.values(), key=lambda c: c.id):
        tid = ext_task_id(cell.id)
        existing = state.tasks.get(tid)
        if cell.state in HOT:
            severity = _ext_severity(cell, state.wind)
            if existing is None:
                out.append(
                    Task(
                        id=tid,
                        kind="extinguish",
                        target_cell=cell.id,
                        required_capability="extinguish",
                        severity=severity,
                        created_t=state.t_sim,
                    )
                )
            elif not existing.done and existing.severity != severity:
                out.append(existing.model_copy(update={"severity": severity}))
        elif existing is not None and not existing.done:
            out.append(existing.model_copy(update={"done": True}))
    return out


def _ext_severity(cell: Cell, wind: Wind) -> TaskSeverity:
    if cell.state == "burning":
        return "critical" if wind.speed >= WIND_CRITICAL_SPEED else "high"
    return "medium"


# --- evacuate ---------------------------------------------------------------


def _evacuate(state: WorldState, graph: RoadGraph) -> list[Task]:
    out: list[Task] = []
    burning = [c for c in state.cells.values() if c.state == "burning"]
    for poi in sorted(state.pois.values(), key=lambda p: p.id):
        tid = evac_task_id(poi.id)
        existing = state.tasks.get(tid)
        groups = [g for g in state.civilians.values() if g.poi_id == poi.id]
        if existing is not None:
            if existing.done:
                continue
            if groups and all(g.state == "safe" for g in groups):
                out.append(existing.model_copy(update={"done": True}))
            elif burning:
                severity = _evac_severity(poi, burning, state.wind, graph)
                if _rank(severity) > _rank(existing.severity):
                    out.append(existing.model_copy(update={"severity": severity}))
            continue
        if poi.kind != "village" or not burning:
            continue
        if not any(g.state in EVAC_STATES and g.count > 0 for g in groups):
            continue
        out.append(
            Task(
                id=tid,
                kind="evacuate",
                target_poi=poi.id,
                required_capability="transport",
                severity=_evac_severity(poi, burning, state.wind, graph),
                created_t=state.t_sim,
            )
        )
    return out


def _evac_severity(
    poi: POI, burning: list[Cell], wind: Wind, graph: RoadGraph
) -> TaskSeverity:
    """`critical` si el frente está cerca, o a sotavento a distancia media; si no,
    `high`. Nunca menos: hay civiles expuestos y fuego."""
    best_d = math.inf
    downwind = False
    push = (wind.bearing_deg + 180.0) % 360.0  # el viento EMPUJA hacia aquí
    for cell in burning:
        cx, cz = _cell_center(cell, graph)
        dx, dz = poi.x - cx, poi.z - cz
        d = math.hypot(dx, dz)
        best_d = min(best_d, d)
        if wind.speed > 0 and d <= DOWNWIND_DISTANCE_M:
            bearing = math.degrees(math.atan2(dx, -dz)) % 360.0  # 0 = norte (-z)
            if _angular_gap(bearing, push) <= DOWNWIND_HALF_ANGLE_DEG:
                downwind = True
    if best_d <= CRITICAL_DISTANCE_M or downwind:
        return "critical"
    return "high"


def _cell_center(cell: Cell, graph: RoadGraph) -> tuple[float, float]:
    return (
        graph.origin[0] + (cell.cx + 0.5) * graph.cell_size,
        graph.origin[1] + (cell.cz + 0.5) * graph.cell_size,
    )


def _angular_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _rank(severity: str) -> int:
    return _RANK.get(severity, 0)


# --- rescue -----------------------------------------------------------------


def _rescue(state: WorldState) -> list[Task]:
    out: list[Task] = []
    for key, fact in sorted(_latest_facts(state).items()):
        seg = key.split(":")
        if len(seg) != 3 or seg[0] != "poi" or seg[2] != "immobile":
            continue
        if fact.kind == "assumed_default":
            continue  # regla 4: lo asumido no funda una tarea
        if _as_int(fact.value) <= 0:
            continue
        poi_id = seg[1]
        tid = rescue_task_id(poi_id)
        if tid in state.tasks:
            continue
        if poi_id not in state.pois:
            log.warning("rescate sobre un POI que no está en el estado: %s", poi_id)
        out.append(
            Task(
                id=tid,
                kind="rescue",
                target_poi=poi_id,
                required_capability="transport",
                severity="critical",
                created_t=state.t_sim,
            )
        )
    return out


def _latest_facts(state: WorldState) -> dict[str, Fact]:
    latest: dict[str, Fact] = {}
    for f in state.facts:
        latest[f.key] = f
    return latest


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


# --- cierres por llegada ----------------------------------------------------


def _arrivals(state: WorldState, ev: Event) -> list[Task]:
    """`world.unit.arrived` en el waypoint de un POI cierra su evacuación o rescate.
    Si la unidad va asignada a otra tarea todavía abierta, no cierra esta; sin
    asignación (o con una ya cerrada), cualquier tarea de ese POI."""
    ua = UnitArrived.model_validate(ev.payload)
    unit = state.units.get(ua.unit_id)
    busy_with = unit.task_id if unit is not None else None
    if busy_with is not None:
        assigned = state.tasks.get(busy_with)
        if assigned is None or assigned.done:
            busy_with = None
    out: list[Task] = []
    for task in sorted(state.tasks.values(), key=lambda t: t.id):
        if task.done or task.kind not in ARRIVAL_CLOSES or task.target_poi is None:
            continue
        poi = state.pois.get(task.target_poi)
        if poi is None or poi.waypoint_id != ua.waypoint_id:
            continue
        if busy_with is not None and busy_with != task.id:
            continue
        out.append(task.model_copy(update={"done": True}))
    return out


__all__ = ["evac_task_id", "ext_task_id", "rescue_task_id", "sync"]
