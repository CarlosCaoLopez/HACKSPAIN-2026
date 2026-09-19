"""Ciclo de vida de las tareas, llamadas salientes y señales en vivo del core.

Bus en memoria (`contracts.bus` con writer a una lista, como `test_voice_fact.py`),
planner stubbeado (ninguna llamada de red) y un escenario mínimo con la Y de
`wildfire_ridge`: base → cruce → desvío norte o sur → Pueblo A.
"""

import logging

import pytest

from contracts import bus
from contracts.calls import Fact
from contracts.events import (
    ActionRequested,
    CallRequest,
    Event,
    EventType,
    SignalRequested,
    TaskChanged,
)
from contracts.plan import UNKNOWN_CONSTRAINT, Policy
from contracts.scenario import HazardSpec, Scenario, Waypoint
from contracts.settings import settings
from contracts.world import POI, Cell, CivilianGroup, RoadEdge, Unit, Wind, WorldState
from core import belief, calls, loop, planner, solver, tasks

RUN = "run_test"
JUDGE = "+34600000000"

# --- escenario ---------------------------------------------------------------


def _scenario(contact_phone: str | None = None) -> Scenario:
    return Scenario(
        id="sc_test",
        name="test",
        origin=(0.0, 0.0),
        hazard=HazardSpec(
            kind="wildfire",
            origin_cell="cell_5_0",
            cell_size=4,
            wind=Wind(bearing_deg=270, speed=1.0),
        ),
        waypoints=[
            Waypoint(id="wp_base", x=0, z=0),
            Waypoint(id="wp_cruce", x=30, z=0),
            Waypoint(id="wp_nor_01", x=60, z=-40),
            Waypoint(id="wp_sur_01", x=60, z=40),
            Waypoint(id="wp_pueblo_a", x=100, z=0),
        ],
        roads=[
            RoadEdge(id="road:wp_base-wp_cruce", a="wp_base", b="wp_cruce", length_m=30),
            RoadEdge(
                id="road:wp_cruce-wp_nor_01", a="wp_cruce", b="wp_nor_01", length_m=50
            ),
            RoadEdge(
                id="road:wp_nor_01-wp_pueblo_a",
                a="wp_nor_01",
                b="wp_pueblo_a",
                length_m=56,
            ),
            RoadEdge(
                id="road:wp_cruce-wp_sur_01", a="wp_cruce", b="wp_sur_01", length_m=50
            ),
            RoadEdge(
                id="road:wp_sur_01-wp_pueblo_a",
                a="wp_sur_01",
                b="wp_pueblo_a",
                length_m=56,
            ),
        ],
        pois=[
            POI(
                id="poi_pueblo_a",
                name="Pueblo A",
                kind="village",
                x=100,
                z=0,
                waypoint_id="wp_pueblo_a",
                contact_phone=contact_phone,
            ),
        ],
        units=[
            Unit(
                id="unit_truck2", kind="fire_truck", x=0, z=0, capabilities=["extinguish"]
            ),
            Unit(
                id="unit_ambulance",
                kind="ambulance",
                x=0,
                z=0,
                capabilities=["transport"],
            ),
        ],
        civilians=[CivilianGroup(id="civ_a", poi_id="poi_pueblo_a", count=10)],
    )


@pytest.fixture
def journal(monkeypatch) -> list[Event]:
    events: list[Event] = []
    bus.reset()
    bus.configure(run_id=RUN, writer=events.append)
    monkeypatch.setattr(settings, "judge_phone", JUDGE)
    yield events
    bus.reset()


@pytest.fixture
def fixed_planner(monkeypatch) -> dict[str, int]:
    """Planner sin red: una `Policy` fija y un contador de llamadas al modelo."""
    calls_n = {"n": 0}

    async def _plan(state, reason, rules=""):
        calls_n["n"] += 1
        return Policy(rationale="test", weights={"life_safety": 0.8})

    async def _critique(state, violations):
        calls_n["n"] += 1
        return Policy(rationale="test")

    monkeypatch.setattr(planner, "plan", _plan)
    monkeypatch.setattr(planner, "replan_with_critique", _critique)
    return calls_n


