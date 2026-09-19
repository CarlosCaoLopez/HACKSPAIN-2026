"""Telegram de punta a punta, sin red: bus en memoria, FakeHumalike, `sendMessage`
parcheado. El pin es el «dónde» exacto que la llamada no pudo dar (use_cases 4:25)."""

from __future__ import annotations

import asyncio
import logging
import math

import httpx
import pytest
from fastapi import FastAPI

import voice
from contracts import bus
from contracts.calls import CallFacts
from contracts.events import Event, EventType
from contracts.scenario import GeoAnchor
from contracts.settings import settings
from voice import fake, humanlike, pois, telegram
from voice.webhooks import router

CHAT = "4471123"
CALL_ID = f"tg_{CHAT}"
SECRET = "s3cret"
SECRET_HEADERS = {telegram.SECRET_HEADER: SECRET}
SOL = (40.4168, -3.7038)  # Puerta del Sol: el ancla de wildfire_ridge
GEO = GeoAnchor(lat=SOL[0], lon=SOL[1], x=187, z=94, scale=0.01, snap_m=80)
POIS = [
    {
        "id": "poi_pueblo_b",
        "name": "Pueblo B",
        "kind": "village",
        "x": 187,
        "z": 94,
        "waypoint_id": "wp_pueblo_b",
    },
    {
        "id": "poi_pueblo_a",
        "name": "Pueblo A",
        "kind": "village",
        "x": -120,
        "z": 40,
        "waypoint_id": "wp_pueblo_a",
    },
]


@pytest.fixture
def journal(monkeypatch) -> list[Event]:
    from contracts.world import POI

    events: list[Event] = []
    bus.reset()
    bus.configure(run_id="run_test", writer=events.append)
    pois.set_scenario([POI.model_validate(p) for p in POIS], [], geo=GEO)
    telegram.reset()
    humanlike.MONITORS.clear()
    humanlike.RECENT_ENDED.clear()
    humanlike._seen_calls.clear()
    monkeypatch.setattr(settings, "telegram_secret_token", SECRET)
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(settings, "telegram_bot_username", "")
    monkeypatch.setattr(settings, "vela_no_telegram", False)
    yield events
    bus.reset()


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
def no_extract(monkeypatch):
    """Sin fenic: el texto cae a las reglas. Los tests no hablan con ningún LLM."""

    async def none(self, text):
        return None

    monkeypatch.setattr(voice.VoiceGateway, "extract", none)


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    """Con token: `sendMessage` se captura en vez de salir a la red."""
    calls: list[dict] = []
    monkeypatch.setattr(settings, "telegram_bot_token", "123:abc")

    async def api(method, body):
        calls.append({"method": method, **body})
        return {"message_id": 77}

    monkeypatch.setattr(telegram, "api_call", api)
    return calls


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(router)
    app.include_router(telegram.router, prefix="/webhooks")  # como lo monta el gateway
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://t", headers=SECRET_HEADERS
    ) as c:
        yield c


def _of(journal: list[Event], t: EventType) -> list[Event]:
    return [e for e in journal if e.type == t]


def _latlon(x: float, z: float) -> tuple[float, float]:
    """La inversa de `telegram.project`: dónde hay que estar para caer en (x, z)."""
    lat = GEO.lat - (z - GEO.z) / (telegram.M_PER_DEG_LAT * GEO.scale)
    lon = GEO.lon + (x - GEO.x) / (
        telegram.M_PER_DEG_LAT * math.cos(math.radians(GEO.lat)) * GEO.scale
    )
    return lat, lon


# --- proyección ---------------------------------------------------------------------


def test_project_anchor_lands_on_pueblo_b(journal):
    x, z = telegram.project(GEO, *SOL)
    assert (round(x), round(z)) == (187, 94)
    assert pois.nearest_poi(x, z, GEO.snap_m).id == "poi_pueblo_b"


