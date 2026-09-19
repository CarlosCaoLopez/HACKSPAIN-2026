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
from core import belief, loop, planner, solver, tasks

RUN = "run_fronts"

EAST = ("cell_34_0", "cell_35_0", "cell_36_0")  # (138..146, 2): a tiro de wp_este
SOUTH = ("cell_14_50", "cell_15_50", "cell_16_50")  # (58..66, 202): a tiro de wp_sur
# Tres celdas cada uno: una chispa de una o dos celdas lejos de la gente no merece
# tarea propia (`MIN_FRONT_CELLS`). El id lleva la celda objetivo con que nació el
# frente: la atacable más cercana al POI amenazado (con viento del oeste, Pueblo A,
# al este: la de mayor x).
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
    west = ("cell_33_0", "cell_34_0", "cell_35_0")
    east = ("cell_38_0", "cell_39_0", "cell_40_0")  # a 3 celdas: otro frente
    st = _burning(belief.initial_state(RUN, sc), *west, *east)
    st, _ = _folded(st, graph)
    assert set(_ext(st)) == {"task_front_35_0", "task_front_40_0"}
    # El del este está más cerca de Pueblo A: es el crítico; el otro, el segundo.
    assert st.tasks["task_front_40_0"].severity == "critical"
    assert st.tasks["task_front_35_0"].severity == "high"

    st = _burning(st, "cell_36_0", "cell_37_0")  # el puente
    st, changed = _folded(st, graph)
    assert set(_ext(st)) == {"task_front_40_0"}  # se queda la más grave
    closed = [t for t in changed if t.done]
    assert [t.id for t in closed] == ["task_front_35_0"]
    assert tasks.sync(st, graph) == []


def test_a_gap_of_two_cells_is_still_one_front() -> None:
    """Dilatación: un hueco de una o dos celdas (una celda que se apagó, o el fuego
    que saltó) no parte el frente en dos tareas. A tres celdas sí son dos."""
    sc = _scenario()
    st = _burning(belief.initial_state(RUN, sc), "cell_33_0", "cell_35_0", "cell_37_0")
    assert len(tasks.fronts(st.cells)) == 1
    st = _burning(belief.initial_state(RUN, sc), "cell_33_0", "cell_36_0")
    assert len(tasks.fronts(st.cells)) == 2


# --- (c) todo quemado: la tarea se cierra ----------------------------------------


def test_front_burnt_out_closes_task() -> None:
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    st = _burning(belief.initial_state(RUN, sc), *EAST)
    st, _ = _folded(st, graph)
    assert set(_ext(st)) == {EAST_ID}

    st = _burning(st, "cell_36_0", state="burnt")  # quedan la 34 y la 35: sigue viva
    st, changed = _folded(st, graph)
    assert [(t.id, t.done, t.target_cell) for t in changed] == [
        (EAST_ID, False, "cell_35_0")
    ]
    assert set(_ext(st)) == {EAST_ID}

    st = _burning(st, "cell_34_0", "cell_35_0", state="burnt")
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
    assert {a.unit_id: a.task_id for a in plan.assignments} == {"unit_truck1": SOUTH_ID}
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
    far_cells = ("cell_20_20", "cell_20_21", "cell_20_22")  # (82, 82..90)
    st = _burning(belief.initial_state(RUN, sc), *EAST, *far_cells)
    st = st.model_copy(update={"wind": Wind(bearing_deg=0, speed=0.0)})
    st, _ = _folded(st, graph)
    policy = planner.neutral_policy()
    matrix, _units, tsks, _routes = solver.cost_matrix(st, policy, graph)
    cost = dict(zip((t.id for t in tsks), matrix[0]))
    near = st.tasks[EAST_ID]
    far = st.tasks["task_front_20_20"]
    nx, nz = graph.cell_center(st.cells[near.target_cell])
    to_road = math.hypot(nx - 140, nz - 0)  # centro de la celda objetivo → wp_este
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


# --- (g) los frentes que importan: tope, chispas y gravedad por rango --------------


def _blocks(*cxs: int, cz: int = 0) -> list[str]:
    """Un frente de tres celdas en fila (cx, cx+1, cx+2) por cada cx."""
    return [f"cell_{cx + i}_{cz}" for cx in cxs for i in range(3)]