def _ev(
    type_: EventType, payload: dict, source: str = "sim", t_sim: float = 0.0
) -> Event:
    return bus.make_event(type_, payload, source=source, t_sim=t_sim)


async def _ignite(core: loop.Core) -> Event:
    ev = _ev(EventType.WORLD_FIRE_DETECTED, {"cell_id": "cell_5_0", "hazard": "wildfire"})
    await bus.publish(ev)  # sella `seq`, como haría el sim de verdad
    await core.on_event(ev)
    return ev


def _of(journal: list[Event], type_: EventType) -> list[Event]:
    return [e for e in journal if e.type == type_]


def _tasks_in(journal: list[Event]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in _of(journal, EventType.TASK_CHANGED):
        t = TaskChanged.model_validate(e.payload).task
        out[t.id] = t.model_dump()
    return out


# --- tareas ------------------------------------------------------------------


async def test_ignition_creates_extinguish_and_evacuate(journal, fixed_planner) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)

    st = core.state()
    ext = st.tasks["task_ext_cell_5_0"]
    assert ext.kind == "extinguish" and ext.target_cell == "cell_5_0"
    assert ext.required_capability == "extinguish" and not ext.done
    evac = st.tasks["task_evac_poi_pueblo_a"]
    assert evac.kind == "evacuate" and evac.target_poi == "poi_pueblo_a"
    assert evac.required_capability == "transport"
    # Pueblo A está a sotavento (viento del oeste, pueblo al este): crítica.
    assert evac.severity == "critical"

    # Las tareas están en el journal, antes del plan, y el plan inicial salió.
    seen = _tasks_in(journal)
    assert set(seen) == {"task_ext_cell_5_0", "task_evac_poi_pueblo_a"}
    types = [e.type for e in journal]
    assert types.index(EventType.TASK_CHANGED) < types.index(EventType.PLAN_EMITTED)
    assert fixed_planner["n"] == 1
    assigned = {a.task_id: a.unit_id for a in core.current_plan().assignments}
    assert assigned == {
        "task_ext_cell_5_0": "unit_truck2",
        "task_evac_poi_pueblo_a": "unit_ambulance",
    }
    gotos = [
        ActionRequested.model_validate(e.payload)
        for e in _of(journal, EventType.ACTION_REQUESTED)
    ]
    assert {g.args["unit_id"] for g in gotos} == {"unit_truck2", "unit_ambulance"}


async def test_cell_changed_dedupes_and_burnt_closes(journal, fixed_planner) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    n_before = len(_of(journal, EventType.TASK_CHANGED))

    # La misma celda vuelve a llegar como `burning`: no nace otra tarea.
    ev = _ev(
        EventType.WORLD_CELL_CHANGED,
        {"cell_id": "cell_5_0", "state": "burning", "hazard": "wildfire"},
        t_sim=10.0,
    )
    await bus.publish(ev)
    await core.on_event(ev)
    assert len(_of(journal, EventType.TASK_CHANGED)) == n_before

    # Una celda `at_risk` nueva: tarea media. Se quema: se cierra.
    ev = _ev(
        EventType.WORLD_CELL_CHANGED,
        {"cell_id": "cell_6_0", "state": "at_risk", "hazard": "wildfire"},
        t_sim=20.0,
    )
    await bus.publish(ev)
    await core.on_event(ev)
    assert core.state().tasks["task_ext_cell_6_0"].severity == "medium"
    ev = _ev(
        EventType.WORLD_CELL_CHANGED,
        {"cell_id": "cell_6_0", "state": "burnt", "hazard": "wildfire"},
        t_sim=30.0,
    )
    await bus.publish(ev)
    await core.on_event(ev)
    assert core.state().tasks["task_ext_cell_6_0"].done is True
    assert _tasks_in(journal)["task_ext_cell_6_0"]["done"] is True
    # Nada de esto llamó al modelo: solo el plan inicial.
    assert fixed_planner["n"] == 1