def test_project_axes_east_and_south():
    # 100 m al este y 100 m al norte reales → +1 en x, −1 en z (scale 0.01)
    x, z = telegram.project(GEO, SOL[0] + 100 / 111_320, SOL[1])
    assert x == pytest.approx(187, abs=0.01) and z == pytest.approx(93, abs=0.01)
    import math

    dlon = 100 / (111_320 * math.cos(math.radians(SOL[0])))
    x, z = telegram.project(GEO, SOL[0], SOL[1] + dlon)
    assert x == pytest.approx(188, abs=0.01) and z == pytest.approx(94, abs=0.01)


def test_far_pin_does_not_snap(journal):
    # 20 km al norte reales = 200 m de mundo: fuera del snap de 80
    x, z = telegram.project(GEO, SOL[0] + 20_000 / 111_320, SOL[1])
    assert pois.nearest_poi(x, z, GEO.snap_m) is None


# --- el webhook -----------------------------------------------------------------------


async def test_pin_publishes_started_location_and_observed_fact(
    client, journal, fakes, no_extract
):
    upd = telegram.fake_update(CHAT, *SOL, "Estoy aquí, junto a unas casas", update_id=1)
    r = await client.post("/webhooks/telegram", json=upd)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["poi_id"] == "poi_pueblo_b" and body["sent"].startswith("tg_dry_")
    assert "Pueblo B" in body["ack"]

    started = _of(journal, EventType.CALL_STARTED)
    assert len(started) == 1
    assert started[0].payload["channel"] == "telegram"
    assert started[0].payload["call_id"] == CALL_ID and started[0].payload["to"] == CHAT
    assert started[0].payload["direction"] == "inbound"

    loc = _of(journal, EventType.CITIZEN_LOCATION)
    assert len(loc) == 1
    p = loc[0].payload
    assert p["poi_id"] == "poi_pueblo_b" and p["poi_name"] == "Pueblo B"
    assert (round(p["x"]), round(p["z"])) == (187, 94) and p["live"] is False
    assert p["text"] == "Estoy aquí, junto a unas casas"

    facts = _of(journal, EventType.WORLD_FACT_ASSERTED)
    assert len(facts) == 1
    f = facts[0]
    assert f.payload["key"] == "poi:poi_pueblo_b:confirmed" and f.payload["value"] is True
    assert f.payload["kind"] == "observed" and f.payload["severity"] == "critical"
    assert f.source == f"call:{CALL_ID}" and f.payload["call_id"] == CALL_ID
    assert f.causes == [loc[0].seq]

    # la transcripción para el CallsPanel: el vecino y el acuse del operador
    turns = _of(journal, EventType.CALL_TRANSCRIPT_PARTIAL)
    assert [t.payload["speaker"] for t in turns] == ["vecino", "operador"]


async def test_text_goes_through_perception_with_pin_poi(
    client, journal, fakes, no_extract
):
    text = "Estoy al final de la pista junto a unas casas, mi madre no puede andar"
    upd = telegram.fake_update(CHAT, *SOL, text, update_id=2)
    r = await client.post("/webhooks/telegram", json=upd)
    assert r.status_code == 200 and r.json()["facts_from_text"] == 1
    keys = {e.payload["key"]: e for e in _of(journal, EventType.WORLD_FACT_ASSERTED)}
    imm = keys["poi:poi_pueblo_b:immobile"]
    assert imm.payload["value"] == 1 and imm.payload["kind"] == "inferred"
    assert imm.source == f"call:{CALL_ID}"


async def test_text_uses_extract_when_available(client, journal, fakes, monkeypatch):
    async def extract(self, text):
        return CallFacts(people_immobile=2, injuries=1, urgency="critical")

    monkeypatch.setattr(voice.VoiceGateway, "extract", extract)
    upd = telegram.fake_update(CHAT, *SOL, "hay dos que no pueden andar", update_id=3)
    await client.post("/webhooks/telegram", json=upd)
    keys = {
        e.payload["key"]: e.payload for e in _of(journal, EventType.WORLD_FACT_ASSERTED)
    }
    assert keys["poi:poi_pueblo_b:immobile"]["value"] == 2
    assert keys["poi:poi_pueblo_b:injuries"]["value"] == 1
    assert keys["poi:poi_pueblo_b:confirmed"]["kind"] == "observed"


