"""belief: eventos → WorldState, puro. Sin sim, sin red."""

from datetime import UTC, datetime

from contracts.calls import Fact
from contracts.events import Event, EventType
from contracts.scenario import HazardSpec, Scenario, Waypoint
from contracts.world import POI, CivilianGroup, RoadEdge, Unit, Wind
from core.belief import apply, apply_fact, initial_state

EDGE = "wp_sur_03-wp_sur_04"


def scenario() -> Scenario:
    return Scenario(
        id="t",
        name="test",
        hazard=HazardSpec(
            kind="wildfire", origin_cell="cell_1_1", wind=Wind(bearing_deg=270, speed=1.0)
        ),
        pois=[
            POI(
                id="poi_molino",
                name="Molino viejo",
                kind="landmark",
                x=10,
                z=10,
                waypoint_id="wp_sur_03",
            ),
            POI(
                id="poi_pueblo_b",
                name="Pueblo B",
                kind="village",
                x=50,
                z=50,
                waypoint_id="wp_sur_04",
            ),
        ],
        units=[
            Unit(
                id="unit_truck1", kind="fire_truck", x=0, z=0, capabilities=["extinguish"]
            )
        ],
        waypoints=[
            Waypoint(id="wp_sur_03", x=10, z=10),
            Waypoint(id="wp_sur_04", x=50, z=50),
        ],
        roads=[RoadEdge(id=EDGE, a="wp_sur_03", b="wp_sur_04", length_m=100)],
        civilians=[CivilianGroup(id="civ_b", poi_id="poi_pueblo_b", count=12)],
    )


def ev(type: EventType, payload: dict, seq: int = 1, t_sim: float = 5.0) -> Event:
    return Event(
        run_id="r",
        seq=seq,
        t_wall=datetime.now(UTC),
        t_sim=t_sim,
        type=type,
        source="test",
        payload=payload,
    )


def fact(key: str, value, source="call:s1", confidence=0.9) -> Fact:
    return Fact(
        key=key,
        value=value,
        confidence=confidence,
        source=source,
        severity="critical",
        t_sim=5.0,
    )


def test_initial_state_indexes_by_id():
    s = initial_state("r", scenario())
    assert s.roads[EDGE].cut is False
    assert s.units["unit_truck1"].status == "idle"
    assert s.civilians["civ_b"].count == 12


def test_road_cut_fact_marks_edge():
    s = initial_state("r", scenario())
    s2 = apply_fact(s, fact(f"road:{EDGE}:cut", True))
    assert s2.roads[EDGE].cut is True
    assert s.roads[EDGE].cut is False  # inmutable
    assert s2.facts[-1].source == "call:s1"


def test_unknown_key_does_not_touch_state():
    s = initial_state("r", scenario())
    s2 = apply_fact(s, fact("inventada:del:todo", True))
    assert s2 == s


def test_immobile_creates_group_for_poi():
    s = initial_state("r", scenario())
    s2 = apply_fact(s, fact("poi:poi_molino:immobile", 3))
    g = next(g for g in s2.civilians.values() if g.poi_id == "poi_molino")
    assert g.immobile == 3 and g.count >= 3


def test_human_fact_wins():
    s = initial_state("r", scenario())
    s = apply_fact(s, fact(f"road:{EDGE}:cut", True, source="human", confidence=1.0))
    s2 = apply_fact(s, fact(f"road:{EDGE}:cut", False, source="call:s2"))
    assert s2.roads[EDGE].cut is True
    assert len(s2.facts) == 2  # el hecho se registra aunque no gane


def test_apply_fact_asserted_event_and_seq():
    s = initial_state("r", scenario())
    s2 = apply(
        s,
        ev(
            EventType.WORLD_FACT_ASSERTED,
            {
                "key": f"road:{EDGE}:cut",
                "value": True,
                "confidence": 0.9,
                "source": "call:s1",
                "severity": "critical",
            },
            seq=7,
        ),
    )
    assert s2.seq == 7 and s2.roads[EDGE].cut is True


def test_unknown_event_only_advances_seq():
    s = initial_state("r", scenario())
    s2 = apply(s, ev(EventType.PLAN_DIVERGENCE, {"value": 0.1, "broken": []}, seq=3))
    assert s2.seq == 3 and s2.roads == s.roads


def test_override_assert_fact():
    s = initial_state("r", scenario())
    s2 = apply(
        s,
        ev(
            EventType.HUMAN_OVERRIDE,
            {
                "kind": "assert_fact",
                "target": f"road:{EDGE}:cut",
                "value": True,
                "note": "jefe",
            },
        ),
    )
    assert s2.roads[EDGE].cut is True and s2.facts[-1].confidence == 1.0


def test_only_an_observed_fact_can_reopen_a_cut_road():
    """Regla 4: lo asumido sostiene la dirección segura (cortada), nunca la contraria."""
    cut = apply_fact(initial_state("r", scenario()), fact(f"road:{EDGE}:cut", True))
    assumed_open = fact(f"road:{EDGE}:cut", False).model_copy(
        update={"kind": "assumed_default"}
    )
    still_cut = apply_fact(cut, assumed_open)
    assert still_cut.roads[EDGE].cut is True
    assert still_cut.facts[-1].kind == "assumed_default"  # se registra, no se aplica
    observed_open = apply_fact(cut, fact(f"road:{EDGE}:cut", False))
    assert observed_open.roads[EDGE].cut is False


def test_fact_event_carries_kind_and_call_id_into_the_state():
    s = initial_state("r", scenario())
    payload = {
        "key": f"road:{EDGE}:cut",
        "value": True,
        "confidence": 0.3,
        "source": "call:s1",
        "severity": "critical",
        "kind": "assumed_default",
        "call_id": "s1",
    }
    last = apply(s, ev(EventType.WORLD_FACT_ASSERTED, payload)).facts[-1]
    assert last.kind == "assumed_default" and last.call_id == "s1"
    del payload["kind"], payload["call_id"]  # journals viejos: siguen siendo observados
    assert (
        apply(s, ev(EventType.WORLD_FACT_ASSERTED, payload)).facts[-1].kind == "observed"
    )


def test_plan_moves_a_unit_only_if_it_has_a_way_to_go():
    """`plan.emitted` pone `moving` a la unidad asignada con ruta por recorrer; con
    una ruta de un solo waypoint (ya está allí) el estado lo dice el sim."""
    s = initial_state("r", scenario())
    plan = {
        "id": "plan_r_1",
        "run_id": "r",
        "created_t": 5.0,
        "policy": {"rationale": "t"},
        "assignments": [
            {
                "unit_id": "unit_truck1",
                "task_id": "task_a",
                "route": ["wp_sur_03"],
                "eta_s": 0.0,
                "cost": 1.0,
            }
        ],
        "context": {"assumptions": [], "world_seq": 1},
    }
    parked = apply(s, ev(EventType.PLAN_EMITTED, plan))
    assert parked.units["unit_truck1"].status == "idle"
    assert parked.units["unit_truck1"].task_id == "task_a"
    plan["assignments"][0]["route"] = ["wp_sur_03", "wp_sur_04"]
    plan["assignments"][0]["eta_s"] = 25.0
    moving = apply(s, ev(EventType.PLAN_EMITTED, plan))
    assert moving.units["unit_truck1"].status == "moving"