async def test_immobile_fact_creates_rescue(journal, fixed_planner) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    fact = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:immobile",
            "value": 2,
            "confidence": 0.9,
            "source": "call:sess_1",
            "severity": "medium",
            "kind": "inferred",
        },
        source="call:sess_1",
        t_sim=40.0,
    )
    await bus.publish(fact)
    await core.on_event(fact)
    rescue = core.state().tasks["task_rescue_poi_pueblo_a"]
    assert rescue.kind == "rescue" and rescue.severity == "critical"
    assert (
        rescue.required_capability == "transport" and rescue.target_poi == "poi_pueblo_a"
    )
    assert "task_rescue_poi_pueblo_a" in _tasks_in(journal)


async def test_assumed_default_immobile_does_not_create_rescue(
    journal, fixed_planner
) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    fact = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:immobile",
            "value": 2,
            "confidence": 0.3,
            "source": "call:sess_1",
            "severity": "low",
            "kind": "assumed_default",
        },
        source="call:sess_1",
    )
    await bus.publish(fact)
    await core.on_event(fact)
    assert "task_rescue_poi_pueblo_a" not in core.state().tasks


async def test_unit_arrival_marks_done(journal, fixed_planner) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    assert core.state().units["unit_ambulance"].task_id == "task_evac_poi_pueblo_a"

    ev = _ev(
        EventType.WORLD_UNIT_ARRIVED,
        {"unit_id": "unit_ambulance", "waypoint_id": "wp_pueblo_a"},
        t_sim=90.0,
    )
    await bus.publish(ev)
    await core.on_event(ev)
    assert core.state().tasks["task_evac_poi_pueblo_a"].done is True
    assert core.state().tasks["task_ext_cell_5_0"].done is False
    assert _tasks_in(journal)["task_evac_poi_pueblo_a"]["done"] is True
    # Se replanificó solo con el solver (sin modelo) y la ambulancia queda libre.
    assert fixed_planner["n"] == 1
    assert "task_evac_poi_pueblo_a" not in {
        a.task_id for a in core.current_plan().assignments
    }


# --- llamadas ----------------------------------------------------------------


async def test_evacuate_assignment_publishes_one_call(journal, fixed_planner) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)

    reqs = [
        CallRequest.model_validate(e.payload)
        for e in _of(journal, EventType.CALL_REQUESTED)
    ]
    assert len(reqs) == 1
    req = reqs[0]
    assert req.task_id == "task_evac_poi_pueblo_a" and req.poi_id == "poi_pueblo_a"
    assert req.to == JUDGE
    assert req.intent == "evacuation_order" and req.audience == "resident"
    assert req.urgency == "critical"
    assert req.facts["poi_name"] == "Pueblo A"
    assert req.facts["route_name"] in ("pista norte", "pista sur")
    assert req.facts["hazard_kind"] == "incendio forestal"
    assert int(req.facts["deadline_min"]) >= 6
    assert req.expect == ["confirmation", "headcount"]
    # `causes` apunta al evento que provocó el plan.
    fire = _of(journal, EventType.WORLD_FIRE_DETECTED)[0]
    assert _of(journal, EventType.CALL_REQUESTED)[0].causes == [fire.seq]

    # Otro replan (hecho crítico) no vuelve a llamar por la misma tarea.
    fact = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:headcount",
            "value": 12,
            "confidence": 1.0,
            "source": "human",
            "severity": "critical",
        },
        source="human",
    )
    await bus.publish(fact)
    await core.on_event(fact)
    assert fixed_planner["n"] == 2
    assert len(_of(journal, EventType.CALL_REQUESTED)) == 1


async def test_no_phone_no_call(journal, fixed_planner, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "judge_phone", "")
    core = loop.Core(bus, _scenario(contact_phone=None))
    with caplog.at_level(logging.WARNING, logger="core.loop"):
        await _ignite(core)
    assert not _of(journal, EventType.CALL_REQUESTED)
    assert any("JUDGE_PHONE" in r.message for r in caplog.records)


async def test_contact_phone_used_when_no_judge(
    journal, fixed_planner, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "judge_phone", "")
    core = loop.Core(bus, _scenario(contact_phone="+34000000001"))
    await _ignite(core)
    req = CallRequest.model_validate(_of(journal, EventType.CALL_REQUESTED)[0].payload)
    assert req.to == "+34000000001"