async def test_wrong_secret_is_401(client, journal, fakes, monkeypatch):
    upd = telegram.fake_update(CHAT, *SOL, None, update_id=4)
    r = await client.post(
        "/webhooks/telegram", json=upd, headers={telegram.SECRET_HEADER: "otro"}
    )
    assert r.status_code == 401 and r.json()["detail"] == "secret"
    r = await client.post(
        "/webhooks/telegram", json=upd, headers={telegram.SECRET_HEADER: ""}
    )
    assert r.status_code == 401
    r = await client.post("/webhooks/telegram", json=upd)  # el secreto bueno
    assert r.status_code == 200
    assert _of(journal, EventType.CALL_STARTED)


async def test_without_secret_the_route_is_closed(client, journal, fakes, monkeypatch):
    """Sin `TELEGRAM_SECRET_TOKEN` la ruta no queda abierta: 401 con el motivo."""
    monkeypatch.setattr(settings, "telegram_secret_token", "")
    upd = telegram.fake_update(CHAT, *SOL, None, update_id=4)
    r = await client.post("/webhooks/telegram", json=upd)
    assert r.status_code == 401 and r.json()["detail"] == "sin TELEGRAM_SECRET_TOKEN"
    assert not journal


@pytest.fixture
async def guarded_client():
    """La app como la monta el gateway: el middleware del `X-Vela-Token` delante."""
    from gateway.main import _webhook_guard

    app = FastAPI()
    app.middleware("http")(_webhook_guard)
    app.include_router(router)
    app.include_router(telegram.router, prefix="/webhooks")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def test_gateway_guard_exempts_telegram_only_with_secret(
    guarded_client, journal, fakes, no_extract, monkeypatch
):
    monkeypatch.setattr(settings, "webhook_shared_token", "vela-token")
    upd = telegram.fake_update(CHAT, *SOL, None, update_id=4)
    # con secreto: Telegram no manda X-Vela-Token y aun así entra (su secreto vale)
    r = await guarded_client.post("/webhooks/telegram", json=upd, headers=SECRET_HEADERS)
    assert r.status_code == 200, r.text
    # sin secreto configurado: cerrado, aunque se traiga el token compartido
    monkeypatch.setattr(settings, "telegram_secret_token", "")
    r = await guarded_client.post(
        "/webhooks/telegram",
        json=telegram.fake_update(CHAT, *SOL, None, update_id=5),
        headers={"X-Vela-Token": "vela-token"},
    )
    assert r.status_code == 401 and r.json()["detail"] == "sin TELEGRAM_SECRET_TOKEN"
    # el resto de /webhooks/* sigue con el token compartido
    r = await guarded_client.post("/webhooks/happyrobot/call", json={})
    assert r.status_code == 401 and r.json()["detail"] == "token inválido"


async def test_without_geo_pin_has_no_poi(client, journal, fakes, no_extract):
    pois.set_geo(None)
    upd = telegram.fake_update(CHAT, *SOL, None, update_id=5)
    r = await client.post("/webhooks/telegram", json=upd)
    assert r.status_code == 200 and r.json()["poi_id"] is None
    loc = _of(journal, EventType.CITIZEN_LOCATION)[0].payload
    assert loc["x"] is None and loc["z"] is None and loc["poi_id"] is None
    assert not _of(journal, EventType.WORLD_FACT_ASSERTED)


async def test_duplicate_update_publishes_nothing(client, journal, fakes, no_extract):
    upd = telegram.fake_update(CHAT, *SOL, None, update_id=6)
    await client.post("/webhooks/telegram", json=upd)
    n = len(journal)
    r = await client.post("/webhooks/telegram", json=upd)
    assert r.json() == {"dup": True} and len(journal) == n


