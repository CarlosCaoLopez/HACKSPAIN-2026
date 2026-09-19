"""Las fuentes reales dentro del gateway (SPEC-007, F4 · REQ-233…236, 260, 261). P4.

Lo que se protege, en orden de lo que más duele si falla el día de la demo:

- **`VELA_FEEDS=off` es exactamente la demo de hoy** (criterio 2): ni tarea, ni socket, ni
  nota. Todo lo nuevo es opt-in y esta es la prueba de que lo es.
- **Sin red, el run sigue** (criterio 8): las tres fuentes se degradan y se anota, pero
  nada se cae.
- **En replay las fuentes no arrancan** y aun así el ancla se sirve (REQ-234, REQ-265): los
  hechos reales llegan por el journal y la pantalla tiene que decir de qué sitio son.
"""

import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from contracts.settings import settings
from gateway.feeds import dgt, firms, open_meteo
from gateway.feeds.anchor import EdgeRef, GeoAnchor
from gateway.main import app

FAKE = Path("fixtures/run_fake.jsonl")
DGT = Path("fixtures/feeds/_test/dgt_sample.xml").read_bytes()
SOURCES = {"open_meteo", "dgt", "firms"}


def fixed_anchor(**over) -> GeoAnchor:
    base = {
        "id": "wildfire_ridge",
        "place": "Sitio de prueba",
        "lat0": 40.0,
        "lon0": -4.0,
        "meters_per_block": 25,
        "edges": {"road:wp_a-wp_b": EdgeRef(road_name="A-8005", pk_from=1.0, pk_to=2.5)},
    }
    return GeoAnchor(**{**base, **over})


def wait_for(condition, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(settings, "vela_mode", "dev")
    monkeypatch.setattr(settings, "vela_feeds", "off")
    # Las capturas de `live` van a un tmp: nunca a `fixtures/feeds/` dentro del repo.
    monkeypatch.setattr(settings, "vela_feeds_dir", str(tmp_path / "capturas"))
    with TestClient(app) as c:
        yield c


def start_run(c: TestClient, scenario: str = "wildfire_ridge") -> None:
    assert c.post("/api/run", json={"scenario_id": scenario}).status_code == 200


def rt_of(c: TestClient):
    return c.app.state.runtime


# --- off: exactamente la demo de hoy --------------------------------------------------------


def test_off_no_arranca_ninguna_tarea_ni_deja_notas(client: TestClient) -> None:
    start_run(client)
    rt = rt_of(client)
    assert "feeds" not in rt.tasks and rt.feeds is None
    assert "feeds" not in rt.components and "feeds" not in rt.notes
    client.post("/api/run/stop")


def test_off_api_feeds_lo_dice_y_health_solo_anade_el_modo(client: TestClient) -> None:
    body = client.get("/api/feeds").json()
    assert body["mode"] == "off" and body["anchor"] is None and body["detections"] == []
    assert {s["status"] for s in body["sources"].values()} == {"off"}
    assert set(body["sources"]) == SOURCES
    assert client.get("/api/health").json()["feeds"] == "off"


# --- sin ancla: se anota, no se cae ----------------------------------------------------------


def test_un_escenario_sin_ancla_se_anota_y_el_run_sigue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "vela_feeds", "live")
    start_run(client, "blackout_grid")  # no tiene ancla
    health = client.get("/api/health").json()
    assert health["run_id"], "el run no puede caerse por no tener ancla"
    assert health["components"]["feeds"] == "absent"
    assert "sin ancla" in health["notes"]["feeds"]
    assert "feeds" not in rt_of(client).tasks
    client.post("/api/run/stop")


# --- en vivo, sin red -----------------------------------------------------------------------


