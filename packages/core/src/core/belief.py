"""Eventos → WorldState. Puro y testeable sin nada montado.

`WorldState` es inmutable: `apply` devuelve uno nuevo, nadie muta en sitio. Es lo
que permite replayar `fixtures/run_golden.jsonl` entero y comprobar que ningún
evento revienta la validación.
"""

from __future__ import annotations

import logging

from contracts.calls import Fact
from contracts.events import Event, EventType
from contracts.factkeys import validate_fact_key
from contracts.plan import Plan
from contracts.scenario import Scenario
from contracts.world import Cell, CivilianGroup, RoadEdge, Task, Wind, WorldState

log = logging.getLogger("core.belief")

HUMAN_CONFIDENCE = 1.0


def initial_state(run_id: str, scenario: Scenario) -> WorldState:
    """El estado del que parte todo, antes del primer tick."""
    return WorldState(
        run_id=run_id,
        seq=0,
        t_sim=0.0,
        wind=scenario.hazard.wind,
        units={u.id: u for u in scenario.units},
        cells={},
        roads={r.id: r for r in scenario.roads},
        pois={p.id: p for p in scenario.pois},
        civilians={c.id: c for c in scenario.civilians},
        tasks={},
        facts=[],
    )


def apply(state: WorldState, ev: Event) -> WorldState:
    """El estado tras aplicar el evento. Un evento desconocido no lanza: devuelve
    el estado tal cual y se registra."""
    p = ev.payload
    update: dict = {"seq": ev.seq, "t_sim": max(state.t_sim, ev.t_sim)}
    match ev.type:
        case EventType.WORLD_TICK:
            update["t_sim"] = float(p.get("t_sim", ev.t_sim))
            if "wind" in p:
                update["wind"] = Wind.model_validate(p["wind"])
        case EventType.WORLD_CELL_CHANGED:
            cells = dict(state.cells)
            cid = p["cell_id"]
            prev = cells.get(cid)
            cx, cz = _cell_index(cid, prev)
            cells[cid] = Cell(
                id=cid,
                cx=cx,
                cz=cz,
                state=p["state"],
                fuel=prev.fuel if prev else 1.0,
                t_changed=ev.t_sim,
            )
            update["cells"] = cells
        case EventType.WORLD_FIRE_DETECTED:
            cells = dict(state.cells)
            cid = p["cell_id"]
            prev = cells.get(cid)
            cx, cz = _cell_index(cid, prev)
            cells[cid] = Cell(id=cid, cx=cx, cz=cz, state="burning", t_changed=ev.t_sim)
            update["cells"] = cells
        case EventType.WORLD_UNIT_POSITION:
            units = dict(state.units)
            u = units.get(p["unit_id"])
            if u is not None:
                units[u.id] = u.model_copy(update={"x": p["x"], "z": p["z"]})
                update["units"] = units
        case EventType.WORLD_UNIT_STATUS:
            units = dict(state.units)
            u = units.get(p["unit_id"])
            if u is not None:
                units[u.id] = u.model_copy(update={"status": p["status"]})
                update["units"] = units
        case EventType.WORLD_UNIT_ARRIVED:
            units = dict(state.units)
            u = units.get(p["unit_id"])
            if u is not None:
                units[u.id] = u.model_copy(update={"status": "idle"})
                update["units"] = units
        case EventType.WORLD_ROAD_CHANGED:
            roads = dict(state.roads)
            r = roads.get(p["edge_id"])
            if r is not None:
                roads[r.id] = r.model_copy(
                    update={"cut": p["cut"], "cut_cause": p.get("cause")}
                )
                update["roads"] = roads
        case EventType.WORLD_CIVILIANS_CHANGED:
            civ = dict(state.civilians)
            g = civ.get(p["group_id"])
            if g is not None:
                civ[g.id] = g.model_copy(
                    update={
                        "count": p["count"],
                        "state": p["state"],
                        "poi_id": p["poi_id"],
                    }
                )
            else:
                civ[p["group_id"]] = CivilianGroup(
                    id=p["group_id"],
                    poi_id=p["poi_id"],
                    count=p["count"],
                    state=p["state"],
                )
            update["civilians"] = civ
        case EventType.WORLD_FACT_ASSERTED:
            fact = Fact(
                key=p["key"],
                value=p["value"],
                confidence=p["confidence"],
                source=p["source"],
                severity=p["severity"],
                t_sim=ev.t_sim,
                kind=p.get("kind", "observed"),
                call_id=p.get("call_id"),
            )
            return apply_fact(state.model_copy(update=update), fact)
        case EventType.HUMAN_OVERRIDE if p.get("kind") == "assert_fact":
            fact = Fact(
                key=p["target"],
                value=p.get("value") if p.get("value") is not None else True,
                confidence=HUMAN_CONFIDENCE,
                source="human",
                severity="critical",
                t_sim=ev.t_sim,
            )
            return apply_fact(state.model_copy(update=update), fact)
        case EventType.TASK_CHANGED:
            # `core.tasks` decide qué tarea nace o se cierra; aquí solo se pliega.
            task = Task.model_validate(p["task"])
            update["tasks"] = {**state.tasks, task.id: task}
        case EventType.PLAN_EMITTED:
            # La unidad pasa a `moving` solo si tiene camino que hacer: con una ruta
            # de un solo waypoint ya está donde se la quiere (el camión sofoca parado)
            # y su estado lo dice el sim, no el plan.
            plan = Plan.model_validate(p)
            units = dict(state.units)
            for a in plan.assignments:
                u = units.get(a.unit_id)
                if u is not None:
                    status = "moving" if len(a.route) > 1 else u.status
                    units[u.id] = u.model_copy(
                        update={"task_id": a.task_id, "status": status}
                    )
            update["units"] = units
        case EventType.ACTION_COMPLETED | EventType.ACTION_FAILED:
            pass
        case _:
            log.debug("evento sin efecto en el estado: %s", ev.type)
    return state.model_copy(update=update)