async def test_failed_handle_lets_telegram_retry(
    client, journal, fakes, no_extract, monkeypatch
):
    """Si `handle` lanza, el update no se marca como visto: el reintento se procesa."""
    real = telegram.handle
    attempts = {"n": 0}

    async def flaky(inc):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("bus caído")
        return await real(inc)

    monkeypatch.setattr(telegram, "handle", flaky)
    upd = telegram.fake_update(CHAT, *SOL, None, update_id=40)
    r = await client.post("/webhooks/telegram", json=upd)
    assert r.status_code == 200  # Telegram no reintenta en bucle
    assert r.json()["ok"] is False and "bus caído" in r.json()["error"]
    assert not _of(journal, EventType.CITIZEN_LOCATION)
    r = await client.post("/webhooks/telegram", json=upd)
    assert r.json()["ok"] is True and r.json()["poi_id"] == "poi_pueblo_b"
    assert len(_of(journal, EventType.CITIZEN_LOCATION)) == 1
    assert (await client.post("/webhooks/telegram", json=upd)).json() == {"dup": True}


async def test_live_location_acks_only_when_poi_changes(
    client, journal, fakes, no_extract
):
    """Ubicación en vivo: el mapa sigue cada edición; el hecho y el acuse, solo al
    cambiar de POI. Sin eso, cada edición era un acuse, un hecho y un replan."""
    first = telegram.fake_update(CHAT, *SOL, None, live=True, update_id=50)
    r = await client.post("/webhooks/telegram", json=first)
    assert r.json()["poi_id"] == "poi_pueblo_b" and r.json()["ack"]
    # 20 m más al norte (real): mismo POI
    moved = telegram.fake_update(
        CHAT, SOL[0] + 20 / 111_320, SOL[1], None, live=True, update_id=51
    )
    r = await client.post("/webhooks/telegram", json=moved)
    assert r.json()["unchanged"] is True and r.json()["ack"] is None
    assert len(_of(journal, EventType.CITIZEN_LOCATION)) == 2
    assert len(_of(journal, EventType.WORLD_FACT_ASSERTED)) == 1
    turns = _of(journal, EventType.CALL_TRANSCRIPT_PARTIAL)
    assert len(turns) == 1  # un solo acuse
    # el pin se va a Pueblo A: hecho nuevo y acuse nuevo
    lat, lon = _latlon(POIS[1]["x"], POIS[1]["z"])
    r = await client.post(
        "/webhooks/telegram",
        json=telegram.fake_update(CHAT, lat, lon, None, live=True, update_id=52),
    )
    assert r.json()["poi_id"] == "poi_pueblo_a" and "Pueblo A" in r.json()["ack"]
    keys = [e.payload["key"] for e in _of(journal, EventType.WORLD_FACT_ASSERTED)]
    assert keys == ["poi:poi_pueblo_b:confirmed", "poi:poi_pueblo_a:confirmed"]
    assert len(_of(journal, EventType.CALL_TRANSCRIPT_PARTIAL)) == 2


async def test_pin_beats_jev_location(client, journal, fakes, monkeypatch):
    """Con Jev activo, el texto («al molino») no puede pisar el POI del pin: Jev no
    pregunta por `location_hint` y el tick lo repone como observado con el del pin."""
    from voice.perception import CallPerception

    monkeypatch.setattr(CallPerception, "active", property(lambda self: True))

    async def ask(self, turns):
        return {
            "location_hint": ("poi_pueblo_a", 0.95),
            "people_immobile": ("1", 0.9),
            "urgency": ("critical", 0.9),
        }

    monkeypatch.setattr(CallPerception, "_ask_jev", ask)
    upd = telegram.fake_update(
        CHAT, *SOL, "Estamos en Pueblo A, mi madre no puede andar", update_id=60
    )
    r = await client.post("/webhooks/telegram", json=upd)
    assert r.status_code == 200 and r.json()["facts_from_text"] == 1
    facts = {
        e.payload["key"]: e.payload for e in _of(journal, EventType.WORLD_FACT_ASSERTED)
    }
    assert facts["poi:poi_pueblo_b:immobile"]["value"] == 1
    assert not any(k.startswith("poi:poi_pueblo_a") for k in facts)
    comp = _of(journal, EventType.CALL_COMPLETENESS)[-1].payload
    loc = next(f for f in comp["fields"] if f["key"] == "location_hint")
    assert loc["status"] == "observed" and loc["value"] == "poi_pueblo_b"
    mon = telegram._sessions[CALL_ID]
    assert mon.perception.pinned_poi == "poi_pueblo_b"


