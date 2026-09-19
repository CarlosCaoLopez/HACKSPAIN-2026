"""El tool `reportar_situacion` de la llamada a un pueblo (alcalde que se evacúa, o
vecino al que avisamos), sin red: bus en memoria, sin Jev ni Humalike de por medio,
porque el POI ya se conoce y no hay nada que resolver por texto."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from contracts import bus
from contracts.events import Event, EventType
from contracts.settings import settings
from voice.webhooks import router

HEADERS = {"X-Vela-Token": settings.webhook_shared_token}  # vacío si no hay .env


@pytest.fixture
def journal() -> list[Event]:
    events: list[Event] = []
    bus.reset()
    bus.configure(run_id="run_test", writer=events.append)
    yield events
    bus.reset()


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


def _keys(journal: list[Event]) -> dict[str, object]:
    return {
        e.payload["key"]: e.payload["value"]
        for e in journal
        if e.type == EventType.WORLD_FACT_ASSERTED
    }


async def test_mayor_reports_immobile_people_and_ambulance_is_promised(
    client, journal
) -> None:
    """El alcalde dice que hay inmóviles: el hecho sale `observed`, con gravedad
    `critical`, y el ack promete la ambulancia — sin adivinar el POI por texto."""
    body = {
        "poi_id": "poi_pueblo_a",
        "role": "evacuation",
        "call_id": "run_hr_1",
        "params": {
            "headcount": "30",
            "people_immobile": "2",
            "injuries": "0",
            "confirmed_order": "sí",
        },
    }
    r = await client.post("/webhooks/happyrobot/village", json=body, headers=HEADERS)
    assert r.status_code == 200, r.text
    ack = r.json()
    assert ack["ok"] is True
    assert ack["ambulance_dispatched"] is True
    assert "ambulancia" in ack["message"] and "2" in ack["message"]
    assert ack["facts_published"] == 4  # headcount, immobile, injuries, confirmed

    facts = _keys(journal)
    assert facts["poi:poi_pueblo_a:headcount"] == 30
    assert facts["poi:poi_pueblo_a:immobile"] == 2
    assert facts["poi:poi_pueblo_a:injuries"] == 0
    assert facts["poi:poi_pueblo_a:confirmed"] is True

    fa = next(
        e.payload
        for e in journal
        if e.type == EventType.WORLD_FACT_ASSERTED
        and e.payload["key"] == "poi:poi_pueblo_a:immobile"
    )
    assert fa["kind"] == "observed"  # el alcalde lo dice él mismo, no se adivina
    assert fa["severity"] == "critical"
    assert fa["source"] == "call:run_hr_1"


async def test_mayor_reports_no_immobile_no_ambulance_promised(client, journal) -> None:
    body = {
        "poi_id": "poi_pueblo_a",
        "role": "evacuation",
        "call_id": "run_hr_2",
        "params": {"headcount": "12", "people_immobile": "0"},
    }
    r = await client.post("/webhooks/happyrobot/village", json=body, headers=HEADERS)
    ack = r.json()
    assert ack["ambulance_dispatched"] is False
    assert "ambulancia" not in ack["message"]


async def test_neighbor_reports_no_capacity(client, journal) -> None:
    """El pueblo vecino dice que no tiene sitio: la clave nueva `shelter_ready`
    queda a `False`, y el ack no promete una ambulancia (no es su emergencia)."""
    body = {
        "poi_id": "poi_pueblo_b",
        "role": "neighbor_alert",
        "call_id": "run_hr_3",
        "params": {"capacity_available": "no"},
    }
    r = await client.post("/webhooks/happyrobot/village", json=body, headers=HEADERS)
    assert r.status_code == 200, r.text
    ack = r.json()
    assert ack["ambulance_dispatched"] is False
    assert "sitio" in ack["message"]
    facts = _keys(journal)
    assert facts["poi:poi_pueblo_b:shelter_ready"] is False


async def test_neighbor_reports_capacity_available(client, journal) -> None:
    body = {
        "poi_id": "poi_pueblo_b",
        "role": "neighbor_alert",
        "call_id": "run_hr_4",
        "params": {"capacity_available": "sí"},
    }
    r = await client.post("/webhooks/happyrobot/village", json=body, headers=HEADERS)
    ack = r.json()
    assert "acoger" in ack["message"]
    assert _keys(journal)["poi:poi_pueblo_b:shelter_ready"] is True


async def test_notes_field_is_not_published_as_a_fact(client, journal) -> None:
    """`notes` es texto libre para el registro, no una clave de `contracts.factkeys`:
    no debe intentar publicarse ni tumbar el webhook."""
    body = {
        "poi_id": "poi_pueblo_a",
        "role": "evacuation",
        "call_id": "run_hr_5",
        "params": {"headcount": "5", "notes": "el alcalde está en el ayuntamiento"},
    }
    r = await client.post("/webhooks/happyrobot/village", json=body, headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["facts_published"] == 1
    assert "poi:poi_pueblo_a:notes" not in _keys(journal)


async def test_without_poi_id_nothing_is_published_but_it_does_not_crash(
    client, journal
) -> None:
    body = {"role": "evacuation", "call_id": "run_hr_6", "params": {"headcount": "5"}}
    r = await client.post("/webhooks/happyrobot/village", json=body, headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["facts_published"] == 0
    assert not journal


async def test_wrong_token_is_rejected(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "webhook_shared_token", "vela-token")
    r = await client.post(
        "/webhooks/happyrobot/village",
        json={"poi_id": "poi_pueblo_a", "params": {}},
        headers={"X-Vela-Token": "no-es-el-token"},
    )
    assert r.status_code == 401
