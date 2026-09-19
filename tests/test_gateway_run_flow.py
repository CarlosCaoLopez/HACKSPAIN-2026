"""El run de punta a punta, sin Minecraft y sin red. P4.

Es el test de `make demo FLAGS="--no-minecraft"`: el gateway arranca en `VELA_MODE=dev`,
`POST /api/run` construye sim y core de verdad contra `NullRcon`, y lo que se comprueba
es **que fluye**, que era lo que no pasaba:

- **El bus se configura antes que nadie.** `Sim` y `Core` leen `bus.current_run_id()` en
  su `__init__`, y `sim._publish` guarda en una reserva todo lo que emite sin run. Sin
  `bus.configure` primero, el journal no existía y el dashboard se quedaba en blanco
  sin que ningún componente marcara error.
- **El journal `runs/<run_id>.jsonl` se escribe** con `run.started`, los `world.tick` y,
  al parar, `run.ended` en la última línea.
- **`GET /api/state` refleja el mundo** (`t_sim > 0`): el core está aplicando lo que
  publica el sim.
- **Los puentes rutean**: `action.requested` → `Sim.execute` (y el sim contesta por el
  bus) y `call.requested` → `VoiceGateway.place_call`. El core no llega a emitir órdenes
  solo —`belief` arranca con `tasks={}` y nadie las crea—, así que se publican desde
  aquí, que es lo que haría el core.

Sin red: claves vacías, el planner devuelve pesos neutros sin tocar OpenAI, y `warmup`
de `voice` se sustituye por un espía (levanta un hilo que importa fenic y aquí solo
importa que se llame). Se usa `httpx.ASGITransport` en el mismo bucle que la app para
poder esperar ticks con `asyncio.sleep` y leer el bus sin cruzar hilos.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

import voice
from contracts import bus
from contracts.calls import CallRequest
from contracts.events import ActionRequested, Event, EventType
from contracts.settings import settings
from core import planner
from gateway import main as gateway_main
from gateway.main import app

SCENARIO = "wildfire_ridge"
SPEED = 20.0
"""Un tick simulado cada 50 ms de pared: tres ticks en menos de un segundo."""
TICKS = 3
WAIT_S = 6.0

SECRETS = (
    "openai_api_key",
    "anthropic_api_key",
    "happyrobot_api_key",
    "happyrobot_hook_evacuation",
    "humalike_api_key",
    "typesafe_api_key",
)


class _SpyVoice:
    """Se queda con las `CallRequest` que le llegan por el puente. `place_call` real
    dispararía el hook de HappyRobot."""

    def __init__(self) -> None:
        self.requests: list[CallRequest] = []

    async def place_call(self, req: CallRequest) -> str:
        self.requests.append(req)
        return f"call_spy_{len(self.requests)}"


@pytest.fixture
async def gateway(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv(
        "VELA_MODE", "dev"
    )  # el bus, en dev, lanza si un payload no valida
    monkeypatch.setattr(settings, "vela_mode", "dev")
    monkeypatch.setattr(settings, "vela_bridges", True)
    for key in SECRETS:
        monkeypatch.setattr(settings, key, "")
    for env in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "HAPPYROBOT_API_KEY",
        "HUMALIKE_API_KEY",
    ):
        monkeypatch.delenv(env, raising=False)

    async def _sin_red(prompt: str):
        return planner.neutral_policy()

    monkeypatch.setattr(planner, "_call", _sin_red)

    warmups: list[bool] = []
    monkeypatch.setattr(voice, "warmup", lambda: warmups.append(True))
    monkeypatch.setattr(gateway_main, "_voice_warmed", False)
    monkeypatch.setattr(gateway_main, "RUNS_DIR", tmp_path / "runs")

    bus.reset()
    async with gateway_main.lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://vela"
        ) as client:
            yield client, app.state.runtime, warmups
    bus.reset()


def _journal(run_id: str) -> Path:
    return gateway_main.RUNS_DIR / f"{run_id}.jsonl"


def _events(path: Path) -> list[Event]:
    return [
        Event.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def _wait_for(path: Path, type_: EventType, n: int = 1) -> list[Event]:
    """Espera a que el journal tenga `n` eventos de ese tipo, o falla con lo que hay."""
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        if path.exists():
            evs = _events(path)
            if sum(1 for e in evs if e.type == type_) >= n:
                return evs
        await asyncio.sleep(0.05)
    tipos = [e.type.value for e in _events(path)] if path.exists() else "sin journal"
    raise AssertionError(
        f"no llegaron {n} × {type_.value} en {WAIT_S}s · journal: {tipos}"
    )


async def _publish(type_: EventType, payload: dict[str, Any]) -> Event:
    ev = bus.make_event(type_, payload, source="core")
    await bus.publish(ev)
    return ev


async def test_el_run_fluye_de_sim_a_core_y_al_dashboard(gateway) -> None:
    client, rt, warmups = gateway

    res = await client.post(
        "/api/run", json={"scenario_id": SCENARIO, "minecraft": False, "speed": SPEED}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    run_id = body["run_id"]
    assert body["minecraft"] is False
    assert body["speed"].startswith(f"{SPEED}×"), body["speed"]

    # El bus es del run, y el journal lo abre él.
    assert bus.current_run_id() == run_id
    journal = _journal(run_id)
    evs = await _wait_for(journal, EventType.WORLD_TICK, TICKS)
    assert evs[0].type == EventType.RUN_STARTED, "run.started tiene que ser lo primero"
    assert any(e.type == EventType.WORLD_FIRE_DETECTED for e in evs)
    assert all(e.run_id == run_id for e in evs)
    assert [e.seq for e in evs] == list(range(1, len(evs) + 1)), "seq sin huecos"

    # El core está aplicando lo que publica el sim: el dashboard ve tiempo.
    state = (await client.get("/api/state")).json()
    assert state["kind"] == "snapshot"
    assert state["state"] is not None, "el core no expone estado"
    assert state["state"]["t_sim"] > 0
    assert state["state"]["run_id"] == run_id

    health = (await client.get("/api/health")).json()
    assert health["run_id"] == run_id
    for comp in ("bus", "journal", "sim", "core", "voice"):
        assert health["components"][comp] == "up", (comp, health["notes"].get(comp))
    assert health["notes"]["bridges"].startswith("encendidos")
    assert re.match(r"\d+ POIs", health["notes"]["voice-pois"]), health["notes"][
        "voice-pois"
    ]
    assert warmups == [True], "voice.warmup() se llama una vez por proceso"

    # Puente 1: action.requested → Sim.execute, y el sim contesta por el bus.
    req = ActionRequested(
        action_id="act_test_goto",
        verb="goto",
        args={"unit_id": "unit_truck1", "waypoint_id": "wp_pueblo_a"},
    )
    await _publish(EventType.ACTION_REQUESTED, req.model_dump(mode="json"))
    evs = await _wait_for(journal, EventType.WORLD_UNIT_STATUS)
    # El core ya planifica solo y puede mover a otras unidades a la vez: basta con
    # que la orden del test haya puesto en marcha al camión 1.
    moving = [
        e
        for e in evs
        if e.type == EventType.WORLD_UNIT_STATUS and e.payload["unit_id"] == "unit_truck1"
    ]
    assert moving and moving[-1].payload["status"] == "moving"
    assert not any(e.type == EventType.ACTION_FAILED for e in evs), "la orden falló"
    assert rt.duplicate_actions == {}, "el puente ejecuta cada orden una vez"

    # Puente 2: call.requested → VoiceGateway.place_call.
    spy = _SpyVoice()
    rt.voice = spy
    call = CallRequest(
        task_id="task_evac_a",
        poi_id="poi_pueblo_a",
        to="+34600000000",
        audience="resident",
        intent="evacuation_order",
        urgency="critical",
    )
    await _publish(EventType.CALL_REQUESTED, call.model_dump(mode="json"))
    deadline = time.monotonic() + WAIT_S
    while not spy.requests and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert [r.task_id for r in spy.requests] == ["task_evac_a"]

    # Parar: run.ended es la última línea y el fichero del bus queda cerrado.
    stopped = (await client.post("/api/run/stop")).json()
    assert stopped["run_id"] == run_id
    assert stopped["journal"] == str(journal)
    last = _events(journal)[-1]
    assert last.type == EventType.RUN_ENDED
    assert last.payload["scenario_id"] == SCENARIO
    assert rt.run_id is None
    assert rt.journal_path is None
    assert (await client.get("/api/health")).json()["run_id"] is None


async def test_dos_runs_seguidos_no_comparten_journal(gateway) -> None:
    """Las seis pasadas del ensayo van sin reiniciar uvicorn: cada una con su id y su
    fichero, y el segundo no escribe en el del primero."""
    client, _rt, _ = gateway
    ids: list[str] = []
    for _ in range(2):
        res = await client.post(
            "/api/run", json={"scenario_id": SCENARIO, "minecraft": False, "speed": SPEED}
        )
        run_id = res.json()["run_id"]
        ids.append(run_id)
        await _wait_for(_journal(run_id), EventType.WORLD_TICK, 1)
        await client.post("/api/run/stop")

    assert ids[0] != ids[1]
    for run_id in ids:
        evs = _events(_journal(run_id))
        assert {e.run_id for e in evs} == {run_id}
        assert evs[0].type == EventType.RUN_STARTED
        assert evs[-1].type == EventType.RUN_ENDED


async def test_el_journal_es_el_de_api_runs(gateway) -> None:
    """Lo que escribe el bus lo lee `journal.score`: el run recién parado sale puntuado
    y no provisional, que es lo que compara el run 1 con el run 12."""
    client, _rt, _ = gateway
    res = await client.post(
        "/api/run", json={"scenario_id": SCENARIO, "minecraft": False, "speed": SPEED}
    )
    run_id = res.json()["run_id"]
    await _wait_for(_journal(run_id), EventType.WORLD_TICK, 1)
    await client.post("/api/run/stop")

    (row,) = [r for r in (await client.get("/api/runs")).json() if r["run_id"] == run_id]
    assert row["provisional"] is False
    assert row["incomplete"] is False
    assert row["synthetic"] is False
    assert row["score"]["scenario_id"] == SCENARIO
    # El fichero es JSONL de verdad, una línea por evento.
    for line in _journal(run_id).read_text(encoding="utf-8").splitlines():
        json.loads(line)
