"""Los alias del escenario: lo que dice el vecino → id. Los dos cargadores (el de
`sim`, que valida, y el de `voice.pois`, que resuelve en caliente) leen el mismo
YAML y tienen que ponerse de acuerdo sin que nadie mantenga una tabla aparte."""

import math
from pathlib import Path

import pytest
import yaml

from sim import runner as runner_mod
from sim.rcon import FakeRcon, PrintRcon, Rcon
from sim.runner import Sim, dummy_core_step
from sim.scenario import ScenarioError, load
from voice import pois

ESCENARIO = Path("scenarios/wildfire_ridge.yaml")
PISTA_SUR = "road:wp_sur_01-wp_sur_02"


@pytest.fixture
def scenario():
    return load(ESCENARIO)


@pytest.fixture
def tabla():
    assert pois.load_scenario_yaml(ESCENARIO)
    yield
    pois.set_scenario([])


def test_el_yaml_declara_los_alias_y_sim_los_valida(scenario):
    assert scenario.poi_aliases["el molino"] == "poi_molino"
    assert scenario.road_aliases["pista del sur"] == PISTA_SUR
    ids = {p.id for p in scenario.pois}
    assert set(scenario.poi_aliases.values()) <= ids
    assert set(scenario.road_aliases.values()) <= {r.id for r in scenario.roads}


def test_el_molino_es_un_poi_del_escenario(scenario):
    molino = next(p for p in scenario.pois if p.id == "poi_molino")
    assert molino.kind == "landmark"
    assert molino.description, "Jev necesita la descripción en inglés"
    assert molino.waypoint_id in {w.id for w in scenario.waypoints}


def test_la_pista_del_sur_es_la_que_corta_el_inject(scenario):
    """El alias apunta al tramo que la demo corta a los 210 s: si alguien cambia
    uno de los dos, el vecino y el inject dejan de hablar de la misma carretera."""
    cortes = [i.payload["edge"] for i in scenario.injects if i.type == "road_cut"]
    assert PISTA_SUR in cortes


def test_voice_resuelve_lo_que_dice_el_vecino(tabla):
    assert pois.resolve_poi_local("el molino viejo") == "poi_molino"
    assert pois.resolve_poi_local("El Molino") == "poi_molino"
    assert pois.resolve_poi_local("el pueblo de arriba") == "poi_pueblo_a"
    assert pois.resolve_poi_local("la base") == "poi_base"
    assert pois.resolve_edge_local("la pista del sur") == PISTA_SUR
    assert pois.resolve_edge_local("carretera del norte") == "road:wp_nor_01-wp_nor_02"
    assert pois.road_label(PISTA_SUR) == "pista del sur"


def test_un_alias_a_un_id_inventado_falla_al_cargar(tmp_path):
    raw = yaml.safe_load(ESCENARIO.read_text(encoding="utf-8"))
    raw["poi_aliases"] = {"la ermita": "poi_ermita"}
    raw["road_aliases"] = {"pista del sur": "road:wp_sur_01-wp_sur_99"}
    roto = tmp_path / "roto.yaml"
    roto.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ScenarioError, match=r"poi_ermita(.|\n)*wp_sur_99"):
        load(roto)


# --- `make dev-sim` sin Minecraft ---


async def test_print_rcon_cumple_el_protocolo(capsys):
    rcon: Rcon = PrintRcon(limit=1)
    await rcon.connect()
    await rcon.send_many(["tp a", "tp b"])
    await rcon.close()
    out = capsys.readouterr().out
    assert "[high] tp a" in out
    assert "tp b" not in out, "por encima del límite se cuenta, no se imprime"
    assert "2 comandos" in out


async def test_el_core_tonto_manda_un_camion_al_fuego():
    runner_mod._FALLBACK.clear()
    sim = Sim(ESCENARIO, FakeRcon())
    assert await dummy_core_step(sim, 1) == "act_dummy_0001"
    ((unit_id, (movement, action_id)),) = sim._moving.items()
    assert unit_id.startswith("unit_truck")
    assert action_id == "act_dummy_0001"
    # Derivado del escenario, no escrito a mano: mover la ignición en el YAML es
    # una decisión de P2 y no debería romper un test del core tonto.
    ox, oz = (
        int(v) * sim.scenario.hazard.cell_size
        for v in sim.scenario.hazard.origin_cell.split("_")[1:]
    )
    esperado = min(
        sim.graph.waypoint_ids,
        key=lambda w: math.dist(sim.graph.position_of(w), (ox, oz)),
    )
    assert movement.route[-1] == esperado, "el waypoint más cercano al origen"