# --- señal a la llamada en curso ----------------------------------------------


async def test_call_fact_replan_signals_unit_dispatched(journal, fixed_planner) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)

    fact = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:immobile",
            "value": 3,
            "confidence": 0.9,
            "source": "call:sess_7",
            "severity": "critical",
            "kind": "observed",
        },
        source="call:sess_7",
        t_sim=50.0,
    )
    await bus.publish(fact)
    await core.on_event(fact)
    assert fixed_planner["n"] == 2  # hecho crítico: replan con modelo

    sigs = _of(journal, EventType.CALL_SIGNAL_REQUESTED)
    assert len(sigs) == 1
    sig = SignalRequested.model_validate(sigs[0].payload)
    assert sig.call_id == "sess_7" and sig.key == "unit_dispatched"
    assert sig.payload["unit"] == "ambulancia"
    assert sig.payload["route"] in ("pista norte", "pista sur")
    assert isinstance(sig.payload["eta_s"], int) and sig.payload["eta_s"] > 0
    assert sigs[0].causes == [fact.seq]
    # La señal sale después del plan que la justifica.
    types = [e.type for e in journal]
    assert types.index(EventType.CALL_SIGNAL_REQUESTED) > len(types) - 1 - types[
        ::-1
    ].index(EventType.PLAN_EMITTED)


async def test_sim_fact_does_not_signal(journal, fixed_planner) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    fact = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:immobile",
            "value": 3,
            "confidence": 1.0,
            "source": "human",
            "severity": "critical",
        },
        source="human",
    )
    await bus.publish(fact)
    await core.on_event(fact)
    assert not _of(journal, EventType.CALL_SIGNAL_REQUESTED)


# --- solver y planner: nada en silencio ---------------------------------------


def test_sync_is_pure_and_idempotent() -> None:
    """Mismo estado, mismas tareas; y con las tareas ya plegadas, nada que publicar."""
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    st = belief.initial_state(RUN, sc)
    assert tasks.sync(st, graph) == []  # sin fuego, sin tareas
    ignited = belief.apply(
        st,
        _ev(EventType.WORLD_FIRE_DETECTED, {"cell_id": "cell_5_0", "hazard": "wildfire"}),
    )
    first = tasks.sync(ignited, graph)
    assert [t.id for t in first] == ["task_ext_cell_5_0", "task_evac_poi_pueblo_a"]
    assert tasks.sync(ignited, graph) == first
    folded = ignited.model_copy(update={"tasks": {t.id: t for t in first}})
    assert tasks.sync(folded, graph) == []


def test_solver_returns_unknown_constraint_violation() -> None:
    sc = _scenario()
    st = belief.initial_state(RUN, sc)
    policy = Policy(
        rationale="x", hard_constraints=["no_unit_into_burning_cell", "foo:bar"]
    )
    plan, viols = solver.solve_with_violations(
        st, policy, solver.RoadGraph.from_scenario(sc)
    )
    assert plan.policy is policy
    assert [v.verifier for v in viols] == [UNKNOWN_CONSTRAINT]
    assert viols[0].severity == "soft" and "foo:bar" in viols[0].message
    # La firma de contrato sigue devolviendo solo el plan.
    assert solver.solve(st, policy).policy is policy


async def test_loop_publishes_unknown_constraint(journal, monkeypatch) -> None:
    async def _plan(state, reason, rules=""):
        return Policy(rationale="x", hard_constraints=["foo:bar"])

    async def _critique(state, violations):
        assert any(v.verifier == UNKNOWN_CONSTRAINT for v in violations)
        return Policy(rationale="corregida")

    monkeypatch.setattr(planner, "plan", _plan)
    monkeypatch.setattr(planner, "replan_with_critique", _critique)
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    viols = _of(journal, EventType.PLAN_VIOLATION)
    assert viols and viols[0].payload["verifier"] == UNKNOWN_CONSTRAINT
    assert core.current_plan().policy.rationale == "corregida"


