# ruff: noqa: F811 — los fixtures importados se vuelven a nombrar como parámetros de test
"""Tareas de extinción por FRENTE: una por componente conexa de celdas en llamas,
con id estable, gravedad por amenaza a la gente y celda objetivo atacable desde la
carretera. Es lo que hace que el cambio de viento cambie la prioridad y los camiones
giren, en vez de quedarse toda la demo en las dos primeras celdas.

Geometría del test (metros del mundo, celdas de 4 m): la base al oeste, el cruce, un
desvío al este (`wp_este`) hacia Pueblo A (al noreste) y otro al sur (`wp_sur`) hacia
Pueblo B (al sur). Con viento del oeste (270) el frente del este va hacia Pueblo A;
con viento del norte (0) el frente del sur va hacia Pueblo B.
"""

import math

from test_core_tasks import (  # noqa: F401 - fixtures compartidos (rootdir tests/ en sys.path)
    _ev,
    _of,
    fixed_planner,
    journal,
)

from contracts import bus
from contracts.events import ActionRequested, EventType
from contracts.scenario import HazardSpec, Scenario, Waypoint
from contracts.world import POI, Cell, CivilianGroup, RoadEdge, Unit, Wind, WorldState
from core import belief, loop, planner, solver, tasks

RUN = "run_fronts"

EAST = ("cell_35_0", "cell_36_0")  # (142, 2), (146, 2): a tiro de wp_este
SOUTH = ("cell_15_50", "cell_16_50")  # (62, 202), (66, 202): a tiro de wp_sur
# El id lleva la celda objetivo con que nació el frente: la atacable más cercana al
# POI amenazado (con viento del oeste, Pueblo A, al este: la de mayor x).
EAST_ID = "task_front_36_0"
SOUTH_ID = "task_front_16_50"


def _scenario() -> Scenario:
    return Scenario(
        id="sc_fronts",
        name="frentes",
        origin=(0.0, 0.0),
        hazard=HazardSpec(
            kind="wildfire",
            origin_cell="cell_35_0",
            cell_size=4,
            base_spread=0.12,
            wind=Wind(bearing_deg=270, speed=1.0),
            suppress_reach_m=24.0,
        ),
        waypoints=[
            Waypoint(id="wp_base", x=0, z=0),
            Waypoint(id="wp_cruce", x=100, z=0),
            Waypoint(id="wp_este", x=140, z=0),
            Waypoint(id="wp_pueblo_a", x=300, z=-100),
            Waypoint(id="wp_sur", x=62, z=210),
            Waypoint(id="wp_pueblo_b", x=62, z=400),
        ],
        roads=[
            RoadEdge(id="road:wp_base-wp_cruce", a="wp_base", b="wp_cruce", length_m=100),
            RoadEdge(id="road:wp_cruce-wp_este", a="wp_cruce", b="wp_este", length_m=40),
            RoadEdge(
                id="road:wp_este-wp_pueblo_a", a="wp_este", b="wp_pueblo_a", length_m=190
            ),
            RoadEdge(id="road:wp_cruce-wp_sur", a="wp_cruce", b="wp_sur", length_m=213),
            RoadEdge(
                id="road:wp_sur-wp_pueblo_b", a="wp_sur", b="wp_pueblo_b", length_m=190
            ),
        ],
        pois=[
            POI(
                id="poi_pueblo_a",
                name="Pueblo A",
                kind="village",
                x=300,
                z=-100,
                waypoint_id="wp_pueblo_a",
            ),
            POI(
                id="poi_pueblo_b",
                name="Pueblo B",
                kind="village",
                x=62,
                z=400,
                waypoint_id="wp_pueblo_b",
            ),
        ],
        units=[
            Unit(
                id="unit_truck1", kind="fire_truck", x=0, z=0, capabilities=["extinguish"]
            )
        ],
        civilians=[
            CivilianGroup(id="civ_a", poi_id="poi_pueblo_a", count=20),
            CivilianGroup(id="civ_b", poi_id="poi_pueblo_b", count=10),
        ],
    )


