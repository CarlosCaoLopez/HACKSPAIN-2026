"""La /demo autoservicio: teléfonos por petición, el candado de un run, el auto-stop. P4.

Lo que protege:

- **Los teléfonos del formulario mandan mientras dura el run y solo mientras dura.**
  `core.loop` lee `settings.phone_*`; `start_run` los pisa con `apply_phones` y
  `stop_run` devuelve los del `.env`. Un segundo run sin `phones` no puede llamar al
  móvil del visitante anterior.
- **Un teléfono que no es E.164 es 422**, antes de tocar nada: HappyRobot marca lo que
  le den y un `6xx` a secas acaba donde quiera la plataforma.
- **Dos demos a la vez es 409 con `retry_after_s`**: Paper solo aguanta un mundo, y la
  landing tiene que poder decir «vuelve en N minutos».
- **El run se para solo** a los `VELA_RUN_MAX_S` segundos, con `run.ended` en el journal.
- **`/api/demo/status` nunca devuelve teléfonos.**
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import pytest

import voice
from contracts import bus
from contracts.events import Event, EventType
from contracts.settings import PHONE_KEYS, settings
from core import planner
from gateway import main as gateway_main
from gateway.main import app

SCENARIO = "wildfire_ridge"
WAIT_S = 12.0

PHONES = {
    "pueblo_a": "+34 600 000 001",
    "pueblo_b": "+34600000002",
    "fire_crew": "+34600000003",
    "ambulance": "+34600000003",  # el mismo que el retén: permitido
    "neighbor": "+34600000004",
}
DEFAULTS = {k: f"+3491100000{i}" for i, k in enumerate(PHONE_KEYS)}


@pytest.fixture
async def gateway(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("VELA_MODE", "dev")
    monkeypatch.setattr(settings, "vela_mode", "dev")
    monkeypatch.setattr(settings, "vela_bridges", True)
    monkeypatch.setattr(settings, "vela_cors_origins", "")
    monkeypatch.setattr(settings, "vela_cam_player", "")
    monkeypatch.setattr(settings, "vela_run_max_s", 0)
    monkeypatch.setattr(settings, "happyrobot_inbound_number", "+14845581911")
    for key in (
        "openai_api_key",
        "anthropic_api_key",
        "happyrobot_api_key",
        "happyrobot_hook_evacuation",
        "humalike_api_key",
        "typesafe_api_key",
    ):
        monkeypatch.setattr(settings, key, "")
    for key, value in DEFAULTS.items():
        monkeypatch.setattr(settings, f"phone_{key}", value)

    async def _sin_red(prompt: str):
        return planner.neutral_policy()

    monkeypatch.setattr(planner, "_call", _sin_red)
    monkeypatch.setattr(voice, "warmup", lambda: None)
    monkeypatch.setattr(gateway_main, "_voice_warmed", False)
    monkeypatch.setattr(gateway_main, "RUNS_DIR", tmp_path / "runs")

    bus.reset()
    async with gateway_main.lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://vela") as c:
            yield c, app.state.runtime
    bus.reset()


def _events(path: Path) -> list[Event]:
    return [
        Event.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def _wait_ended(path: Path) -> list[Event]:
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        if path.exists():
            evs = _events(path)
            if evs and evs[-1].type == EventType.RUN_ENDED:
                return evs
        await asyncio.sleep(0.05)
    raise AssertionError("el run no se paró solo")


async def _wait_idle(rt) -> None:
    """Espera a que el `Runtime` quede libre después del `run.ended`.

    No es lo mismo que `_wait_ended`: la invariante 3 dice que el evento se escribe
    al journal **antes** de repartirse, así que entre ver `run.ended` en el fichero y
    ver el proceso recogido hay una ventana real. Mirar `rt.run_id` justo al salir de
    `_wait_ended` es una carrera, y se pierde cuando la máquina va cargada.
    """
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        if rt.run_id is None and rt.journal_path is None:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"el auto-stop no recogió el proceso: run_id={rt.run_id}")


def _run(**extra) -> dict:
    return {"scenario_id": SCENARIO, "minecraft": False, "speed": 20.0, **extra}


async def test_los_telefonos_del_run_mandan_y_se_devuelven(gateway) -> None:
    client, rt = gateway
    res = await client.post("/api/run", json=_run(phones=PHONES))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["phones"] == "por petición"
    assert body["inbound_number"] == "+14845581911"
    assert body["watch_url"] == "/"

    # Normalizados y aplicados: lo que lee `core.loop` es lo del formulario.
    assert settings.phone_pueblo_a == "+34600000001"
    assert settings.phone_for_poi("poi_pueblo_a") == "+34600000001"
    assert settings.phone_fire_crew == settings.phone_ambulance == "+34600000003"
    assert settings.phone_neighbor == "+34600000004"
    assert rt.phones_given is True

    health = (await client.get("/api/health")).json()
    assert health["phones"] == "por petición"

    await client.post("/api/run/stop")
    # Y al parar, los del `.env` de vuelta, los cinco.
    assert settings.phones() == DEFAULTS
    assert rt.phones_given is False and rt.phones_prev == {}


async def test_sin_phones_mandan_los_del_env(gateway) -> None:
    client, rt = gateway
    res = await client.post("/api/run", json=_run())
    assert res.status_code == 200, res.text
    assert res.json()["phones"] == "del .env"
    assert settings.phones() == DEFAULTS
    assert rt.phones_given is False
    await client.post("/api/run/stop")


@pytest.mark.parametrize(
    "phones",
    [
        {"pueblo_a": "600000001"},  # sin prefijo internacional
        {"pueblo_a": "+34 6"},  # demasiado corto
        {"alcalde": "+34600000001"},  # papel que no existe
    ],
)
async def test_un_telefono_malo_es_422_y_no_arranca_nada(gateway, phones: dict) -> None:
    client, rt = gateway
    res = await client.post("/api/run", json=_run(phones=phones))
    assert res.status_code == 422, res.text
    assert rt.run_id is None
    assert settings.phones() == DEFAULTS


async def test_dos_demos_a_la_vez_es_409_con_espera(gateway, monkeypatch) -> None:
    client, rt = gateway
    monkeypatch.setattr(settings, "vela_run_max_s", 300)
    first = await client.post("/api/run", json=_run(phones=PHONES))
    assert first.status_code == 200, first.text

    second = await client.post("/api/run", json=_run(phones=PHONES))
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["reason"] == "busy"
    assert detail["run_id"] == first.json()["run_id"]
    assert 0 < detail["retry_after_s"] <= 300

    status = (await client.get("/api/demo/status")).json()
    assert status["busy"] is True
    assert status["ends_in_s"] == pytest.approx(detail["retry_after_s"], abs=2)
    assert [r["key"] for r in status["phone_roles"]] == list(PHONE_KEYS)
    # Ningún teléfono en ningún sitio de la respuesta.
    assert "+34600000001" not in second.text and "+34600000001" not in str(status)

    await client.post("/api/run/stop")
    assert rt.run_id is None
    assert (await client.get("/api/demo/status")).json()["busy"] is False


async def test_el_run_se_para_solo(gateway, monkeypatch) -> None:
    client, rt = gateway
    monkeypatch.setattr(settings, "vela_run_max_s", 1)
    res = await client.post("/api/run", json=_run(phones=PHONES))
    run_id = res.json()["run_id"]
    journal = gateway_main.RUNS_DIR / f"{run_id}.jsonl"

    evs = await _wait_ended(journal)
    assert evs[0].type == EventType.RUN_STARTED
    # Lo que se comprueba es que llega a recogerse, no que sea instantáneo.
    await _wait_idle(rt)
    assert settings.phones() == DEFAULTS
    assert (await client.get("/api/demo/status")).json()["busy"] is False