async def test_bot_token_never_reaches_the_log(journal, monkeypatch, caplog):
    """`httpx` loguea la URL de cada petición y la de la Bot API lleva el token."""
    token = "123456:ABC-def_GHI"
    monkeypatch.setattr(settings, "telegram_bot_token", token)

    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    monkeypatch.setattr(
        telegram, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(ok))
    )
    with caplog.at_level(logging.INFO):
        assert await telegram.send_message(CHAT, "hola") == "tg_msg_5"
    http_lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert http_lines and "sendMessage" in http_lines[0]  # httpx sí lo registró
    assert token not in caplog.text and "bot***" in caplog.text


async def test_second_update_same_chat_starts_once(client, journal, fakes, no_extract):
    await client.post(
        "/webhooks/telegram", json=telegram.fake_update(CHAT, None, None, "hola", 7)
    )
    await client.post(
        "/webhooks/telegram",
        json=telegram.fake_update(CHAT, *SOL, None, live=True, update_id=8),
    )
    assert len(_of(journal, EventType.CALL_STARTED)) == 1
    loc = _of(journal, EventType.CITIZEN_LOCATION)
    assert len(loc) == 1 and loc[0].payload["live"] is True


async def test_text_only_asks_for_the_pin(client, journal, fakes, no_extract):
    r = await client.post(
        "/webhooks/telegram", json=telegram.fake_update(CHAT, None, None, "hola", 9)
    )
    assert "ubicación" in r.json()["ack"].lower()
    assert not _of(journal, EventType.CITIZEN_LOCATION)


async def test_ignored_updates_are_ok(client, journal, fakes):
    r = await client.post(
        "/webhooks/telegram", json={"update_id": 10, "callback_query": {}}
    )
    assert r.status_code == 200 and r.json()["ignored"] is True
    assert not journal


# --- señales ---------------------------------------------------------------------------


async def test_signal_for_tg_call_is_sent_and_journaled(
    client, journal, fakes, sent, no_extract
):
    await client.post(
        "/webhooks/telegram", json=telegram.fake_update(CHAT, *SOL, None, update_id=11)
    )
    assert sent and sent[-1]["method"] == "sendMessage" and sent[-1]["chat_id"] == CHAT

    task = asyncio.create_task(telegram.signal_dispatcher())
    await asyncio.sleep(0)  # que se suscriba antes de publicar
    req = bus.make_event(
        EventType.CALL_SIGNAL_REQUESTED,
        {
            "call_id": CALL_ID,
            "key": "unit_dispatched",
            "payload": {"unit": "la ambulancia", "route": "la pista norte", "eta_s": 40},
        },
        source="core",
    )
    await bus.publish(req)
    for _ in range(50):
        await asyncio.sleep(0.02)
        if _of(journal, EventType.CALL_SIGNAL_SENT):
            break
    task.cancel()
    s = _of(journal, EventType.CALL_SIGNAL_SENT)
    assert len(s) == 1
    p = s[0].payload
    assert p["call_id"] == CALL_ID and p["signal_id"] == "tg_msg_77"
    assert "la ambulancia" in p["message"] and p["refined"] is True
    assert s[0].causes == [req.seq]
    assert sent[-1]["text"] == p["message"]


async def test_signal_without_token_is_dry(journal, fakes):
    telegram.session(CALL_ID, "run_test")
    sid = await telegram.on_signal(
        CALL_ID, "unit_dispatched", {"unit": "camión 1", "route": "pista", "eta_s": 0}, []
    )
    assert sid and sid.startswith("tg_dry_")
    assert _of(journal, EventType.CALL_SIGNAL_SENT)[0].payload["signal_id"] == sid


async def test_voice_dispatcher_ignores_tg_sessions(journal, fakes, caplog):
    task = asyncio.create_task(humanlike.signal_dispatcher())
    await asyncio.sleep(0)
    with caplog.at_level("WARNING", logger="voice.humanlike"):
        await bus.publish(
            bus.make_event(
                EventType.CALL_SIGNAL_REQUESTED,
                {"call_id": CALL_ID, "key": "unit_dispatched", "payload": {}},
                source="core",
            )
        )
        await asyncio.sleep(0.05)
    task.cancel()
    assert "desconocida" not in caplog.text


