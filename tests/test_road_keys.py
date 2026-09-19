"""Los ids de arista reales (`road:wp_a-wp_b`) atraviesan belief, solver y divergencia.

Los tests de `core` usan ids sintéticos (`e1`), que no llevan el prefijo `road:`, y por eso
no vieron que con los ids del escenario de P2 (renombrados a `road:wp_a-wp_b`):

- el solver emitía `road:road:…:open` y la divergencia no sabía evaluarla (`None`),
- el hecho de corte `road:wp_a-wp_b:cut` no cortaba la arista de `WorldState.roads`,
- voz generaba una clave `road:road:…:cut` que `validate_fact_key` rechaza en silencio.

Los tres fallaban sin ruido, y juntos son el mecanismo del clímax de la demo: el vecino
avisa del corte y el plan tiene que enterarse. Aquí se prueba de punta a punta con el YAML
de verdad y no con un id inventado.
"""

from contracts.calls import Fact
from contracts.factkeys import (
    road_bare,
    road_cause_key,
    road_cut_key,
    road_open_key,
    validate_fact_key,
)
from contracts.plan import Assignment
from core.belief import apply_fact, initial_state
from core.divergence import evaluate_assumption
from core.solver import RoadGraph, build_context
from gateway.scenarios import load_scenario

SC = load_scenario("wildfire_ridge")
ROAD = "road:wp_sur_01-wp_sur_02"  # la que corta el inject del YAML


def _cut_fact(key: str) -> Fact:
    return Fact(
        key=key,
        value=True,
        confidence=0.93,
        source="call:vh_1074",
        severity="critical",
        t_sim=229.0,
    )


def test_los_helpers_aceptan_el_id_con_y_sin_prefijo() -> None:
    assert road_bare(ROAD) == road_bare("wp_sur_01-wp_sur_02") == "wp_sur_01-wp_sur_02"
    for make in (road_cut_key, road_open_key, road_cause_key):
        assert make(ROAD) == make("wp_sur_01-wp_sur_02")
        assert "road:road" not in make(ROAD)
    # `:cut` y `:cause` son claves de hecho y tienen que validar; `:open` es una
    # suposición del plan, no un hecho, y no está (ni debe estar) en `FACT_KEYS`.
    for make in (road_cut_key, road_cause_key):
        assert validate_fact_key(make(ROAD)) is not None, make(ROAD)


def test_el_hecho_de_corte_corta_la_arista_del_estado() -> None:
    st = initial_state("r", SC)
    assert st.roads[ROAD].cut is False
    after = apply_fact(st, _cut_fact(road_cut_key(ROAD)))
    assert after.roads[ROAD].cut is True


def test_el_solver_emite_suposiciones_que_la_divergencia_sabe_evaluar() -> None:
    st = initial_state("r", SC)
    route = Assignment(
        unit_id="unit_truck1",
        task_id="t",
        route=["wp_cruce", "wp_sur_01", "wp_sur_02"],
        eta_s=1.0,
        cost=1.0,
    )
    keys = [
        a.key for a in build_context(st, [route], RoadGraph.from_scenario(SC)).assumptions
    ]
    assert road_open_key(ROAD) in keys
    assert not any("road:road" in k for k in keys), keys

    # Y cerrando el círculo: cortada la arista, la suposición pasa de True a False. Con el
    # id doblado devolvía `None` (no evaluable) en los dos casos y el replan no saltaba.
    assert evaluate_assumption(st, road_open_key(ROAD)) is True
    cut = apply_fact(st, _cut_fact(road_cut_key(ROAD)))
    assert evaluate_assumption(cut, road_open_key(ROAD)) is False


def test_los_ids_sinteticos_de_antes_siguen_funcionando() -> None:
    """La traducción no rompe lo que ya iba: un id sin prefijo se busca tal cual."""
    from core.belief import road_of

    st = initial_state("r", SC)
    assert road_of(st.roads, ROAD) is st.roads[ROAD]
    assert road_of(st.roads, "wp_sur_01-wp_sur_02") is st.roads[ROAD]
    assert road_of(st.roads, "nada") is None


def test_un_assumed_default_entra_a_las_suposiciones_con_peso_alto_hasta_que_se_falsa() -> (
    None
):
    """Regla 4: lo asumido es una suposición pre-rota. Pesa más que una arista abierta y
    deja de vigilarse cuando un hecho observado la sustituye."""
    from core.divergence import divergence
    from core.solver import ASSUMED_WEIGHT

    key = "poi:poi_pueblo_b:immobile"
    st = apply_fact(
        initial_state("r", SC),
        _cut_fact(key).model_copy(update={"value": 1, "kind": "assumed_default"}),
    )
    ctx = build_context(st, [], RoadGraph.from_scenario(SC))
    (assumed,) = [a for a in ctx.assumptions if a.key == key]
    assert assumed.expected == 1 and assumed.weight == ASSUMED_WEIGHT > 1.0
    assert divergence(st, ctx)[0] == 0.0

    truth = apply_fact(
        st, _cut_fact(key).model_copy(update={"value": 3})
    )  # llega el dato observado: 3, no 1
    assert key in divergence(truth, ctx)[1]
    assert key not in [
        a.key for a in build_context(truth, [], RoadGraph.from_scenario(SC)).assumptions
    ]