def test_sin_red_las_cuatro_fuentes_se_degradan_y_el_run_sigue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args, **kwargs):
        raise httpx.ConnectError("sin red")

    for module, name in [(open_meteo, "fetch_current"), (dgt, "fetch"), (firms, "fetch")]:
        monkeypatch.setattr(module, name, boom)
    monkeypatch.setattr("gateway.main.load_anchor", lambda _id: fixed_anchor())
    monkeypatch.setattr(settings, "vela_feeds", "live")
    monkeypatch.setattr(settings, "firms_map_key", "clave-de-prueba")

    start_run(client)
    assert wait_for(
        lambda: (
            {s["status"] for s in client.get("/api/feeds").json()["sources"].values()}
            == {"degraded"}
        )
    ), client.get("/api/feeds").json()

    body = client.get("/api/feeds").json()
    assert all("sin red" in s["last_error"] for s in body["sources"].values())
    assert all(
        s["next_poll_s"] is not None for s in body["sources"].values()
    )  # reintentan
    health = client.get("/api/health").json()
    assert (
        health["run_id"]
        and health["feeds"] == "live"
        and health["components"]["feeds"] == "up"
    )

    stop = client.post("/api/run/stop").json()
    assert stop["stopped"] is True
    assert "feeds" not in rt_of(client).tasks, "parar el run tiene que parar las fuentes"


# --- recorded: los hechos salen por el mismo camino --------------------------------------------


def test_recorded_publica_los_hechos_firmados_como_feeds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folder = tmp_path / "wildfire_ridge" / "dgt"
    folder.mkdir(parents=True)
    (folder / "2026-09-19T100000Z.xml").write_bytes(DGT)
    monkeypatch.setattr("gateway.main.load_anchor", lambda _id: fixed_anchor())
    monkeypatch.setattr(settings, "vela_feeds", "recorded")
    monkeypatch.setattr(settings, "vela_feeds_dir", str(tmp_path))

    seen = rt_of(client).hub.register()  # antes de arrancar: lo que se publique le llega
    start_run(client)
    assert wait_for(
        lambda: client.get("/api/feeds").json()["sources"]["dgt"]["published"] == 1
    )

    facts = []
    while not seen.queue.empty():
        ev = seen.queue.get_nowait()
        if ev.type == "world.fact.asserted":
            facts.append(ev)
    assert {f.payload["key"] for f in facts} == {
        "road:wp_a-wp_b:cut"
    }  # un solo hecho: sin causa
    assert {f.source for f in facts} == {"feeds"}  # la envoltura (REQ-236)
    assert all(f.payload["source"].startswith("api:dgt:") for f in facts)
    assert all(f.payload["kind"] == "observed" for f in facts)

    body = client.get("/api/feeds").json()
    assert body["mode"] == "recorded"
    assert (
        body["anchor"]["place"] == "Sitio de prueba" and body["anchor"]["fixed"] is True
    )
    assert isinstance(body["anchor"]["lat0"], float) and isinstance(
        body["anchor"]["lon0"], float
    )
    assert body["sources"]["firms"] == {
        **body["sources"]["firms"],
        "status": "off",
        "note": "sin capturas",
    }
    client.post("/api/run/stop")


# --- replay: no arrancan, pero el ancla se sirve ------------------------------------------------


@pytest.fixture
def replay_client(monkeypatch: pytest.MonkeyPatch):
    if not FAKE.exists():
        pytest.skip(
            "falta fixtures/run_fake.jsonl · uv run python scripts/fake_journal.py"
        )
    monkeypatch.setattr(settings, "vela_mode", "replay")
    monkeypatch.setattr(settings, "vela_replay_file", str(FAKE))
    monkeypatch.setattr(settings, "vela_replay_speed", 60.0)
    monkeypatch.setattr(settings, "vela_replay_loop", True)
    monkeypatch.setattr(settings, "vela_feeds", "live")  # da igual: en replay no arrancan
    monkeypatch.setattr("gateway.main.load_anchor", lambda _id: fixed_anchor())
    with TestClient(app) as c:
        yield c


def test_en_replay_no_arrancan_fuentes_pero_el_ancla_se_sirve(
    replay_client: TestClient,
) -> None:
    assert "feeds" not in rt_of(replay_client).tasks
    body = replay_client.get("/api/feeds").json()
    assert body["mode"] == "replay"
    assert body["anchor"]["place"] == "Sitio de prueba"
    assert {s["status"] for s in body["sources"].values()} == {"off"}
