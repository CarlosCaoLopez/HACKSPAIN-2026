"""`POST /control/*` y el token de los webhooks. P4.

Lo que se prueba aquí es lo que tiene lógica de verdad, no el cromo:

- **El eco de replay no toca el chorro.** Es el test que protege la decisión de
  diseño del H4: el hub numeraría el eco con `last_seq + 1`, que es el `seq` del
  siguiente evento del journal, y el dashboard lo leería como hueco, pediría
  `GET /api/state` y se quedaría sin historia en pantalla justo al pulsar el botón.
- **La pausa pausa sin dejar hueco.** Pausar es dejar de emitir, no saltarse eventos:
  un hueco dispararía la misma recuperación y el mismo parpadeo.
- **El gateway no fabrica eventos del mundo.** Sin sim, `/control/inject` responde
  503 y no aparece ningún `world.inject` inventado.
- **El token de los webhooks**, que es la única superficie que ve internet.
"""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contracts.settings import settings
from gateway.main import app

FAKE = Path("fixtures/run_fake.jsonl")

VETO = {
    "kind": "veto_assignment",
    "target": "unit_ambulance1:task_notify_b",
    "note": "el jefe de bomberos quiere la ambulancia en el refugio",
}

RESPONSE_SHAPE = {"accepted", "published", "echo", "run_id", "seq", "detail"}


@pytest.fixture
def dev_client(monkeypatch: pytest.MonkeyPatch):
    """El gateway con sim, core, voice y bus sin cuerpo: el estado normal hasta el
    sábado, y el que tiene que seguir en pie."""
    monkeypatch.setattr(settings, "vela_mode", "dev")
    with TestClient(app) as client:
        yield client


@pytest.fixture
def webhook_client(monkeypatch: pytest.MonkeyPatch):
    """Como `dev_client`, pero sin relanzar la excepción del endpoint.

    Los webhooks son de P3 y hoy son `raise NotImplementedError`: lo que se prueba
    aquí es **si la petición llega hasta él o la para el token**, no qué contesta. Con
    `raise_server_exceptions` el `TestClient` relanzaría su excepción y el test estaría
    midiendo el fichero de Hugo en vez del middleware mío. Así vale hoy (500) y seguirá
    valiendo cuando él lo implemente (200).
    """
    monkeypatch.setattr(settings, "vela_mode", "dev")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def replay_client(monkeypatch: pytest.MonkeyPatch):
    if not FAKE.exists():
        pytest.skip("falta fixtures/run_fake.jsonl · uv run python scripts/fake_journal.py")
    monkeypatch.setattr(settings, "vela_mode", "replay")
    monkeypatch.setattr(settings, "vela_replay_file", str(FAKE))
    monkeypatch.setattr(settings, "vela_replay_speed", 60.0)
    monkeypatch.setattr(settings, "vela_replay_loop", True)
    with TestClient(app) as client:
        yield client


# --- La forma única de respuesta -------------------------------------------------


def test_los_tres_endpoints_tienen_la_misma_forma(dev_client: TestClient) -> None:
    """Un solo parser en el dashboard, y un solo sitio donde mirar cuando un botón
    no hace lo que esperaba."""
    override = dev_client.post("/control/override", json=VETO)
    pause = dev_client.post("/control/pause", params={"paused": False})
    inject = dev_client.post("/control/inject", json={"inject_type": "road_cut"})

    assert set(override.json()) == RESPONSE_SHAPE
    assert set(pause.json()) == RESPONSE_SHAPE
    # El inject sin sim es un 503 con `detail`, no la forma completa: es un error HTTP,
    # y ahí el contrato es `{"detail": ...}` como en el resto del gateway.
    assert inject.status_code == 503
    assert set(inject.json()) == {"detail"}


def test_kind_invalido_es_422_y_el_proceso_sigue(dev_client: TestClient) -> None:
    """La lista de `OverrideKind` es contrato: la valida Pydantic, no un `if` mío."""
    res = dev_client.post("/control/override", json={"kind": "lo_que_sea", "target": "x"})
    assert res.status_code == 422
    assert dev_client.get("/api/health").status_code == 200


def test_target_con_forma_rara_se_acepta_y_se_avisa(dev_client: TestClient) -> None:
    """Avisar y no rechazar: un 400 en mitad del pitch por un id mal escrito es peor
    que un override que el core acabe descartando."""
    body = dev_client.post(
        "/control/override", json={"kind": "veto_assignment", "target": "unit_truck1"}
    ).json()
    assert body["accepted"] is True
    assert body["published"] is True
    assert "aviso" in body["detail"]


def test_el_target_bueno_no_lleva_aviso(dev_client: TestClient) -> None:
    assert dev_client.post("/control/override", json=VETO).json()["detail"] == ""


def test_override_publicado_trae_su_seq(dev_client: TestClient) -> None:
    """El `seq` es lo que le deja al dashboard reconocer su propia fila del chorro."""
    before = dev_client.get("/api/health").json()["last_seq"]
    body = dev_client.post("/control/override", json=VETO).json()
    assert body["published"] is True and body["echo"] is False
    assert body["seq"] == before + 1
    assert dev_client.get("/api/health").json()["last_seq"] == body["seq"]


# --- El eco de replay ------------------------------------------------------------