def _cell(cell_id: str, state: str = "burning") -> Cell:
    cx, cz = (int(v) for v in cell_id.removeprefix("cell_").split("_"))
    return Cell(id=cell_id, cx=cx, cz=cz, state=state)


def _burning(st: WorldState, *ids: str, state: str = "burning") -> WorldState:
    return st.model_copy(
        update={"cells": {**st.cells, **{c: _cell(c, state) for c in ids}}}
    )


def _folded(st: WorldState, graph: solver.RoadGraph) -> tuple[WorldState, list]:
    """Sincroniza y pliega, como hace el loop; devuelve el estado y las tareas de
    extinción publicadas (las de evacuación no son de este test)."""
    changed = tasks.sync(st, graph)
    folded = st.model_copy(update={"tasks": {**st.tasks, **{t.id: t for t in changed}}})
    return folded, [t for t in changed if t.kind == "extinguish"]


def _ext(st: WorldState) -> dict[str, object]:
    return {t.id: t for t in st.tasks.values() if t.kind == "extinguish" and not t.done}


# --- (a) dos frentes, dos tareas, ids estables mientras crecen ------------------


def test_two_fronts_two_tasks_with_stable_ids() -> None:
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    st = _burning(belief.initial_state(RUN, sc), *EAST, *SOUTH)
    st, changed = _folded(st, graph)
    assert [t.id for t in changed] == [SOUTH_ID, EAST_ID]
    assert set(_ext(st)) == {SOUTH_ID, EAST_ID}
    assert st.tasks[EAST_ID].target_cell in EAST
    assert st.tasks[SOUTH_ID].target_cell in SOUTH
    # Nada que publicar si nada cambia.
    assert tasks.sync(st, graph) == []

    # Los dos frentes crecen una celda: siguen siendo los mismos dos, mismos ids, y
    # mientras se ataquen desde el mismo waypoint la celda objetivo no baila.
    st = _burning(st, "cell_37_0", "cell_17_50")
    st, changed = _folded(st, graph)
    assert changed == []
    assert set(_ext(st)) == {SOUTH_ID, EAST_ID}


# --- (b) dos frentes se juntan: una sigue, la otra se cierra ---------------------


def test_merging_fronts_keep_one_task_and_close_the_other() -> None:
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    st = _burning(belief.initial_state(RUN, sc), "cell_35_0", "cell_38_0")
    st, _ = _folded(st, graph)
    assert set(_ext(st)) == {"task_front_35_0", "task_front_38_0"}

    st = _burning(st, "cell_36_0", "cell_37_0")  # el puente
    st, changed = _folded(st, graph)
    assert set(_ext(st)) == {"task_front_35_0"}
    closed = [t for t in changed if t.done]
    assert [t.id for t in closed] == ["task_front_38_0"]
    assert tasks.sync(st, graph) == []


# --- (c) todo quemado: la tarea se cierra ----------------------------------------


def test_front_burnt_out_closes_task() -> None:
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    st = _burning(belief.initial_state(RUN, sc), *EAST)
    st, _ = _folded(st, graph)
    assert set(_ext(st)) == {EAST_ID}

    st = _burning(st, "cell_36_0", state="burnt")  # queda la 35: sigue viva
    st, changed = _folded(st, graph)
    assert [(t.id, t.done, t.target_cell) for t in changed] == [
        (EAST_ID, False, "cell_35_0")
    ]
    assert set(_ext(st)) == {EAST_ID}

    st = _burning(st, "cell_35_0", state="burnt")
    st, changed = _folded(st, graph)
    assert [t.id for t in changed if t.done] == [EAST_ID]
    assert _ext(st) == {}
    assert tasks.sync(st, graph) == []


# --- (d) gravedad por amenaza a la gente, y cambia con el viento -----------------