def test_at_most_four_fronts_have_a_task() -> None:
    """Seis frentes separados: solo los cuatro más amenazantes (los más cercanos a
    Pueblo A con viento del oeste) tienen tarea; los dos del oeste, no."""
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    st = _burning(belief.initial_state(RUN, sc), *_blocks(4, 10, 16, 22, 28, 34))
    assert len(tasks.fronts(st.cells)) == 6
    st, changed = _folded(st, graph)
    assert len(changed) == tasks.MAX_FRONTS == 4
    targets = {t.target_cell for t in changed}
    westmost = set(_blocks(4, 10))
    assert not targets & westmost
    by_rank = sorted(changed, key=lambda t: -tasks._rank(t.severity))
    assert [t.severity for t in by_rank[:2]] == ["critical", "high"]
    assert {t.severity for t in by_rank[2:]} <= {"medium", "low"}


def test_small_sparks_far_from_people_get_no_task() -> None:
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    st = _burning(belief.initial_state(RUN, sc), *EAST, "cell_20_20")  # chispa a 84 m
    st, changed = _folded(st, graph)
    assert [t.id for t in changed] == [EAST_ID]
    # Una chispa igual de pequeña pero a las puertas de Pueblo B sí merece tarea.
    st = _burning(st, "cell_15_95")  # (62, 382): a 18 m de Pueblo B
    st, changed = _folded(st, graph)
    assert {t.id: t.severity for t in changed} == {
        "task_front_15_95": "critical",  # la que antes llega a la gente
        EAST_ID: "high",  # y el frente del este baja al segundo puesto
    }
    # Y la ignición (una sola celda, nada más ardiendo) tiene tarea igual.
    st = _burning(belief.initial_state(RUN, sc), "cell_20_20")
    _, changed = _folded(st, graph)
    assert [t.id for t in changed] == ["task_front_20_20"]


def test_severity_is_by_threat_rank_and_the_wind_reorders_it() -> None:
    """Tres frentes: el que antes llega a la gente es `critical`, el segundo `high`,
    el tercero `medium`. Gira el viento y el orden cambia con él."""
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    mid = ("cell_25_0", "cell_26_0", "cell_27_0")  # (102..110, 2): junto al cruce
    st = _burning(belief.initial_state(RUN, sc), *EAST, *mid, *SOUTH)
    st, _ = _folded(st, graph)
    sev = {t.target_cell: t.severity for t in _ext(st).values()}
    assert sev == {"cell_36_0": "critical", "cell_27_0": "high", "cell_16_50": "medium"}

    # Viento del norte: solo el frente del sur va hacia alguien (Pueblo B); los otros
    # dos no llegan a nadie en una hora.
    st = st.model_copy(update={"wind": Wind(bearing_deg=0, speed=1.0)})
    st, changed = _folded(st, graph)
    assert len(changed) == 3
    sev = {t.id: t.severity for t in _ext(st).values()}
    assert sev == {SOUTH_ID: "critical", EAST_ID: "medium", "task_front_27_0": "medium"}


# --- (h) dos camiones, un frente ---------------------------------------------------


def test_two_trucks_share_the_only_front_from_two_waypoints() -> None:
    """Con un solo frente el segundo camión no se queda parado: la tarea se duplica
    en la matriz y cada camión la ataca desde un waypoint distinto si el frente lo
    permite (aquí toca el cruce y el desvío este)."""
    sc = _scenario()
    sc = sc.model_copy(
        update={
            "units": sc.units
            + [
                Unit(
                    id="unit_truck2",
                    kind="fire_truck",
                    x=0,
                    z=10,
                    capabilities=["extinguish"],
                )
            ]
        }
    )
    graph = solver.RoadGraph.from_scenario(sc)
    ring = [f"cell_{cx}_0" for cx in range(25, 37)]  # (102..146, 2)
    st = _burning(belief.initial_state(RUN, sc), *ring)
    st, _ = _folded(st, graph)
    (task,) = _ext(st).values()
    plan = solver.solve(st, planner.neutral_policy(), graph)
    assert [a.task_id for a in plan.assignments] == [task.id, task.id]
    assert {a.route[-1] for a in plan.assignments} == {"wp_este", "wp_cruce"}
    assert task.id not in plan.unassigned_tasks
    assert len(plan.unassigned_tasks) == len(set(plan.unassigned_tasks))


