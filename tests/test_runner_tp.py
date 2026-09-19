"""El `/tp` final de un movimiento que nace hecho, y el estado `working`.

Dos cosas que solo se ven con Paper de verdad y que aquí se prueban contra
`FakeRcon`: que el marcador no se quede atrás cuando el `goto` no tiene nada
que recorrer, y que un camión parado junto al fuego no figure como `idle`.
"""

from pathlib import Path

import pytest

from contracts.events import EventType
from sim import runner as runner_mod
from sim.rcon import HIGH, FakeRcon
from sim.runner import Sim

ESCENARIO = Path("scenarios/wildfire_ridge.yaml")


@pytest.fixture
def sim():
    runner_mod._FALLBACK.clear()
    return Sim(ESCENARIO, FakeRcon())


def eventos(tipo: EventType) -> list[dict]:
    return [e.payload for e in runner_mod._FALLBACK if e.type == tipo]


def tps(sim: Sim) -> list[str]:
    return [
        c for p, c in sim.rcon.commands if c.startswith("tp @e[tag=unit") and p == HIGH
    ]


# --- el `/tp` que faltaba ---


async def test_un_goto_al_waypoint_donde_ya_esta_manda_un_tp(sim):
    """`unit_truck1` arranca encima de `wp_base`: la ruta es un solo waypoint y el
    `Movement` nace `done`. Antes eso no mandaba ningún `/tp` y el marcador se
    quedaba donde estuviera; ahora manda exactamente uno, al destino."""
    await sim.execute("a1", "goto", {"unit_id": "unit_truck1", "waypoint_id": "wp_base"})
    await sim.tick(1.0)

    x, z = sim.graph.position_of("wp_base")
    assert tps(sim) == [f"tp @e[tag=unit_truck1] {x:.2f} 65.00 {z:.2f} 0.0 0"]
    assert eventos(EventType.WORLD_UNIT_ARRIVED) == [
        {"unit_id": "unit_truck1", "waypoint_id": "wp_base"}
    ]
    assert eventos(EventType.WORLD_UNIT_POSITION)[-1]["x"] == x


async def test_reasignar_a_mitad_de_arista_no_deja_el_marcador_atras(sim):
    """La ambulancia del ensayo: en ruta, reasignada al waypoint que ya tiene más
    cerca. El journal decía que había llegado y en Minecraft seguía 55 bloques
    atrás. El `/tp` del tick de llegada tiene que ser al destino, y el evento de
    posición tiene que decir lo mismo que el `/tp`."""
    await sim.execute(
        "a1", "goto", {"unit_id": "unit_truck1", "waypoint_id": "wp_pueblo_a"}
    )
    for _ in range(25):
        await sim.tick(1.0)
    unidad = sim.units["unit_truck1"]
    destino = sim._nearest("unit_truck1")
    x, z = sim.graph.position_of(destino)
    assert (unidad.x, unidad.z) != (x, z), "tiene que estar a mitad de arista"

    sim.rcon.commands.clear()
    await sim.execute("a2", "goto", {"unit_id": "unit_truck1", "waypoint_id": destino})
    await sim.tick(1.0)

    assert tps(sim) == [f"tp @e[tag=unit_truck1] {x:.2f} 65.00 {z:.2f} 0.0 0"]
    posicion = eventos(EventType.WORLD_UNIT_POSITION)[-1]
    assert (posicion["x"], posicion["z"]) == (x, z)
    assert eventos(EventType.WORLD_UNIT_ARRIVED)[-1]["waypoint_id"] == destino
    assert "unit_truck1" not in sim._moving