def test_severity_follows_the_wind() -> None:
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    st = _burning(belief.initial_state(RUN, sc), *EAST, *SOUTH)
    st, _ = _folded(st, graph)
    # Viento del oeste: el frente del este va a Pueblo A; el del sur no va a nadie.
    assert st.tasks[EAST_ID].severity == "critical"
    assert st.tasks[SOUTH_ID].severity == "medium"

    # Viento del norte: el del sur va a Pueblo B; el del este ya no amenaza.
    st = st.model_copy(update={"wind": Wind(bearing_deg=0, speed=1.0)})
    st, changed = _folded(st, graph)
    assert {t.id: t.severity for t in changed} == {
        SOUTH_ID: "critical",
        EAST_ID: "medium",
    }
    # Una tarea sin gente cerca y sin carretera a tiro es `low`.
    calm = belief.initial_state(RUN, sc).model_copy(
        update={"wind": Wind(bearing_deg=0, speed=0.0)}
    )
    calm = _burning(calm, "cell_20_20")  # (82, 82): a 84 m del cruce
    _, changed = _folded(calm, graph)
    assert [(t.id, t.severity) for t in changed] == [("task_front_20_20", "low")]


# --- (e) el solver va al crítico, y al girar el viento cambia asignación y ruta --


async def test_wind_shift_changes_assignment_and_route(journal, fixed_planner) -> None:
    core = loop.Core(bus, _scenario())
    ev = _ev(
        EventType.WORLD_FIRE_DETECTED, {"cell_id": "cell_35_0", "hazard": "wildfire"}
    )
    await bus.publish(ev)
    await core.on_event(ev)
    for cell in SOUTH:
        ev = _ev(
            EventType.WORLD_CELL_CHANGED,
            {"cell_id": cell, "state": "burning", "hazard": "wildfire"},
            t_sim=5.0,
        )
        await bus.publish(ev)
        await core.on_event(ev)
    plan = core.current_plan()
    assert {a.unit_id: a.task_id for a in plan.assignments} == {
        "unit_truck1": "task_front_35_0"
    }
    assert plan.assignments[0].route == ["wp_base", "wp_cruce", "wp_este"]

    # Gira el viento: el frente del sur pasa a crítico y el camión gira con él, sin
    # esperar al throttle de re-solve y sin necesitar al modelo.
    tick = _ev(
        EventType.WORLD_TICK,
        {"t_sim": 6.0, "wind": {"bearing_deg": 0, "speed": 1.0}},
        t_sim=6.0,
    )
    await bus.publish(tick)
    await core.on_event(tick)
    plan = core.current_plan()
    assert {a.unit_id: a.task_id for a in plan.assignments} == {
        "unit_truck1": "task_front_15_50"
    }
    assert plan.assignments[0].route == ["wp_base", "wp_cruce", "wp_sur"]
    gotos = [
        ActionRequested.model_validate(e.payload).args["route"]
        for e in _of(journal, EventType.ACTION_REQUESTED)
    ]
    assert gotos[-1] == ["wp_base", "wp_cruce", "wp_sur"]
    assert fixed_planner["n"] == 1


def test_wind_shift_changes_target_cell_and_thus_the_route() -> None:
    """Un solo frente grande: al cambiar el viento cambia el POI amenazado, con él la
    celda objetivo, y con ella el waypoint desde el que se ataca."""
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    # Un frente en L que toca el desvío este y el cruce.
    ring = [f"cell_{cx}_0" for cx in range(26, 37)]  # (106..146, 2)
    st = _burning(belief.initial_state(RUN, sc), *ring)
    st, _ = _folded(st, graph)
    (task,) = _ext(st).values()
    plan = solver.solve(st, planner.neutral_policy(), graph)
    assert plan.assignments[0].route[-1] == "wp_este"  # hacia Pueblo A

    # Viento del norte: el fuego va hacia Pueblo B, y la cabeza (la celda que antes
    # llega a él) pasa al extremo oeste del frente, junto al cruce.
    st = st.model_copy(update={"wind": Wind(bearing_deg=0, speed=1.0)})
    st, changed = _folded(st, graph)
    assert [t.id for t in changed] == [task.id]
    assert st.tasks[task.id].target_cell != task.target_cell
    plan = solver.solve(st, planner.neutral_policy(), graph)
    assert plan.assignments[0].route[-1] == "wp_cruce"


