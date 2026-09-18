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