async def test_una_llegada_normal_no_duplica_el_tp(sim):
    """El último paso del interpolador ya deja el marcador en el destino: no hace
    falta un segundo `/tp` encima. Cinco por segundo mientras se mueve, y ni uno
    más al llegar."""
    await sim.execute("a1", "goto", {"unit_id": "unit_truck1", "waypoint_id": "wp_cruce"})
    viaje = sim._moving["unit_truck1"][0]
    total = 0
    while "unit_truck1" in sim._moving:
        sim.rcon.commands.clear()
        await sim.tick(1.0)
        assert len(tps(sim)) <= 5
        total += len(tps(sim))
    assert total == -(-viaje.total_m // (viaje.speed_mps / 5))  # pasos de 0,8 m


# --- `working`: el estado cuenta lo que hace la unidad ---


def _aparcar(sim: Sim, unit_id: str, cid: str) -> None:
    x, z = sim.hazard.center_of(cid)
    sim.units[unit_id] = sim.units[unit_id].model_copy(update={"x": x, "z": z})


async def test_un_camion_parado_junto_al_fuego_pasa_a_working_una_vez(sim):
    origen = sim.scenario.hazard.origin_cell
    _aparcar(sim, "unit_truck1", origen)
    for _ in range(3):
        await sim._suppress(0.0)  # sin avanzar la sofocación: solo el estado
    assert eventos(EventType.WORLD_UNIT_STATUS) == [
        {"unit_id": "unit_truck1", "status": "working", "reason": f"sofocando {origen}"}
    ]
    assert sim.units["unit_truck1"].status == "working"


async def test_cuando_apaga_lo_que_tenia_a_tiro_vuelve_a_idle_una_vez(sim):
    """`working` → `idle` exactamente una vez por cambio, y nada mientras no
    cambie nada. El propio camión apaga la celda: a `suppress_rate` celdas por
    minuto, la única que tiene a tiro cae en `60 / rate` segundos."""
    origen = sim.scenario.hazard.origin_cell
    _aparcar(sim, "unit_truck1", origen)
    segundos = int(60 / sim.scenario.hazard.suppress_rate) + 2
    for _ in range(segundos):
        await sim._suppress(1.0)
    assert sim.hazard.state_of(origen) == "burnt"
    assert eventos(EventType.WORLD_UNIT_STATUS) == [
        {"unit_id": "unit_truck1", "status": "working", "reason": f"sofocando {origen}"},
        {"unit_id": "unit_truck1", "status": "idle", "reason": "sin fuego a tiro"},
    ]


async def test_una_unidad_en_marcha_sofoca_pero_sigue_moving(sim):
    """Sofoca al pasar —eso es cosa del autómata—, pero su estado cuenta lo que
    hace: ir a algún sitio."""
    origen = sim.scenario.hazard.origin_cell
    _aparcar(sim, "unit_truck1", origen)
    await sim.execute(
        "a1", "goto", {"unit_id": "unit_truck1", "waypoint_id": "wp_pueblo_b"}
    )
    await sim._suppress(0.0)
    estados = [e["status"] for e in eventos(EventType.WORLD_UNIT_STATUS)]
    assert estados == ["moving"]


async def test_al_llegar_junto_al_fuego_pasa_de_llegada_a_working(sim):
    """La secuencia que ve el dashboard: `moving` → `idle` (llegada) → `working`
    en el mismo tick, y ahí se queda mientras arda algo a tiro."""
    # Ningún waypoint queda a tiro de la ignición del YAML, así que se prende la
    # celda de debajo de `wp_sur_01` a mano y el camión llega desde tres bloques.
    wx, wz = sim.graph.position_of("wp_sur_01")
    sim.hazard._activate(sim.hazard_cell_at(wx, wz))
    sim.units["unit_truck1"] = sim.units["unit_truck1"].model_copy(
        update={"x": wx - 3, "z": wz}
    )
    await sim.execute(
        "a1", "goto", {"unit_id": "unit_truck1", "waypoint_id": "wp_sur_01"}
    )
    await sim.tick(1.0)
    estados = [(e["status"], e["reason"]) for e in eventos(EventType.WORLD_UNIT_STATUS)]
    assert estados[:2] == [("moving", "hacia wp_sur_01"), ("idle", "llegada")]
    assert estados[2][0] == "working", f"en wp_sur_01 hay fuego a tiro: {estados}"
    await sim._suppress(0.0)
    assert len(eventos(EventType.WORLD_UNIT_STATUS)) == 3, "sin cambio no hay evento"