# --- (i) permanencia: el coste de cambio del solver ---------------------------------


def test_a_held_unit_pays_to_leave_its_waypoint() -> None:
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    st = _burning(belief.initial_state(RUN, sc), *EAST, *SOUTH)
    st, _ = _folded(st, graph)
    free = solver.solve(st, planner.neutral_policy(), graph)
    assert free.assignments[0].task_id == EAST_ID  # el crítico
    held = solver.solve(
        st, planner.neutral_policy(), graph, holds={"unit_truck1": "wp_sur"}
    )
    assert held.assignments[0].task_id == SOUTH_ID
    assert held.assignments[0].cost < solver.HOLD_PENALTY_M


async def test_dwell_holds_a_moving_unit_until_its_task_closes_or_a_critical_orphan(
    journal, fixed_planner
) -> None:
    core = loop.Core(bus, _scenario())
    ev = _ev(
        EventType.WORLD_FIRE_DETECTED, {"cell_id": "cell_35_0", "hazard": "wildfire"}
    )
    await bus.publish(ev)
    await core.on_event(ev)
    assert core._goto_t == {"unit_truck1": 0.0}

    def _at(t: float, status: str = "moving") -> None:
        core._state = core._state.model_copy(
            update={
                "t_sim": t,
                "units": {
                    "unit_truck1": core._state.units["unit_truck1"].model_copy(
                        update={"status": status}
                    )
                },
            }
        )

    _at(10.0)
    assert core._holds() == {"unit_truck1": "wp_este"}
    _at(10.0, status="idle")  # parada a medio camino: no está en su waypoint
    assert core._holds() == {}
    _at(loop.DWELL_S)  # se acabó la permanencia
    assert core._holds() == {}
    # Una tarea crítica sin unidad la libera aunque esté dentro de la permanencia.
    _at(10.0)
    orphan = Task(
        id="task_front_x",
        kind="extinguish",
        target_cell="cell_15_50",
        required_capability="extinguish",
        severity="critical",
        created_t=10.0,
    )
    core._state = core._state.model_copy(
        update={"tasks": {**core._state.tasks, orphan.id: orphan}}
    )
    assert core._holds() == {}


# --- (j) el cortafuegos no baila -----------------------------------------------------


def test_waiting_target_only_moves_if_the_new_contact_is_clearly_sooner() -> None:
    """Frente que nadie tiene a tiro: la celda de contacto (y el waypoint donde se
    espera) se conserva mientras la nueva no llegue a la carretera en menos del 80 %
    del tiempo de la vigente. Dos waypoints, `wp_a` y `wp_b`, y un frente que crece
    de uno hacia el otro."""
    sc = Scenario(
        id="sc_wait",
        name="espera",
        hazard=HazardSpec(
            kind="wildfire", origin_cell="cell_10_10", wind=Wind(bearing_deg=0, speed=0)
        ),
        waypoints=[Waypoint(id="wp_a", x=0, z=0), Waypoint(id="wp_b", x=100, z=0)],
        roads=[RoadEdge(id="road:wp_a-wp_b", a="wp_a", b="wp_b", length_m=100)],
        units=[
            Unit(
                id="unit_truck1", kind="fire_truck", x=0, z=0, capabilities=["extinguish"]
            )
        ],
    )
    graph = solver.RoadGraph.from_scenario(sc)
    st = _burning(belief.initial_state(RUN, sc), "cell_10_10")  # (42, 42): a 59 m de wp_a
    st, _ = _folded(st, graph)
    (task,) = _ext(st).values()
    assert task.target_cell == "cell_10_10"
    assert solver.attack_waypoint(42, 42, st.wind, graph) == "wp_a"

    # Crece hacia wp_b: (62, 38) está a 54 m de wp_b, un 90 % de los 59 m: se queda.
    st = _burning(st, "cell_12_10", "cell_14_10", "cell_15_9")
    st, changed = _folded(st, graph)
    assert changed == [] and st.tasks[task.id].target_cell == "cell_10_10"

    # (70, 30) está a 42 m de wp_b, un 71 %: ahora el camión espera en wp_b.
    st = _burning(st, "cell_17_7")
    st, changed = _folded(st, graph)
    assert [t.target_cell for t in changed] == ["cell_17_7"]
    assert solver.attack_waypoint(70, 30, st.wind, graph) == "wp_b"