async def test_planner_failure_is_logged(monkeypatch, caplog) -> None:
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("sin red")

    monkeypatch.setattr(planner, "AsyncOpenAI", Boom)
    with caplog.at_level(logging.ERROR, logger="core.planner"):
        policy = await planner.plan(_scenario_state(), "test")
    assert policy == planner.neutral_policy()
    assert any("neutra" in r.message for r in caplog.records)


def _scenario_state() -> WorldState:
    return belief.initial_state(RUN, _scenario())


# --- nombres humanos -----------------------------------------------------------


def test_route_and_unit_names() -> None:
    assert (
        calls.route_name(["wp_base", "wp_cruce", "wp_nor_01", "wp_pueblo_a"])
        == "pista norte"
    )
    assert (
        calls.route_name(["wp_base", "wp_cruce", "wp_sur_01", "wp_pueblo_a"])
        == "pista sur"
    )
    roads = {r.id: r for r in _scenario().roads}
    assert calls.route_name(["wp_base", "wp_cruce"], roads) == "wp_base-wp_cruce"
    assert calls.route_name(["wp_x", "wp_y"]) == "wp_x-wp_y"
    assert (
        calls.unit_name(Unit(id="unit_truck2", kind="fire_truck", x=0, z=0)) == "camión 2"
    )
    assert (
        calls.unit_name(Unit(id="unit_ambulance", kind="ambulance", x=0, z=0))
        == "ambulancia"
    )


# --- lo que se le cuenta al vecino -----------------------------------------------


def test_route_name_is_the_track_travelled_not_the_last_waypoint() -> None:
    """Al molino (`wp_sur_02`) se llega por la pista norte y Pueblo A cuando la sur
    está cortada; y una unidad parada en la sur que sale por la norte va "por la
    norte"."""
    assert (
        calls.route_name(
            ["wp_cruce", "wp_nor_01", "wp_nor_02", "wp_pueblo_a", "wp_sur_02"]
        )
        == "pista norte"
    )
    assert (
        calls.route_name(
            ["wp_sur_01", "wp_cruce", "wp_nor_01", "wp_nor_02", "wp_pueblo_a"]
        )
        == "pista norte"
    )
    assert calls.route_name(["wp_cruce", "wp_sur_01", "wp_sur_02"]) == "pista sur"
    assert calls.route_name(["wp_sur_01"]) == "pista sur"  # ya está en la pista


async def test_three_facts_of_one_call_signal_once(journal, fixed_planner) -> None:
    """Un `report_fact` publica varios hechos seguidos (arista cortada, causa, inmóviles).
    La misma unidad por la misma pista se dice una vez, no una por hecho."""
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    keys = [
        ("road:wp_cruce-wp_sur_01:cut", True),
        ("road:wp_cruce-wp_sur_01:cause", "árbol caído"),
        ("poi:poi_pueblo_a:immobile", 3),
    ]
    for key, value in keys:
        fact = _ev(
            EventType.WORLD_FACT_ASSERTED,
            {
                "key": key,
                "value": value,
                "confidence": 0.9,
                "source": "call:sess_9",
                "severity": "critical",
                "kind": "observed",
            },
            source="call:sess_9",
            t_sim=50.0,
        )
        await bus.publish(fact)
        await core.on_event(fact)
    sigs = [
        SignalRequested.model_validate(e.payload)
        for e in _of(journal, EventType.CALL_SIGNAL_REQUESTED)
    ]
    assert len(sigs) == 1, [s.payload for s in sigs]
    assert sigs[0].payload["unit"] == "ambulancia"
    assert sigs[0].payload["route"] == "pista norte"  # la sur está cortada
    assert sigs[0].payload["eta_s"] > 0