# --- el ack del tool ----------------------------------------------------------------------


@pytest.fixture
def tool_pois():
    from contracts.world import POI, RoadEdge

    edge = "wp_sur_03-wp_sur_04"
    pois.set_scenario(
        [POI.model_validate(POIS[0])],
        [RoadEdge(id=edge, a="wp_sur_03", b="wp_sur_04", length_m=100)],
        road_aliases={"pista del sur": edge},
        geo=GEO,
    )
    humanlike._seen_calls.clear()


async def test_tool_ack_points_to_telegram_when_unlocated(
    client, journal, fakes, tool_pois, monkeypatch
):
    monkeypatch.setattr(settings, "telegram_bot_username", "vela_112_bot")
    body = {
        "session_id": "sess_tg",
        "run_id": "run_test",
        "params": {
            "location_hint": "cerca de unas casas al final de la pista, no sé el nombre",
            "road_blocked": "la pista del sur",
            "urgency": "critical",
        },
    }
    r = await client.post("/webhooks/happyrobot/fact", json=body)
    assert r.status_code == 200, r.text
    ack = r.json()
    assert ack["resolved_poi_name"] is None
    assert ack["telegram_hint"] is True and ack["telegram_bot"] == "vela_112_bot"
    assert "@vela_112_bot" in ack["message"] and "usted" not in ack["message"].lower()
    assert "mande su ubicación" in ack["message"]
    # la frase pasó por Humalike (FakeHumalike devuelve el borrador tal cual)
    hl, _ = fakes
    drafts = [c[1]["draft"] for c in hl.calls if c[0] == "foresee"]
    assert any("@vela_112_bot" in d for d in drafts)
    # y la pista cortada sigue entrando aunque no haya ubicación
    keys = {e.payload["key"] for e in _of(journal, EventType.WORLD_FACT_ASSERTED)}
    assert "road:wp_sur_03-wp_sur_04:cut" in keys


async def test_tool_ack_has_no_hint_when_located_or_without_bot(
    client, journal, fakes, tool_pois, monkeypatch
):
    monkeypatch.setattr(settings, "telegram_bot_username", "vela_112_bot")
    body = {
        "session_id": "sess_ok",
        "run_id": "run_test",
        "params": {"location_hint": "Pueblo B", "people_immobile": "1"},
    }
    ack = (await client.post("/webhooks/happyrobot/fact", json=body)).json()
    assert ack["telegram_hint"] is False and "@" not in ack["message"]

    monkeypatch.setattr(settings, "telegram_bot_username", "")
    body = {
        "session_id": "sess_nobot",
        "run_id": "run_test",
        "params": {"location_hint": "no sé dónde", "road_blocked": "la pista del sur"},
    }
    ack = (await client.post("/webhooks/happyrobot/fact", json=body)).json()
    assert ack["telegram_hint"] is False and ack["telegram_bot"] is None
    assert "Telegram" not in ack["message"]