def road_of(roads: dict[str, RoadEdge], ref: str) -> RoadEdge | None:
    """La arista de `WorldState.roads` a la que apunta un trozo de clave de hecho.

    `state.roads` se indexa por el id del escenario (`road:wp_a-wp_b`), pero la clave de
    hecho lleva ese mismo id con el prefijo una sola vez, así que su segmento del medio es
    `wp_a-wp_b`. Buscar `roads.get("wp_a-wp_b")` no encuentra nada y —peor— no falla: el
    hecho de corte entraba a `facts` y no cortaba la arista. Se prueba tal cual (ids
    sintéticos, `e1`) y con el prefijo."""
    return roads.get(ref) or roads.get(f"road:{ref}")


def apply_fact(state: WorldState, fact: Fact) -> WorldState:
    """Un hecho con procedencia entra al estado. Clave desconocida (no está en
    `contracts.factkeys`) se registra y no se aplica.

    Un `assert_fact` humano entra con `confidence=1.0` y gana a cualquier hecho de
    llamada sobre la misma clave."""
    expected = validate_fact_key(fact.key)
    if expected is None:
        log.warning("clave fuera del mapa, no se aplica: %s", fact.key)
        return state
    prev = _latest(state.facts, fact.key)
    if prev is not None and prev.source == "human" and fact.source != "human":
        return state.model_copy(update={"facts": [*state.facts, fact]})
    value = _coerce(fact.value, expected)
    update: dict = {"facts": [*state.facts, fact]}
    seg = fact.key.split(":")
    match seg:
        case ["road", edge_id, "cut"]:
            roads = dict(state.roads)
            r = road_of(roads, edge_id)
            if r is not None:
                # Regla 4: solo lo observado puede REABRIR una arista. Un hecho
                # asumido o inferido sostiene la dirección segura (cortada) y no más.
                if fact.kind != "observed" and r.cut and not bool(value):
                    return state.model_copy(update={"facts": [*state.facts, fact]})
                roads[r.id] = r.model_copy(update={"cut": bool(value)})
                update["roads"] = roads
        case ["road", edge_id, "cause"]:
            roads = dict(state.roads)
            r = road_of(roads, edge_id)
            if r is not None:
                roads[r.id] = r.model_copy(update={"cut_cause": str(value)})
                update["roads"] = roads
        case ["poi", poi_id, attr]:
            update["civilians"] = _apply_poi_fact(state, poi_id, attr, value)
        case ["cell", cell_id, "state"]:
            cells = dict(state.cells)
            c = cells.get(cell_id)
            if c is not None:
                cells[cell_id] = c.model_copy(
                    update={"state": value, "t_changed": fact.t_sim}
                )
                update["cells"] = cells
        case ["unit", unit_id, "available"]:
            units = dict(state.units)
            u = units.get(unit_id)
            if u is not None:
                status = "idle" if bool(value) else "unavailable"
                units[unit_id] = u.model_copy(update={"status": status})
                update["units"] = units
        case ["wind", "bearing_deg"]:
            update["wind"] = state.wind.model_copy(update={"bearing_deg": float(value)})
        case ["wind", "speed"]:
            update["wind"] = state.wind.model_copy(update={"speed": float(value)})
    return state.model_copy(update=update)


def _apply_poi_fact(
    state: WorldState, poi_id: str, attr: str, value: object
) -> dict[str, CivilianGroup]:
    civ = dict(state.civilians)
    group = next((g for g in civ.values() if g.poi_id == poi_id), None)
    if group is None:
        if attr in ("immobile", "injuries", "headcount"):
            count = int(value) if attr == "headcount" else max(int(value), 1)
            group = CivilianGroup(id=f"civ_{poi_id}", poi_id=poi_id, count=count)
        else:
            return civ
    match attr:
        case "immobile":
            group = group.model_copy(
                update={"immobile": int(value), "count": max(group.count, int(value))}
            )
        case "headcount":
            group = group.model_copy(update={"count": int(value)})
        case "injuries":
            group = group.model_copy(
                update={"state": "trapped" if int(value) > 0 else group.state}
            )
        case "evacuated":
            group = group.model_copy(
                update={"state": "safe" if bool(value) else group.state}
            )
        case "confirmed":
            group = group.model_copy(
                update={"state": "warned" if bool(value) else group.state}
            )
    civ[group.id] = group
    return civ


def _latest(facts: list[Fact], key: str) -> Fact | None:
    for f in reversed(facts):
        if f.key == key:
            return f
    return None


def _coerce(value: object, expected: type) -> object:
    try:
        if expected is bool and isinstance(value, str):
            return value.strip().lower() in ("true", "1", "sí", "si", "yes")
        return expected(value)  # type: ignore[call-arg]
    except (TypeError, ValueError):
        return value


def _cell_index(cell_id: str, prev: Cell | None) -> tuple[int, int]:
    if prev is not None:
        return prev.cx, prev.cz
    parts = cell_id.split("_")
    try:
        return int(parts[-2]), int(parts[-1])
    except (IndexError, ValueError):
        return 0, 0


__all__ = ["apply", "apply_fact", "initial_state"]
