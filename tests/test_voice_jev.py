"""La percepción con Jev de punta a punta, sin red: bus en memoria, FakeJev guionado,
FakeHumalike y FakeLive sin guion. Comprueba el orden de la regla 4: lo observado
sale `observed`, lo que rellena el sistema sale `assumed_default`."""

import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from contracts import bus
from contracts.events import Event, EventType
from contracts.world import POI, RoadEdge
from voice import fake, humanlike, jev, pois
from voice.budget import COMPLETION_BUDGET_S
from voice.perception import CallPerception
from voice.webhooks import router

EDGE = "road:wp_sur_03-wp_sur_04"


@pytest.fixture
def journal() -> list[Event]:
    events: list[Event] = []
    bus.reset()
    bus.configure(run_id="run_test", writer=events.append)
    pois.set_scenario(
        [
            POI(
                id="poi_molino",
                name="Molino viejo",
                kind="landmark",
                x=1,
                z=1,
                waypoint_id="wp_sur_03",
            ),
            POI(
                id="poi_pueblo_b",
                name="Pueblo B",
                kind="village",
                x=5,
                z=5,
                waypoint_id="wp_sur_04",
            ),
        ],
        [RoadEdge(id=EDGE, a="wp_sur_03", b="wp_sur_04", length_m=100)],
        road_aliases={"pista del sur": EDGE},
    )
    humanlike.MONITORS.clear()
    humanlike._seen_calls.clear()
    yield events
    bus.reset()


def use_jev(script):
    jev.configure(fake.FakeJev(script))  # type: ignore[arg-type]


@pytest.fixture
def fakes():
    hl = fake.FakeHumalike()
    live = fake.FakeLive(post_tools=False)
    humanlike.configure(hl=hl, hr=live, autostart=False)  # type: ignore[arg-type]
    saved = humanlike.PLAN_WAIT_S
    humanlike.PLAN_WAIT_S = 0.2
    yield hl, live
    humanlike.PLAN_WAIT_S = saved
    humanlike.configure(None, None, True)


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        yield c


def facts_of(journal: list[Event]) -> dict[str, dict]:
    return {
        e.payload["key"]: e.payload
        for e in journal
        if e.type == EventType.WORLD_FACT_ASSERTED
    }


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


TURNS = [{"speaker": "caller", "text": "la pista del sur está cortada"}]


async def test_tick_publishes_observed_fact_with_kind_and_call_id(journal):
    use_jev(
        [
            {
                "urgency": ("critical", 0.95),
                "road_blocked": (EDGE, 0.93),
                "location_hint": ("poi_molino", 0.9),
                "people_immobile": ("3", 0.8),
            }
        ]
    )
    cp = CallPerception("hl_1")
    await cp.tick(TURNS)
    facts = facts_of(journal)
    cut = facts[f"road:{EDGE.removeprefix('road:')}:cut"]
    assert cut["value"] is True and cut["kind"] == "observed"
    assert cut["call_id"] == "hl_1" and cut["source"] == "call:hl_1"
    assert facts["poi:poi_molino:immobile"]["value"] == 3
    snap = [e for e in journal if e.type == EventType.CALL_COMPLETENESS][-1].payload
    assert snap["budget_s"] == COMPLETION_BUDGET_S["critical"]
    assert {f["key"]: f["status"] for f in snap["fields"]}["road_blocked"] == "observed"


async def test_budget_runs_out_and_the_gap_is_assumed_not_observed(journal):
    """El vecino da el corte y el lugar pero no cuántos; sin presupuesto, se asume."""
    use_jev(
        [
            {
                "urgency": ("critical", 0.95),
                "road_blocked": (EDGE, 0.93),
                "location_hint": ("poi_molino", 0.9),
                "people_immobile": ("not_stated", 0.9),
            }
        ]
    )
    clock = Clock()
    cp = CallPerception("hl_1", clock=clock)
    assert await cp.tick(TURNS) == "people_immobile"  # una pregunta, la que falta
    clock.t = 9.0  # pasan los 8 s del `critical`
    await cp.tick(TURNS, new_text=False)
    facts = facts_of(journal)
    assumed = facts["poi:poi_molino:immobile"]
    assert assumed["kind"] == "assumed_default" and assumed["confidence"] < 0.55
    assert facts[f"road:{EDGE.removeprefix('road:')}:cut"]["kind"] == "observed"
    snap = [e for e in journal if e.type == EventType.CALL_COMPLETENESS][-1].payload
    assert {f["key"]: f["status"] for f in snap["fields"]}[
        "people_immobile"
    ] == "assumed_default"


async def test_a_later_observation_falsifies_the_assumption(journal):
    use_jev(
        [
            {
                "urgency": ("critical", 0.95),
                "location_hint": ("poi_molino", 0.9),
                "people_immobile": ("not_stated", 0.9),
            },
            {"people_immobile": ("3", 0.9)},
        ]
    )
    clock = Clock()
    cp = CallPerception("hl_1", clock=clock)
    await cp.tick(TURNS)
    clock.t = 9.0
    await cp.tick(TURNS, new_text=False)
    clock.t = 12.0
    await cp.tick(TURNS + [{"speaker": "caller", "text": "son tres"}])
    last = [e for e in journal if e.payload.get("key") == "poi:poi_molino:immobile"][-1]
    assert last.payload["kind"] == "observed" and last.payload["value"] == 3