def test_en_replay_el_override_no_entra_en_el_chorro(replay_client: TestClient) -> None:
    """EL TEST DEL H4. Si el eco se repartiera al hub, su `seq` chocaría con el del
    siguiente evento del journal: el cliente vería un `seq` repetido, lo leería como
    hueco y se quedaría con `events: []` — la historia borrada justo al pulsar."""
    with replay_client.websocket_connect("/ws") as ws:
        snap = ws.receive_json()
        last = snap["seq"]
        for _ in range(3):
            last = ws.receive_json()["event"]["seq"]

        body = replay_client.post("/control/override", json=VETO).json()
        assert body == {
            "accepted": True,
            "published": False,
            "echo": True,
            "run_id": "run_fake_0001",
            "seq": None,
            "detail": "eco local (replay): no se publica ni se journalea",
        }

        # Y el chorro sigue exactamente donde estaba: ni un evento de más.
        for _ in range(5):
            nxt = ws.receive_json()["event"]["seq"]
            assert nxt == last + 1, "el eco se ha colado en el chorro"
            last = nxt


def test_el_eco_no_escribe_journal(replay_client: TestClient) -> None:
    replay_client.post("/control/override", json=VETO)
    assert not list(Path("runs").glob("run_fake_0001.jsonl"))


def test_en_replay_no_se_inyecta(replay_client: TestClient) -> None:
    res = replay_client.post("/control/inject", json={"inject_type": "road_cut"})
    assert res.status_code == 409


# --- La pausa --------------------------------------------------------------------


def test_pausa_idempotente_y_visible(dev_client: TestClient) -> None:
    for _ in range(2):
        assert dev_client.post("/control/pause", params={"paused": True}).json()["accepted"]
    assert dev_client.get("/api/health").json()["paused"] is True

    dev_client.post("/control/pause", params={"paused": False})
    assert dev_client.get("/api/health").json()["paused"] is False


def test_sin_sim_la_pausa_lo_dice(dev_client: TestClient) -> None:
    """Degradación explícita: `Sim` no expone `pause()` todavía (pedido a P2) y la
    respuesta lo dice en vez de fingir que el mundo se ha parado."""
    detail = dev_client.post("/control/pause", params={"paused": True}).json()["detail"]
    assert "solo se marca la bandera" in detail


def test_la_pausa_para_el_replay_sin_dejar_hueco(replay_client: TestClient) -> None:
    """Pausar es dejar de emitir. Al reanudar, el `seq` siguiente es el que tocaba:
    un hueco dispararía la recuperación del cliente y el parpadeo del dashboard."""
    with replay_client.websocket_connect("/ws") as ws:
        ws.receive_json()  # snapshot
        last = ws.receive_json()["event"]["seq"]

        replay_client.post("/control/pause", params={"paused": True})
        quieto = replay_client.get("/api/health").json()["last_seq"]
        time.sleep(0.5)  # a 60× esto serían decenas de eventos
        assert replay_client.get("/api/health").json()["last_seq"] == quieto

        replay_client.post("/control/pause", params={"paused": False})
        while (nxt := ws.receive_json()["event"]["seq"]) <= last:
            pass  # los que ya estaban en la cola del cliente antes de pausar
        assert nxt <= quieto + 1, "se han emitido eventos durante la pausa"


# --- El token de los webhooks ----------------------------------------------------


def test_sin_token_configurado_no_se_bloquea_nada(webhook_client: TestClient) -> None:
    """El viernes por la noche el token no existe: unos webhooks devolviendo 401 sin
    que nadie sepa por qué cuestan una hora a las tres de la mañana."""
    assert webhook_client.get("/api/health").json()["webhooks"] == "sin token"
    res = webhook_client.post("/webhooks/humalike/call", json={})
    assert res.status_code != 401  # llega al router de P3 (hoy, a su NotImplementedError)


def test_token_malo_es_401_y_se_cuenta(
    monkeypatch: pytest.MonkeyPatch, dev_client: TestClient
) -> None:
    monkeypatch.setattr(settings, "webhook_shared_token", "secreto")
    assert dev_client.get("/api/health").json()["webhooks"] == "activo"

    assert dev_client.post("/webhooks/humalike/call", json={}).status_code == 401
    assert dev_client.post(
        "/webhooks/humalike/call", json={}, headers={"X-Vela-Token": "otro"}
    ).status_code == 401
    assert dev_client.get("/api/health").json()["webhook_rejected"] == 2


def test_token_bueno_pasa_al_router_de_p3(
    monkeypatch: pytest.MonkeyPatch, webhook_client: TestClient
) -> None:
    monkeypatch.setattr(settings, "webhook_shared_token", "secreto")
    res = webhook_client.post(
        "/webhooks/humalike/call", json={}, headers={"X-Vela-Token": "secreto"}
    )
    assert res.status_code != 401
    assert webhook_client.get("/api/health").json()["webhook_rejected"] == 0


def test_el_token_no_afecta_a_lo_demas(
    monkeypatch: pytest.MonkeyPatch, dev_client: TestClient
) -> None:
    """Solo `/webhooks/*` va por el túnel. El control y la API son de localhost y
    `docs/interfaces.md` dice explícitamente que no llevan autenticación: meter un
    login en el camino de un botón que hay que pulsar en directo es regalarle un modo
    de fallo al pitch."""
    monkeypatch.setattr(settings, "webhook_shared_token", "secreto")
    assert dev_client.get("/api/health").status_code == 200
    assert dev_client.post("/control/override", json=VETO).status_code == 200