async def test_pin_hangs_from_the_voice_call_and_places_its_pending_facts(
    client, journal, fakes, tool_pois, no_extract, monkeypatch
):
    """La voz da el *qué* (dos que no pueden andar) sin *dónde*; el pin da el dónde.
    El `citizen.location` apunta al `call.started` de la llamada de voz y el recuento
    del tool entra al estado con el POI del pin, no el que el texto deja inferir."""
    monkeypatch.setattr(settings, "telegram_bot_username", "vela_112_bot")
    body = {
        "session_id": "v2_in",
        "run_id": "run_test",
        "params": {
            "location_hint": "cerca de unas casas al final de la pista, no sé el nombre",
            "people_immobile": 2,
            "urgency": "critical",
        },
    }
    r = await client.post("/webhooks/happyrobot/fact", json=body)
    assert r.status_code == 200 and r.json()["telegram_hint"] is True
    started = _of(journal, EventType.CALL_STARTED)
    assert len(started) == 1  # el tool llegó sin webhook de inicio: la llamada existe
    assert started[0].payload["call_id"] == "v2_in"
    assert started[0].payload["direction"] == "inbound"
    assert not any(
        "immobile" in e.payload["key"]
        for e in _of(journal, EventType.WORLD_FACT_ASSERTED)
    )  # sin ubicar no entra al estado…
    assert humanlike.MONITORS["v2_in"].state.pending_facts == {
        "immobile": 2
    }  # …pero no se pierde

    upd = telegram.fake_update(
        CHAT, *SOL, "Le mando la ubicación, mi madre no puede andar", update_id=70
    )
    r = await client.post("/webhooks/telegram", json=upd)
    assert r.status_code == 200, r.text
    assert r.json()["voice_call_id"] == "v2_in" and r.json()["facts_from_call"] == 1
    loc = _of(journal, EventType.CITIZEN_LOCATION)[0]
    assert started[0].seq in loc.causes
    imm = [
        e
        for e in _of(journal, EventType.WORLD_FACT_ASSERTED)
        if e.payload["key"] == "poi:poi_pueblo_b:immobile"
    ]
    assert len(imm) == 1  # el del tool; el texto («mi madre») no lo duplica
    assert imm[0].payload["value"] == 2 and imm[0].payload["kind"] == "inferred"
    assert imm[0].source == f"call:{CALL_ID}" and imm[0].causes == [loc.seq]
    assert imm[0].payload["severity"] == "critical"
    assert humanlike.MONITORS["v2_in"].state.pending_facts == {}
    # y el POI confirmado, observado, sigue ahí
    conf = next(
        e
        for e in _of(journal, EventType.WORLD_FACT_ASSERTED)
        if e.payload["key"] == "poi:poi_pueblo_b:confirmed"
    )
    assert conf.payload["kind"] == "observed"


async def test_pin_after_hangup_still_hangs_from_the_recent_call(
    client, journal, fakes, tool_pois, no_extract
):
    body = {
        "session_id": "v3_in",
        "run_id": "run_test",
        "params": {"location_hint": "no sé", "people_immobile": 1, "urgency": "critical"},
    }
    await client.post("/webhooks/happyrobot/fact", json=body)
    started = _of(journal, EventType.CALL_STARTED)[0]
    end = {
        "type": "end",
        "session_id": "v3_in",
        "status": "completed",
        "direction": "inbound",
    }
    assert (await client.post("/webhooks/happyrobot/call", json=end)).status_code == 200
    assert "v3_in" not in humanlike.MONITORS and "v3_in" in humanlike.RECENT_ENDED
    assert telegram.recent_voice_call().call_id == "v3_in"

    await client.post(
        "/webhooks/telegram", json=telegram.fake_update(CHAT, *SOL, None, update_id=80)
    )
    loc = _of(journal, EventType.CITIZEN_LOCATION)[0]
    assert started.seq in loc.causes
    keys = {
        e.payload["key"]: e.payload for e in _of(journal, EventType.WORLD_FACT_ASSERTED)
    }
    assert keys["poi:poi_pueblo_b:immobile"]["value"] == 1

    # colgada hace más de cinco minutos: el pin va solo
    humanlike.RECENT_ENDED["v3_in"].ended_t -= telegram.RECENT_CALL_S + 1
    assert telegram.recent_voice_call() is None


def test_bot_mention_survives_refinement():
    from voice.webhooks import ensure_bot_mentioned

    out = ensure_bot_mentioned("Tranquilo, ya vamos.", "vela_112_bot", True)
    assert "@vela_112_bot" in out
    assert ensure_bot_mentioned("Tranquilo.", "vela_112_bot", False) == "Tranquilo."


def test_keyword_facts_are_conservative():
    cf = telegram.keyword_facts("mi madre no puede andar")
    assert cf and cf.people_immobile == 1 and cf.urgency == "critical"
    cf = telegram.keyword_facts("hay tres personas que no pueden moverse y un herido")
    assert cf and cf.people_immobile == 3 and cf.injuries == 1
    assert telegram.keyword_facts("estamos bien, gracias") is None
