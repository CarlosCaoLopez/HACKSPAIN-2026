"""Las cuatro banderas de `scripts/demo.py` y el plan B nivel 3. P4.

Lo que se prueba aquí es lo que tiene lógica de verdad:

- **`--no-minecraft` no construye un cliente RCON.** Se comprueba sobre el objeto que
  recibe `Sim`, no sobre un log: `RconClient.connect` reintenta con backoff, así que un
  cliente real contra un puerto muerto son varios segundos de bloqueo justo al arrancar
  el run. Un test que solo mirase un mensaje no distinguiría *no conecta* de *conecta y
  falla*, que es exactamente la diferencia que importa.
- **Las banderas viajan en el cuerpo del POST**, no por entorno, y `/api/health` las
  cuenta: es lo que lee la cabecera del dashboard para anunciar que la demo va degradada.
- **El guion se puede leer**: `demo.py` traduce un evento a una línea sin tocar la red.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from contracts.events import Event, EventType
from contracts.settings import settings
from gateway import main as gateway_main
from gateway.rcon_null import NullRcon

DEMO = Path("scripts/demo.py")


def _load_demo() -> Any:
    """`scripts/` no es un paquete instalable: se carga por ruta, como haría la consola."""
    spec = importlib.util.spec_from_file_location("vela_demo", DEMO)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = _load_demo()


# --- las banderas ------------------------------------------------------------------


def test_las_cuatro_banderas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "demo.py",
            "--scenario",
            "blackout_grid",
            "--mock-calls",
            "--speed",
            "4",
            "--no-minecraft",
        ],
    )
    args = demo.parse_args()
    assert args.scenario == "blackout_grid"
    assert args.mock_calls is True
    assert args.speed == 4.0
    assert args.no_minecraft is True


def test_por_defecto_todo_de_verdad(monkeypatch: pytest.MonkeyPatch) -> None:
    """La demo buena es la que no lleva banderas: nivel 1 del plan B por defecto."""
    monkeypatch.setattr("sys.argv", ["demo.py"])
    args = demo.parse_args()
    assert (args.scenario, args.mock_calls, args.speed, args.no_minecraft) == (
        demo.SCENARIO_DEFAULT,
        False,
        1.0,
        False,
    )


# --- el plan B nivel 3 --------------------------------------------------------------


class _SpySim:
    """Se queda con lo que le dan en vez de arrancar nada."""

    last_rcon: Any = None

    def __init__(self, scenario_path: Path, rcon: Any) -> None:
        _SpySim.last_rcon = rcon

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "vela_mode", "dev")
    with TestClient(gateway_main.app) as c:
        yield c


def _patch_sim(monkeypatch: pytest.MonkeyPatch) -> None:
    """`sim` no tiene cuerpo, así que se sustituye por el espía en el punto donde
    `start_run` lo importa. No se toca `packages/sim/**`."""
    _SpySim.last_rcon = None
    import sim

    monkeypatch.setattr(sim, "Sim", _SpySim, raising=False)


def test_sin_minecraft_no_se_construye_un_cliente_rcon(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_sim(monkeypatch)
    res = client.post(
        "/api/run", json={"scenario_id": "wildfire_ridge", "minecraft": False}
    )
    assert res.status_code == 200
    assert res.json()["minecraft"] is False
    assert isinstance(_SpySim.last_rcon, NullRcon), "el sim ha recibido un RCON de verdad"
    client.post("/api/run/stop")


def test_con_minecraft_el_rcon_es_el_de_p2(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El contrapunto del test anterior: sin la bandera, el cliente real. Si `RconClient`
    sigue sin cuerpo, el `guard` lo anota y `last_rcon` se queda a `None`; lo que no
    puede pasar nunca es que salga un `NullRcon` sin haberlo pedido."""
    _patch_sim(monkeypatch)
    client.post("/api/run", json={"scenario_id": "wildfire_ridge"})
    assert not isinstance(_SpySim.last_rcon, NullRcon)
    client.post("/api/run/stop")


def test_health_anuncia_los_dos_planes_b(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Es lo que lee la cabecera del dashboard. Una demo degradada se anuncia sola."""
    _patch_sim(monkeypatch)
    client.post(
        "/api/run",
        json={"scenario_id": "wildfire_ridge", "minecraft": False, "mock_calls": True},
    )
    health = client.get("/api/health").json()
    assert health["minecraft"] == "apagado"
    assert health["calls"] == "simuladas"
    client.post("/api/run/stop")


def test_la_velocidad_que_el_sim_no_acepta_se_dice(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un sim sin `set_speed()` ni atributo `speed` (el espía): se pide por pato y se
    degrada a la vista, nunca en silencio. El `Sim` real de P2 sí lleva `speed` y
    `test_gateway_run_flow.py` comprueba que se le fija."""
    _patch_sim(monkeypatch)
    client.post(
        "/api/run",
        json={"scenario_id": "wildfire_ridge", "speed": 4.0, "minecraft": False},
    )
    assert "no acepta velocidad" in client.get("/api/health").json()["notes"]["speed"]
    client.post("/api/run/stop")


async def test_el_sim_de_verdad_corre_contra_null_rcon() -> None:
    """`NullRcon` tiene que aceptar la firma del Protocol `sim.rcon.Rcon`, no solo sus
    nombres: `sim/runner.py` llama `send(cmd, LOW)` y `send_many(cmds, LOW)` con el
    carril en posicional. Con `send(self, command)` a secas, el primer `/fill` del
    worldgen reventaba con `TypeError` DENTRO de la task del sim, y `--no-minecraft`
    arrancaba un run sin mundo y sin avisar. Aquí se arranca un `Sim` real, se le da
    una orden y se le hace avanzar: worldgen, `/tp` del movimiento y teardown, los
    tres caminos por los que salen comandos."""
    from sim import runner as runner_mod
    from sim.runner import Sim

    runner_mod._FALLBACK.clear()  # sin run en el bus: los eventos van a la reserva
    rcon = NullRcon()
    sim = Sim(Path("scenarios/wildfire_ridge.yaml"), rcon)
    await sim.start()
    try:
        assert rcon.sent > 0, "el worldgen no ha mandado nada"
        assert "low" in rcon.by_priority
        await sim.execute(
            "act_null", "goto", {"unit_id": "unit_truck1", "waypoint_id": "wp_pueblo_a"}
        )
        before = rcon.sent
        await sim.tick(1.0)
        assert rcon.sent > before, "el movimiento no ha mandado ningún /tp"
        assert "high" in rcon.by_priority
    finally:
        await sim.stop()
    fallos = [
        e.payload for e in runner_mod._FALLBACK if e.type == EventType.ACTION_FAILED
    ]
    assert fallos == [], fallos
    assert sim.units["unit_truck1"].status == "moving"


# --- el guion ----------------------------------------------------------------------


def _event(type_: EventType, payload: dict, t_sim: float = 90.0) -> Event:
    return Event(
        run_id="run_test",
        seq=1,
        t_wall=datetime.now(UTC),
        t_sim=t_sim,
        type=type_,
        source="core",
        payload=payload,
    )


def test_el_guion_canta_el_motivo_del_replan() -> None:
    ev = _event(
        EventType.PLAN_REPLAN_STARTED,
        {"reason": "pista norte cortada", "trigger": "hard_constraint_violation"},
        t_sim=230.0,
    )
    assert demo.line(ev) == "  03:50  REPLAN · pista norte cortada"


def test_el_guion_no_pinta_payloads_crudos() -> None:
    ev = _event(EventType.PLAN_EMITTED, {"assignments": [{}, {}, {}]})
    assert demo.line(ev).endswith("3 asignaciones")
