"""El hub, el WS y los endpoints del gateway. P4.

Corre con `sim`, `core`, `voice` y el bus **sin cuerpo**: eso no es una limitación
del test, es el requisito. El gateway tiene que arrancar degradado y servir el
dashboard igual, porque hasta el sábado media arquitectura está en
`NotImplementedError`.

Lo que se prueba aquí y por qué:

- **El snapshot es atómico**: el primer `event` que ve un cliente es siempre
  `snapshot.seq + 1`. Registrar la cola después de capturar el estado pierde los
  eventos de en medio; capturar sin descartar los `seq <=` los duplica. Es el bug
  clásico de este patrón y no se ve hasta que el mapa miente en la demo.
- **Backpressure cierra al cliente lento, no descarta eventos** — y no toca a los
  demás clientes.
- **Los 409/404 de `POST /api/run`** y que `stop` sea idempotente: entre ensayo y
  ensayo del H5 no hay tiempo de reiniciar uvicorn.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contracts.events import Event, EventType, WorldTick
from contracts.settings import settings
from contracts.world import Wind
from gateway.hub import CLOSE_BACKPRESSURE, Hub
from gateway.main import app

FAKE = Path("fixtures/run_fake.jsonl")


def _ev(seq: int, run_id: str = "run_test") -> Event:
    return Event(
        run_id=run_id,
        seq=seq,
        t_wall=datetime.now(UTC),
        t_sim=float(seq),
        type=EventType.WORLD_TICK,
        source="sim",
        payload=WorldTick(
            t_sim=float(seq), wind=Wind(bearing_deg=270, speed=1.2)
        ).model_dump(mode="json"),
    )


# --- El hub, sin servidor --------------------------------------------------------


async def test_hub_reparte_a_todos() -> None:
    hub = Hub()
    a, b = hub.register(), hub.register()
    hub.dispatch(_ev(1))
    hub.dispatch(_ev(2))
    assert [a.queue.get_nowait().seq for _ in range(2)] == [1, 2]
    assert b.queue.qsize() == 2
    assert hub.last_seq == 2
    assert hub.run_id == "run_test"


async def test_backpressure_cierra_al_lento_y_no_a_los_demas() -> None:
    """Un cliente atascado se cierra con 4002; el evento NO se descarta para el
    resto. Preferimos que el lento rehaga `GET /api/state` a mentirle."""
    hub = Hub(maxsize=2)
    lento, rapido = hub.register(), hub.register()

    for seq in (1, 2, 3):
        hub.dispatch(_ev(seq))
        # El rápido drena a cada evento, como hace el bucle del WS.
        rapido.queue.get_nowait()

    assert lento.closing
    assert lento.close_code == CLOSE_BACKPRESSURE
    assert lento.close_reason == "backpressure"
    assert not rapido.closing
    assert hub.stats()["dropped_clients"] == 1


async def test_cliente_cerrado_deja_de_recibir() -> None:
    hub = Hub()
    c = hub.register()
    hub.dispatch(_ev(1))
    hub.unregister(c)
    hub.dispatch(_ev(2))
    assert c.queue.qsize() == 1  # el segundo ya no era suyo


# --- El WS de verdad, en modo replay ---------------------------------------------


@pytest.fixture
def replay_client(monkeypatch: pytest.MonkeyPatch):
    """El gateway en `VELA_MODE=replay` sobre el journal falso, a 60×.

    Es el camino real de `make dev-dash`: sin sim, sin core y sin voz.
    """
    if not FAKE.exists():
        pytest.skip(
            "falta fixtures/run_fake.jsonl · uv run python scripts/fake_journal.py"
        )
    monkeypatch.setattr(settings, "vela_mode", "replay")
    monkeypatch.setattr(settings, "vela_replay_file", str(FAKE))
    monkeypatch.setattr(settings, "vela_replay_speed", 60.0)
    monkeypatch.setattr(settings, "vela_replay_loop", True)
    with TestClient(app) as client:
        yield client


def test_snapshot_y_luego_el_chorro(replay_client: TestClient) -> None:
    with replay_client.websocket_connect("/ws") as ws:
        snap = ws.receive_json()
        assert snap["kind"] == "snapshot"
        assert set(snap) == {"kind", "state", "plan", "seq"}

        first = ws.receive_json()
        assert first["kind"] == "event"
        # EL REQUISITO: ni hueco ni duplicado entre el snapshot y el primer evento.
        assert first["event"]["seq"] == snap["seq"] + 1

        last = first["event"]["seq"]
        for _ in range(20):
            frame = ws.receive_json()
            assert frame["kind"] == "event"
            assert frame["event"]["seq"] == last + 1, "hueco en el chorro"
            last = frame["event"]["seq"]


def test_el_sobre_viaja_entero(replay_client: TestClient) -> None:
    """`causes` incluida: es la materia prima de la cadena que dibuja el H3."""
    with replay_client.websocket_connect("/ws") as ws:
        ws.receive_json()  # snapshot
        ev = ws.receive_json()["event"]
        assert set(ev) == {
            "run_id",
            "seq",
            "t_wall",
            "t_sim",
            "type",
            "source",
            "payload",
            "causes",
        }


def test_state_tiene_la_forma_del_snapshot(replay_client: TestClient) -> None:
    """`GET /api/state` y el primer frame del WS son la misma forma: el cliente
    tiene un solo parser para arranque, hueco y reconexión."""
    body = replay_client.get("/api/state").json()
    assert body["kind"] == "snapshot"
    assert set(body) == {"kind", "state", "plan", "seq"}


def test_en_replay_no_se_arrancan_runs(replay_client: TestClient) -> None:
    res = replay_client.post("/api/run", json={"scenario_id": "wildfire_ridge"})
    assert res.status_code == 409


def test_replay_no_escribe_journal(replay_client: TestClient) -> None:
    """Reproducir un run no puede generar otro run: `runs/` es el dataset del
    criterio de aprendizaje y `GET /api/runs` mentiría."""
    health = replay_client.get("/api/health").json()
    assert health["mode"] == "replay"
    assert health["run_id"] == "run_fake_0001"  # el del fichero, no uno nuevo
    assert not list(Path("runs").glob("run_fake_0001.jsonl"))


# --- Los endpoints, con todo lo demás ausente ------------------------------------


@pytest.fixture
def dev_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "vela_mode", "dev")
    with TestClient(app) as client:
        yield client


def test_arranque_degradado(dev_client: TestClient) -> None:
    """Con sim, core, voice y el bus sin cuerpo, el proceso sigue en pie."""
    health = dev_client.get("/api/health").json()
    assert health["mode"] == "dev"
    assert health["run_id"] is None
    assert {"bus", "journal", "sim", "core", "voice", "replay"} <= set(
        health["components"]
    )
    assert dev_client.get("/api/state").json()["kind"] == "snapshot"
    assert dev_client.get("/api/plan").json() is None


def test_escenarios(dev_client: TestClient) -> None:
    ids = dev_client.get("/api/scenarios").json()
    assert "wildfire_ridge" in ids and "blackout_grid" in ids


def test_run_404_409_y_stop_idempotente(dev_client: TestClient) -> None:
    assert (
        dev_client.post("/api/run", json={"scenario_id": "no_existe"}).status_code == 404
    )

    first = dev_client.post("/api/run", json={"scenario_id": "wildfire_ridge"})
    assert first.status_code == 200
    assert first.json()["run_id"]

    again = dev_client.post("/api/run", json={"scenario_id": "wildfire_ridge"})
    assert again.status_code == 409  # un run a la vez

    stop = dev_client.post("/api/run/stop").json()
    assert stop["stopped"] is True
    # Dos veces stop no es un error: el H5 para y arranca seis veces seguidas sin
    # reiniciar uvicorn.
    assert dev_client.post("/api/run/stop").json() == {
        "run_id": None,
        "stopped": False,
        "journal": None,
    }


def test_run_body_tipado(dev_client: TestClient) -> None:
    assert dev_client.post("/api/run", json={}).status_code == 422


def test_runs_no_rompe_con_journals_a_medias(dev_client: TestClient) -> None:
    runs = dev_client.get("/api/runs").json()
    assert isinstance(runs, list)
    assert all("run_id" in r and "partial" in r for r in runs)