async def test_jev_down_means_no_perception_and_no_crash(journal):
    class Down:
        enabled = True
        failed_reason = None

        async def tick(self, state, questions):
            return None

    jev.configure(Down())  # type: ignore[arg-type]
    cp = CallPerception("hl_1")
    assert await cp.tick(TURNS) is None
    assert facts_of(journal) == {}


async def test_tool_is_only_a_trigger_its_values_are_ignored(client, journal, fakes):
    """El tool dice `road_blocked: ...` en texto libre. Los hechos salen de Jev."""
    use_jev(
        [
            {
                "urgency": ("critical", 0.95),
                "road_blocked": (EDGE, 0.93),
                "location_hint": ("poi_molino", 0.9),
                "people_immobile": ("2", 0.9),
            }
        ]
    )
    body = {
        "session_id": "s1",
        "run_id": "run_test",
        "params": {"road_blocked": "un camino inventado", "people_immobile": "99"},
    }
    r = await client.post("/webhooks/happyrobot/fact", json=body)
    assert r.status_code == 200, r.text
    facts = facts_of(journal)
    assert facts["poi:poi_molino:immobile"]["value"] == 2  # no 99
    assert all(f["kind"] == "observed" for f in facts.values())
    assert r.json()["resolved_poi_name"] == "Molino viejo"


async def test_end_of_call_closes_with_a_final_tick(client, journal, fakes):
    use_jev(
        [
            {
                "urgency": ("critical", 0.95),
                "road_blocked": (EDGE, 0.93),
                "location_hint": ("poi_molino", 0.9),
                "people_immobile": ("3", 0.9),
            }
        ]
    )
    end = {
        "type": "end",
        "session_id": "s2",
        "status": "completed",
        "direction": "inbound",
        "transcript": "vecino: la pista del sur está cortada en el molino, tres no pueden andar",
    }
    r = await client.post("/webhooks/happyrobot/call", json=end)
    assert r.status_code == 200
    ended = next(e for e in journal if e.type == EventType.CALL_ENDED)
    assert ended.payload["facts"]["resolved_poi_id"] == "poi_molino"
    assert ended.payload["facts"]["road_blocked"] == EDGE
    assert ended.payload["facts"]["people_immobile"] == 3


def test_turns_from_text_labels_the_agent_as_operator():
    turns = humanlike.turns_from_text(
        "operador: ¿dónde está?\nvecino: en el molino\nsigo aquí"
    )
    assert [t["speaker"] for t in turns] == ["operator", "caller"]
    assert turns[1]["text"] == "en el molino sigo aquí"


def test_a_jev_answer_maps_to_typed_answers(journal):
    """`Noul` no trae `confidence`; el score de urgency es un índice de nivel."""
    q = pois.questions()
    ans = SimpleNamespace(choice="poi_molino", confidence=0.91)
    got = jev._read(q["location_hint"], ans)
    assert got is not None and got.value == "poi_molino" and got.stated
    got = jev._read(q["urgency"], SimpleNamespace(score=1.96, confidence=0.7))
    assert got is not None and got.value == "critical"
    got = jev._read(q["confirmed_order"], SimpleNamespace(noul=0.1))
    assert got is not None and got.value is False and got.confidence == pytest.approx(0.9)


async def test_tick_translates_the_sdk_response_and_never_raises(journal):
    class Sdk:
        async def system_one(self, state, questions, **kw):
            assert set(questions) >= {"location_hint", "road_blocked"}
            return SimpleNamespace(
                answers={
                    "location_hint": SimpleNamespace(choice="poi_molino", confidence=0.9)
                },
                usage=SimpleNamespace(input_tokens=42),
            )

    client = jev.JevClient(api_key="k", client=Sdk())
    t0 = time.perf_counter()
    perc = await client.tick(jev.build_state(TURNS), pois.questions())
    assert perc is not None and perc.answers["location_hint"].value == "poi_molino"
    assert perc.tokens_in == 42 and time.perf_counter() - t0 < 1


def test_no_jev_extract_model_is_closed_over_the_scenario(journal):
    """Plan B: fenic solo puede devolver ids del escenario, no texto libre."""
    from pydantic import ValidationError

    from voice.extract_schema import build_extract_model, to_call_facts

    model = build_extract_model(list(pois.pois()), list(pois.roads()))
    ok = model(location_hint="poi_molino", road_blocked=EDGE)
    with pytest.raises(ValidationError):
        model(road_blocked="una carretera inventada")
    cf = to_call_facts(ok.model_dump(), {"poi_molino": "Molino viejo"})
    assert cf.resolved_poi_id == "poi_molino" and cf.location_hint == "Molino viejo"
    assert pois.resolve_edge_local(EDGE) == EDGE


async def test_synthetic_calls_are_scored_by_jev_without_touching_the_world(journal):
    from types import SimpleNamespace as NS

    from voice import synthetic

    class Scorer:
        enabled = True
        failed_reason = None

        async def tick(self, state, questions):
            text = state["transcript"][0]["text"]
            hot = "cortada" in text
            return (
                jev.Perception(answers={"relevant": jev.Answer(hot, 0.9)}, latency_ms=1.0)
                if questions
                else NS()
            )

    jev.configure(Scorer())  # type: ignore[arg-type]
    calls = synthetic.generate(20, 0.0, seed=1)
    await synthetic.score_relevance(calls)
    scored = [c.analysis["jev_relevance"] for c in calls]
    assert len(scored) == 20 and all(0 <= p <= 1 for p in scored)
    assert facts_of(journal) == {}  # etiquetar no publica hechos