# --- (f) celda objetivo atacable cuando la hay; si no, intercepción ---------------


def test_target_cell_is_attackable_when_possible() -> None:
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    column = [f"cell_35_{cz}" for cz in range(9)]  # (142, 2..34): las últimas lejos
    st = _burning(belief.initial_state(RUN, sc), *column)
    st, _ = _folded(st, graph)
    (task,) = _ext(st).values()
    x, z = graph.cell_center(st.cells[task.target_cell])
    assert solver.attackable_from(x, z, graph) == "wp_este"
    assert task.target_cell == "cell_35_0"  # la atacable más cercana a Pueblo A

    # Sin ninguna a tiro: la celda es la que antes llega al POI amenazado, y el
    # waypoint de ataque es el de intercepción (donde el fuego va, no el más cercano).
    st = _burning(belief.initial_state(RUN, sc), "cell_20_20")  # (82, 82)
    st = st.model_copy(update={"wind": Wind(bearing_deg=0, speed=1.0)})
    st, _ = _folded(st, graph)
    (task,) = _ext(st).values()
    assert task.target_cell == "cell_20_20"
    assert solver.attackable_from(82, 82, graph) is None
    assert graph.nearest_waypoint(82, 82) == "wp_cruce"
    assert solver.attack_waypoint(82, 82, st.wind, graph) == "wp_sur"
    plan = solver.solve(st, planner.neutral_policy(), graph)
    assert plan.assignments[0].route == ["wp_base", "wp_cruce", "wp_sur"]


def test_offroad_distance_weighs_in_the_cost() -> None:
    """Un frente a 3 m de la carretera cuesta su ruta y poco más; uno a 84 m, la ruta
    más esos 84 m a pie."""
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    st = _burning(belief.initial_state(RUN, sc), "cell_35_0", "cell_20_20")
    st = st.model_copy(update={"wind": Wind(bearing_deg=0, speed=0.0)})
    st, _ = _folded(st, graph)
    policy = planner.neutral_policy()
    matrix, _units, tsks, _routes = solver.cost_matrix(st, policy, graph)
    cost = dict(zip((t.id for t in tsks), matrix[0]))
    near = st.tasks["task_front_35_0"]
    far = st.tasks["task_front_20_20"]
    to_road = math.hypot(142 - 140, 2 - 0)  # centro de cell_35_0 → wp_este
    assert cost[near.id] == (140 + to_road) * solver.SEVERITY_FACTOR[near.severity]
    assert far.severity == "low"
    to_road = math.hypot(82 - 100, 82 - 0)  # centro de cell_20_20 → wp_cruce
    assert cost[far.id] == (100 + to_road) * solver.SEVERITY_FACTOR["low"]


def test_goto_is_not_resent_for_a_suffix_of_the_ordered_route() -> None:
    """La unidad avanza por su ruta (o cambia de frente atacado desde el mismo
    waypoint): el tramo que le queda es un sufijo de lo ordenado y no se reenvía.
    Otra pista (arista cortada) u otro destino, sí."""
    ordered = ("wp_base", "wp_cruce", "wp_sur_01")
    assert loop._same_way(ordered, ordered)
    assert loop._same_way(ordered, ("wp_cruce", "wp_sur_01"))
    assert loop._same_way(ordered, ("wp_sur_01",))
    assert not loop._same_way(ordered, ("wp_sur_01", "wp_sur_02"))
    assert not loop._same_way(ordered, ("wp_base", "wp_cruce", "wp_nor_01"))
    assert not loop._same_way(("wp_sur_01",), ordered)