def test_severity_weighs_in_the_cost() -> None:
    """A igual distancia, una tarea `critical` cuesta la mitad que una `high` y una
    cuarta parte que una `medium`: dejar sin cubrir lo grave deja de ser gratis."""
    sc = _scenario()
    sc = sc.model_copy(
        update={
            "pois": sc.pois
            + [
                POI(
                    id="poi_molino",
                    name="Molino",
                    kind="landmark",
                    x=60,
                    z=40,
                    waypoint_id="wp_sur_01",
                )
            ],
        }
    )
    graph = solver.RoadGraph.from_scenario(sc)
    st = belief.initial_state(RUN, sc)
    st = belief.apply(
        st,
        _ev(EventType.WORLD_FIRE_DETECTED, {"cell_id": "cell_5_0", "hazard": "wildfire"}),
    )
    st = belief.apply_fact(
        st,
        Fact(
            key="poi:poi_molino:immobile",
            value=3,
            confidence=0.9,
            source="call:sess_1",
            severity="critical",
            kind="observed",
            t_sim=50.0,
        ),
    )
    st = st.model_copy(update={"tasks": {t.id: t for t in tasks.sync(st, graph)}})
    rescue = st.tasks["task_rescue_poi_molino"]
    assert rescue.severity == "critical"
    policy = planner.neutral_policy()
    crit = solver._weighted_cost(100.0, st, rescue, policy)
    high = solver._weighted_cost(
        100.0, st, rescue.model_copy(update={"severity": "high"}), policy
    )
    med = solver._weighted_cost(
        100.0, st, rescue.model_copy(update={"severity": "medium"}), policy
    )
    assert crit == 25.0 and high == 50.0 and med == 100.0


async def test_signal_waits_until_the_pois_task_is_assigned(
    journal, fixed_planner
) -> None:
    """El vecino del molino no oye "ya va la ambulancia" mientras la ambulancia va a otro
    sitio: la señal sale con el plan que por fin asigna su rescate, y apunta al hecho."""
    sc = _scenario()
    sc = sc.model_copy(
        update={
            "pois": sc.pois
            + [
                POI(
                    id="poi_molino",
                    name="Molino",
                    kind="landmark",
                    x=60,
                    z=40,
                    waypoint_id="wp_sur_01",
                )
            ],
        }
    )
    core = loop.Core(bus, sc)
    await _ignite(core)
    # La ambulancia está a las puertas de Pueblo A: la evacuación le sale gratis.
    arrive = _ev(
        EventType.WORLD_UNIT_POSITION,
        {"unit_id": "unit_ambulance", "x": 96, "z": 0, "heading": 90.0},
        t_sim=40.0,
    )
    await bus.publish(arrive)
    await core.on_event(arrive)
    fact = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_molino:immobile",
            "value": 3,
            "confidence": 0.9,
            "source": "call:sess_5",
            "severity": "critical",
            "kind": "observed",
        },
        source="call:sess_5",
        t_sim=50.0,
    )
    await bus.publish(fact)
    await core.on_event(fact)
    assert not _of(journal, EventType.CALL_SIGNAL_REQUESTED), "aún no va nadie al molino"
    assert "task_rescue_poi_molino" in core._plan.unassigned_tasks

    # La ambulancia llega a Pueblo A: la evacuación cierra y el rescate se asigna.
    moved = _ev(
        EventType.WORLD_UNIT_POSITION,
        {"unit_id": "unit_ambulance", "x": 100, "z": 0, "heading": 90.0},
        t_sim=60.0,
    )
    await bus.publish(moved)
    await core.on_event(moved)
    arrived = _ev(
        EventType.WORLD_UNIT_ARRIVED,
        {"unit_id": "unit_ambulance", "waypoint_id": "wp_pueblo_a"},
        t_sim=60.0,
    )
    await bus.publish(arrived)
    await core.on_event(arrived)
    sigs = _of(journal, EventType.CALL_SIGNAL_REQUESTED)
    assert len(sigs) == 1
    sig = SignalRequested.model_validate(sigs[0].payload)
    assert sig.call_id == "sess_5"
    assert sig.payload["unit"] == "ambulancia" and sig.payload["route"] == "pista sur"
    assert sigs[0].causes == [fact.seq]


