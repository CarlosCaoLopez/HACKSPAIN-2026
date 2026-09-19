"""Task 5 · divergencia y bucle de replan. Corre sin bus, sin sim y sin red.

`belief` y el bus son stubs de otros dueños: aquí se fakean por sus firmas. Lo que
se prueba de verdad es la lógica pura de `divergence` y la orquestación de `Core`.
"""

from datetime import UTC, datetime

from contracts.events import (
    ActionRequested,
    Event,
    EventType,
    ReplanStarted,
)
from contracts.plan import (
    Assumption,
    PlanContext,
    Policy,
)
from contracts.scenario import HazardSpec, Scenario, Waypoint
from contracts.world import POI, RoadEdge, Task, Unit, Wind, WorldState
from core import belief, loop, planner
from core.divergence import divergence, evaluate_assumption, should_replan

# --- Escenario y estado mínimos --------------------------------------------


def _scenario() -> Scenario:
    return Scenario(
        id="sc_test",
        name="test",
        hazard=HazardSpec(
            kind="wildfire", origin_cell="cell_0_0", wind=Wind(bearing_deg=0, speed=1.0)
        ),
        waypoints=[Waypoint(id="wp_a", x=0, z=0), Waypoint(id="wp_b", x=10, z=0)],
        roads=[RoadEdge(id="e1", a="wp_a", b="wp_b", length_m=10.0)],
    )


def _state(cut: bool = False, bearing: float = 0.0) -> WorldState:
    return WorldState(
        run_id="run_test",
        seq=1,
        t_sim=0.0,
        wind=Wind(bearing_deg=bearing, speed=1.0),
        units={
            "unit_truck": Unit(
                id="unit_truck",
                kind="fire_truck",
                x=0,
                z=0,
                capabilities=["extinguish"],
            )
        },
        roads={"e1": RoadEdge(id="e1", a="wp_a", b="wp_b", length_m=10.0, cut=cut)},
        pois={
            "poi_x": POI(
                id="poi_x", name="Pueblo X", kind="village", x=10, z=0, waypoint_id="wp_b"
            )
        },
        tasks={
            "task_ext": Task(
                id="task_ext",
                kind="extinguish",
                target_poi="poi_x",
                required_capability="extinguish",
                severity="high",
                created_t=0.0,
            )
        },
    )


def _ctx() -> PlanContext:
    return PlanContext(
        assumptions=[
            Assumption(key="road:e1:open", expected=True, weight=1.0),
            Assumption(key="wind:bearing_deg", expected=0.0, weight=0.5),
        ],
        world_seq=1,
    )


def _tick() -> Event:
    return Event(
        run_id="run_test",
        seq=5,
        t_wall=datetime.now(UTC),
        t_sim=0.0,
        type=EventType.WORLD_TICK,
        source="sim",
        payload={"t_sim": 0.0, "wind": {"bearing_deg": 0.0, "speed": 1.0}},
    )


# --- divergence: puro -------------------------------------------------------


def test_evaluate_assumption_road_and_wind() -> None:
    st = _state()
    assert evaluate_assumption(st, "road:e1:open") is True
    assert evaluate_assumption(_state(cut=True), "road:e1:open") is False
    assert evaluate_assumption(st, "road:ausente:open") is None
    assert evaluate_assumption(st, "wind:bearing_deg") == 0.0


def test_divergence_road_break() -> None:
    value, broken = divergence(_state(cut=True), _ctx())
    assert "road:e1:open" in broken
    assert value == 1.0 / 1.5  # peso road / (road + wind)


def test_divergence_wind_tolerance() -> None:
    # Dentro de tolerancia: no roto.
    v_ok, broken_ok = divergence(_state(bearing=10.0), _ctx())
    assert broken_ok == []
    assert v_ok == 0.0
    # Fuera de tolerancia: roto.
    v_bad, broken_bad = divergence(_state(bearing=90.0), _ctx())
    assert "wind:bearing_deg" in broken_bad
    assert v_bad > 0.0


def test_divergence_empty_denominator() -> None:
    assert divergence(_state(), PlanContext(assumptions=[], world_seq=1)) == (0.0, [])
    # Suposición no evaluable → se salta, denominador 0.
    ctx = PlanContext(
        assumptions=[Assumption(key="road:ausente:open", expected=True)], world_seq=1
    )
    assert divergence(_state(), ctx) == (0.0, [])


