"""El tool `report_fact` de punta a punta, sin red: bus en memoria, FakeHumalike,
FakeLive sin guion. Comprueba el orden: hechos antes que el ack de Humalike."""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from contracts import bus
from contracts.events import Event, EventType
from contracts.world import POI, RoadEdge
from voice import fake, humanlike, pois
from voice.webhooks import router

EDGE = "wp_sur_03-wp_sur_04"
from contracts.settings import settings

HEADERS = {"X-Vela-Token": settings.webhook_shared_token}  # vacío si no hay .env


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


@pytest.fixture
def fakes():
    hl = fake.FakeHumalike()
    live = fake.FakeLive(post_tools=False)
    humanlike.configure(hl=hl, hr=live, autostart=False)  # type: ignore[arg-type]
    yield hl, live
    humanlike.configure(None, None, True)


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


BODY = {
    "session_id": "sess_1",
    "run_id": "run_test",
    "params": {
        "location_hint": "el molino viejo",
        "road_blocked": "la pista del sur",
        "people_immobile": "3",
        "urgency": "critical",
    },
}


async def test_tool_publishes_facts_then_ack(client, journal, fakes):
    r = await client.post("/webhooks/happyrobot/fact", json=BODY, headers=HEADERS)
    assert r.status_code == 200, r.text
    ack = r.json()
    assert ack["ack"] is True
    assert ack["resolved_poi_name"] == "Molino viejo"
    assert "Molino viejo" in ack["message"]
    assert ack["facts_published"] == 3

    types = [e.type for e in journal]
    facts = [e for e in journal if e.type == EventType.WORLD_FACT_ASSERTED]
    keys = {e.payload["key"] for e in facts}
    assert keys == {f"road:{EDGE}:cut", f"road:{EDGE}:cause", "poi:poi_molino:immobile"}
    assert all(e.source == "call:sess_1" for e in facts)
    # los hechos van antes que la lectura emocional
    assert types.index(EventType.WORLD_FACT_ASSERTED) < types.index(EventType.CALL_AFFECT)
    affect = next(e for e in journal if e.type == EventType.CALL_AFFECT)
    assert affect.payload["emotions"][0]["type"] == "fear"


async def test_duplicate_tool_post_is_idempotent(client, journal, fakes):
    a = (
        await client.post("/webhooks/happyrobot/fact", json=BODY, headers=HEADERS)
    ).json()
    n = len([e for e in journal if e.type == EventType.WORLD_FACT_ASSERTED])
    b = (
        await client.post("/webhooks/happyrobot/fact", json=BODY, headers=HEADERS)
    ).json()
    assert a == b
    assert len([e for e in journal if e.type == EventType.WORLD_FACT_ASSERTED]) == n


async def test_signal_requested_reaches_agent(client, journal, fakes):
    _, live = fakes
    await client.post("/webhooks/happyrobot/fact", json=BODY, headers=HEADERS)
    mon = humanlike.MONITORS["sess_1"]
    await mon.on_signal_requested(
        "unit_dispatched", {"unit": "camión 2", "route": "pista norte", "eta_s": 40}, []
    )
    assert live.signals and live.signals[-1]["kind"] == "unit_dispatched"
    assert "camión 2" in live.signals[-1]["message"]
    assert any(e.type == EventType.CALL_SIGNAL_SENT for e in journal)


async def test_end_of_call_archives_with_health_score(client, journal, fakes):
    await client.post("/webhooks/happyrobot/fact", json=BODY, headers=HEADERS)
    mon = humanlike.MONITORS["sess_1"]
    await mon.on_message("user", "estoy en el molino viejo")
    end = {
        "type": "end",
        "session_id": "sess_1",
        "status": "completed",
        "direction": "inbound",
    }
    r = await client.post("/webhooks/happyrobot/call", json=end, headers=HEADERS)
    assert r.status_code == 200
    ended = next(e for e in journal if e.type == EventType.CALL_ENDED)
    assert ended.payload["health_score"] == pytest.approx(0.82)
    assert "molino" in ended.payload["transcript"]
    # duplicado
    assert (
        await client.post("/webhooks/happyrobot/call", json=end, headers=HEADERS)
    ).json() == {"dup": True}


async def test_coach_on_long_silence(journal, fakes):
    _, live = fakes
    mon = humanlike.get_or_start("sess_2", "run_test")
    mon.state.thread_id = "thr"
    await mon.on_message("user", "...")
    await asyncio.sleep(0.6)  # el foresee de fondo tarda 0,3 s
    coach = [s for s in live.signals if s.get("kind") == "coach"]
    assert coach and coach[-1]["action"] == "acknowledge"
    assert coach[-1]["tone"] == "calm" and coach[-1]["pace"] == "slow"


async def test_signal_sent_carries_latency_and_causes(client, journal, fakes):
    _, live = fakes
    await client.post("/webhooks/happyrobot/fact", json=BODY, headers=HEADERS)
    mon = humanlike.MONITORS["sess_1"]
    await mon.on_signal_requested(
        "unit_dispatched", {"unit": "camión 2", "route": "pista norte", "eta_s": 40}, [42]
    )
    sent = next(
        e
        for e in journal
        if e.type == EventType.CALL_SIGNAL_SENT and e.payload["key"] == "unit_dispatched"
    )
    assert sent.causes == [42]
    assert sent.payload["refined"] is True
    assert 0 <= sent.payload["latency_ms"] < 1500
    assert sent.payload["message"] == live.signals[-1]["message"]