def test_coverage_only_violated_when_a_plan_withdraws_it() -> None:
    """Un pueblo con `min_coverage=1` al que nunca llegó nadie no es una violación del
    plan; sí lo es mandar a otro sitio a la unidad que lo cubre."""
    from core import verifiers

    sc = _scenario()
    sc = sc.model_copy(
        update={
            "pois": [
                sc.pois[0].model_copy(update={"min_coverage": 1}),
                POI(
                    id="poi_molino",
                    name="Molino",
                    kind="landmark",
                    x=60,
                    z=40,
                    waypoint_id="wp_sur_01",
                ),
            ],
            "units": sc.units
            + [
                Unit(
                    id="unit_truck9",
                    kind="fire_truck",
                    x=100,
                    z=0,
                    capabilities=["extinguish"],
                )
            ],
        }
    )
    graph = solver.RoadGraph.from_scenario(sc)
    st = belief.initial_state(RUN, sc)
    st = belief.apply(
        st,
        _ev(EventType.WORLD_FIRE_DETECTED, {"cell_id": "cell_5_0", "hazard": "wildfire"}),
    )
    st = belief.apply_fact(
        st,
        Fact(
            key="poi:poi_molino:immobile",
            value=2,
            confidence=0.9,
            source="call:sess_2",
            severity="critical",
            kind="observed",
            t_sim=50.0,
        ),
    )
    st = st.model_copy(update={"tasks": {t.id: t for t in tasks.sync(st, graph)}})
    # Nadie en Pueblo A salvo el camión 9: un plan que lo deja allí no viola nada.
    plan = solver.solve(st, planner.neutral_policy(), graph)
    stays = plan.model_copy(
        update={
            "assignments": [a for a in plan.assignments if a.unit_id != "unit_truck9"]
        }
    )
    assert verifiers.coverage_maintained(st, stays, graph) is None
    # Sin ninguna unidad en el pueblo tampoco: no hay cobertura que retirar.
    gone = st.model_copy(
        update={"units": {k: v for k, v in st.units.items() if k != "unit_truck9"}}
    )
    assert verifiers.coverage_maintained(gone, stays, graph) is None
    # Retirar al camión 9 hacia otro POI (el molino) sí.
    withdraws = stays.model_copy(
        update={
            "assignments": [a for a in stays.assignments if a.unit_id != "unit_ambulance"]
            + [
                plan.assignments[0].model_copy(
                    update={
                        "unit_id": "unit_truck9",
                        "task_id": "task_rescue_poi_molino",
                        "route": ["wp_pueblo_a", "wp_sur_01"],
                    }
                )
            ]
        }
    )
    v = verifiers.coverage_maintained(st, withdraws, graph)
    assert v is not None and v.verifier == "coverage_maintained"


def test_a_unit_reached_by_the_fire_can_still_be_assigned_out() -> None:
    """`no_unit_into_burning_cell` no cuenta el waypoint de salida (la unidad ya está
    ahí) ni el destino de una tarea `extinguish` (arde por definición). Antes, con el
    frente encima del cruce, todo par era infactible y el plan salía vacío."""
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    st = belief.initial_state(RUN, sc)
    st = belief.apply(
        st,
        _ev(EventType.WORLD_FIRE_DETECTED, {"cell_id": "cell_5_0", "hazard": "wildfire"}),
    )
    # El frente llega al cruce, donde está la ambulancia.
    cx, cz = graph.coords["wp_cruce"]
    cruce_cell = graph.cell_of(cx, cz)
    burning = Cell(
        id=cruce_cell,
        cx=int((cx - graph.origin[0]) // graph.cell_size),
        cz=int((cz - graph.origin[1]) // graph.cell_size),
        state="burning",
    )
    st = st.model_copy(update={"cells": {**st.cells, cruce_cell: burning}})
    amb = st.units["unit_ambulance"].model_copy(update={"x": 30, "z": 0})
    st = st.model_copy(update={"units": {**st.units, "unit_ambulance": amb}})
    st = st.model_copy(update={"tasks": {t.id: t for t in tasks.sync(st, graph)}})
    policy = Policy(rationale="x", hard_constraints=["no_unit_into_burning_cell"])
    plan = solver.solve(st, policy, graph)
    by_unit = {a.unit_id: a.task_id for a in plan.assignments}
    assert by_unit.get("unit_ambulance") == "task_evac_poi_pueblo_a", by_unit
    assert "task_evac_poi_pueblo_a" not in plan.unassigned_tasks