def test_should_replan_triggers() -> None:
    assert should_replan(0.0, 1, [])[0] is True  # violación dura
    assert should_replan(0.0, 0, [_fact()])[0] is True  # hecho crítico
    assert should_replan(0.30, 0, [])[0] is True  # divergencia
    assert should_replan(0.10, 0, []) == (False, "")  # nada: sin LLM


def _fact():
    from contracts.calls import Fact

    return Fact(
        key="cell:cell_1_1:state",
        value="burning",
        confidence=1.0,
        source="call:x",
        severity="critical",
        t_sim=0.0,
    )


# --- loop: smoke con bus y belief fakeados ---------------------------------


class FakeBus:
    def __init__(self) -> None:
        self.published: list[Event] = []

    def current_run_id(self) -> str:
        return "run_test"

    async def publish(self, ev: Event) -> None:
        self.published.append(ev)


def _types(bus: FakeBus) -> list[EventType]:
    return [e.type for e in bus.published]


def _install_belief(monkeypatch, initial: WorldState, applied: WorldState) -> None:
    monkeypatch.setattr(belief, "initial_state", lambda run_id, sc: initial)
    monkeypatch.setattr(belief, "apply", lambda state, ev: applied)
    monkeypatch.setattr(belief, "apply_fact", lambda state, fact: applied)


async def test_initial_plan_and_actions(monkeypatch) -> None:
    calls = {"n": 0}

    async def _plan(state, reason, rules=""):
        calls["n"] += 1
        return Policy(rationale="test")

    monkeypatch.setattr(planner, "plan", _plan)
    _install_belief(monkeypatch, _state(), _state())

    bus = FakeBus()
    core = loop.Core(bus, _scenario())
    await core.on_event(_tick())

    kinds = _types(bus)
    assert EventType.PLAN_REPLAN_STARTED in kinds
    assert EventType.PLAN_EMITTED in kinds
    assert EventType.ACTION_REQUESTED in kinds
    assert calls["n"] == 1
    assert core.current_plan() is not None

    # La acción es un goto de la unidad asignada.
    act = next(
        ActionRequested.model_validate(e.payload)
        for e in bus.published
        if e.type == EventType.ACTION_REQUESTED
    )
    assert act.verb == "goto"
    assert act.args["unit_id"] == "unit_truck"


async def test_no_flag_no_llm(monkeypatch) -> None:
    calls = {"n": 0}

    async def _plan(state, reason, rules=""):
        calls["n"] += 1
        return Policy(rationale="test")

    monkeypatch.setattr(planner, "plan", _plan)
    _install_belief(monkeypatch, _state(), _state())

    bus = FakeBus()
    core = loop.Core(bus, _scenario())
    await core.on_event(_tick())  # plan inicial: 1 llamada
    await core.on_event(_tick())  # tick benigno: sin bandera, sin LLM

    assert calls["n"] == 1
    assert EventType.PLAN_DIVERGENCE in _types(bus)


async def test_road_cut_forces_replan(monkeypatch) -> None:
    calls = {"n": 0}

    async def _plan(state, reason, rules=""):
        calls["n"] += 1
        return Policy(rationale="test")

    monkeypatch.setattr(planner, "plan", _plan)
    # Primero un estado sano; luego uno con la arista cortada. Solo los ticks
    # avanzan el guion: los eventos que el core pliega de sí mismo (plan, tareas)
    # dejan el estado tal cual.
    monkeypatch.setattr(belief, "initial_state", lambda run_id, sc: _state())
    states = iter([_state(), _state(cut=True)])
    monkeypatch.setattr(
        belief,
        "apply",
        lambda state, ev: next(states) if ev.type == EventType.WORLD_TICK else state,
    )

    bus = FakeBus()
    core = loop.Core(bus, _scenario())
    await core.on_event(_tick())  # plan inicial (sano)
    await core.on_event(_tick())  # arista cortada → replan

    assert calls["n"] == 2
    started = [
        ReplanStarted.model_validate(e.payload)
        for e in bus.published
        if e.type == EventType.PLAN_REPLAN_STARTED
    ]
    assert len(started) == 2
