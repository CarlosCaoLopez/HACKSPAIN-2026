"""La costura entre el escenario de P2 y el detector de divergencia de P1.

Estos tests no prueban lógica: prueban que dos paquetes que no se importan entre
sí se entienden. Es donde salen los fallos que ninguna suite por separado ve.
"""

from pathlib import Path

import pytest

from contracts.factkeys import road_bare, road_cut_key, road_open_key, validate_fact_key
from contracts.world import Wind, WorldState
from core.divergence import evaluate_assumption
from sim.scenario import load

ESCENARIOS = ["wildfire_ridge", "blackout_grid"]


@pytest.fixture
def estado():
    s = load(Path("scenarios/wildfire_ridge.yaml"))
    return s, WorldState(
        run_id="r", seq=1, t_sim=0.0, wind=Wind(bearing_deg=270, speed=1.2),
        roads={r.id: r for r in s.roads}, pois={p.id: p for p in s.pois},
    )


@pytest.mark.parametrize("nombre", ESCENARIOS)
def test_el_id_de_una_arista_es_su_propia_direccion(nombre):
    """`road:wp_a-wp_b`: la misma forma con la que una llamada o un
    `human.override` nombran una carretera sin conocer el YAML."""
    s = load(Path(f"scenarios/{nombre}.yaml"))
    for road in s.roads:
        assert road.id.startswith("road:"), road.id
        assert road_bare(road.id) == f"{road.a}-{road.b}", road.id


def test_la_clave_de_un_corte_lleva_el_prefijo_una_sola_vez(estado):
    """Los ids ya traen `road:`, así que componer a mano da `road:road:…`, una
    clave que `validate_fact_key` rechaza en silencio. Por eso se pasa por
    `road_cut_key`, que normaliza."""
    _, state = estado
    for edge_id in state.roads:
        clave = road_cut_key(edge_id)
        assert clave.count("road:") == 1, clave
        assert validate_fact_key(clave) is bool, clave


def test_divergence_ve_el_corte_de_una_carretera_nuestra(estado):
    """El disparo del clímax: el plan supone que la pista sur es transitable, la
    llamada dice que no, y la divergencia tiene que verlo. Si la clave no se
    parsea, `evaluate_assumption` devuelve None, la suposición no es evaluable y
    el replan del minuto 3:30 no salta."""
    _, state = estado
    edge_id = "road:wp_sur_01-wp_sur_02"
    suposicion = road_open_key(edge_id)

    assert evaluate_assumption(state, suposicion) is True

    cortada = state.roads[edge_id].model_copy(update={"cut": True})
    despues = state.model_copy(update={"roads": {**state.roads, edge_id: cortada}})
    assert evaluate_assumption(despues, suposicion) is False, (
        "la divergencia no ve el corte: el replan de la demo no se dispararía"
    )


def test_todas_nuestras_carreteras_son_evaluables(estado):
    _, state = estado
    for edge_id in state.roads:
        valor = evaluate_assumption(state, road_open_key(edge_id))
        assert valor is not None, edge_id
