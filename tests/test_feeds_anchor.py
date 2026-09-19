"""El ancla y el reloj fechado de las fuentes reales (SPEC-007, F1). P4.

Lo que tiene lógica de verdad y se rompe en silencio si falla:

- **La proyección** ida y vuelta: un error aquí pone el foco de FIRMS en otra celda y nada
  avisa, porque el hecho sale igual de válido.
- **El id de celda**, que no se formatea igual en los dos escenarios (`cell_18_7` frente a
  `cell_08_00`). Un id mal formateado es un hecho que `belief` no aplica, sin error.
- **El reloj**: los datos tienen que salir en orden de `t_sim` y no antes de tiempo.
"""

import math
from datetime import UTC, datetime, timedelta, timezone

import pytest

from contracts.events import FactAsserted
from contracts.world import Cell, RoadEdge
from gateway.feeds import Observation
from gateway.feeds.anchor import (
    GeoAnchor,
    bbox,
    cell_id_at,
    edges_on_route,
    load_anchor,
    to_geo,
    to_world,
    world_box,
)
from gateway.feeds.clock import Schedule, due_t_sim

CEST = timezone(timedelta(hours=2))


def make_anchor(**over) -> GeoAnchor:
    base = {
        "id": "t",
        "place": "Sitio de prueba",
        "lat0": 40.0,
        "lon0": -4.0,
        "meters_per_block": 25,
    }
    return GeoAnchor(**{**base, **over})


def fact(key: str = "wind:bearing_deg") -> FactAsserted:
    return FactAsserted(
        key=key, value=1.0, confidence=0.8, source="api:test", severity="low"
    )


# --- proyección -----------------------------------------------------------------------


def test_el_ancla_es_el_origen_del_mundo():
    a = make_anchor()
    assert to_world(a, a.lat0, a.lon0) == (0.0, 0.0)


def test_este_es_mas_x_y_sur_es_mas_z():
    a = make_anchor()
    x, z = to_world(a, a.lat0, a.lon0 + 0.01)  # al este
    assert x > 0 and z == pytest.approx(0)
    x, z = to_world(a, a.lat0 - 0.01, a.lon0)  # al sur
    assert x == pytest.approx(0) and z > 0


def test_un_grado_de_latitud_son_111_km():
    a = make_anchor(meters_per_block=1)
    _, z = to_world(a, a.lat0 - 1, a.lon0)
    assert z == pytest.approx(111_195, rel=1e-3)


def test_ida_y_vuelta_a_20_km_con_error_de_menos_de_un_metro():
    a = make_anchor()
    x, z = 20_000 / a.meters_per_block, -13_000 / a.meters_per_block
    lat, lon = to_geo(a, x, z)
    x2, z2 = to_world(a, lat, lon)
    assert math.hypot(x2 - x, z2 - z) * a.meters_per_block < 1.0


def test_el_cuadro_de_firms_contiene_el_ancla_y_tiene_el_radio_pedido():
    a = make_anchor(firms_radius_km=10)
    west, south, east, north = bbox(a)
    assert west < a.lon0 < east and south < a.lat0 < north
    # 10 km de radio = 20 km de lado en latitud
    assert (north - south) * 111.195 == pytest.approx(20, rel=1e-2)


def test_reference_start_sin_zona_horaria_se_rechaza_al_cargar():
    with pytest.raises(ValueError, match="zona horaria"):
        make_anchor(reference_start=datetime(2025, 8, 14, 12, 0))  # noqa: DTZ001 — sin zona a propósito: es lo que se rechaza


def test_meters_per_block_tiene_que_ser_positivo():
    with pytest.raises(ValueError):
        make_anchor(meters_per_block=0)


def test_el_ancla_del_repo_carga_esta_fijada_y_es_un_sitio_de_verdad():
    """Si alguien deja `fixed: true` con las coordenadas del marcador, las fuentes consultarían
    un punto inventado y lo presentarían como real: por eso se comprueba que el sitio existe
    dentro de España y que el día está fijado (SPEC-007 REQ-238, modo fechado)."""
    a = load_anchor("wildfire_ridge")
    assert a is not None and a.fixed is True
    assert 35.5 < a.lat0 < 44.5 and -10.0 < a.lon0 < 5.0, (
        "fuera de la península: un marcador"
    )
    assert (a.lat0, a.lon0) != (40.0, -4.0), "son las coordenadas del marcador"
    assert a.reference_start is not None and a.reference_start.tzinfo is not None
    assert a.place and "SIN FIJAR" not in a.place


def test_un_escenario_sin_ancla_devuelve_none():
    assert load_anchor("no_existe") is None


# --- celdas ---------------------------------------------------------------------------


def cells_of(*pairs: tuple[str, int, int]) -> dict[str, Cell]:
    return {cid: Cell(id=cid, cx=cx, cz=cz) for cid, cx, cz in pairs}


def test_la_celda_se_busca_por_indice_y_no_se_formatea_si_existe():
    cells = cells_of(("cell_18_7", 18, 7), ("cell_08_00", 8, 0))
    assert cell_id_at(73, 29, 4, cells, "cell_18_7") == "cell_18_7"
    # Aunque el escenario formatee con ceros, manda el id que ya tiene el estado.
    assert cell_id_at(33, 1, 4, cells, "cell_18_7") == "cell_08_00"


def test_sin_la_celda_en_el_estado_se_formatea_como_el_origin_cell_sin_ceros():
    assert cell_id_at(73, 29, 4, {}, "cell_18_7") == "cell_18_7"
    assert cell_id_at(5, 5, 4, {}, "cell_18_7") == "cell_1_1"


def test_sin_la_celda_en_el_estado_se_formatea_como_el_origin_cell_con_ceros():
    assert cell_id_at(33, 1, 4, {}, "cell_08_00") == "cell_08_00"
    assert cell_id_at(5, 5, 4, {}, "cell_08_00") == "cell_01_01"
    assert (
        cell_id_at(400, 0, 4, {}, "cell_08_00") == "cell_100_00"
    )  # más ancho que el relleno


def test_fuera_de_la_rejilla_no_es_una_celda():
    assert cell_id_at(-1, 10, 4, {}, "cell_18_7") is None
    assert cell_id_at(10, -0.5, 4, {}, "cell_18_7") is None


def test_la_caja_del_valle_sale_de_los_waypoints_y_pois_con_margen():
    from gateway.scenarios import load_scenario

    sc = load_scenario("wildfire_ridge")
    x0, x1, z0, z1 = world_box(sc, margin=0)
    xs = [p.x for p in sc.pois] + [w.x for w in sc.waypoints]
    zs = [p.z for p in sc.pois] + [w.z for w in sc.waypoints]
    assert (x0, x1, z0, z1) == (min(xs), max(xs), min(zs), max(zs))
    wide = world_box(sc, margin=20)
    assert wide == (x0 - 20, x1 + 20, z0 - 20, z1 + 20)


# --- aristas en ruta --------------------------------------------------------------------

ROADS = [
    RoadEdge(id="road:wp_a-wp_b", a="wp_a", b="wp_b", length_m=100),
    RoadEdge(id="road:wp_b-wp_c", a="wp_b", b="wp_c", length_m=100),
    RoadEdge(id="road:wp_x-wp_y", a="wp_x", b="wp_y", length_m=100),
]


def test_una_ruta_usa_sus_aristas_en_los_dos_sentidos():
    assert edges_on_route([["wp_a", "wp_b", "wp_c"]], ROADS) == {
        "road:wp_a-wp_b",
        "road:wp_b-wp_c",
    }
    assert edges_on_route([["wp_c", "wp_b"]], ROADS) == {"road:wp_b-wp_c"}


def test_saltos_sin_arista_y_rutas_vacias_no_rompen_ni_inventan():
    assert edges_on_route([["wp_a", "wp_c"]], ROADS) == set()  # no son vecinos
    assert edges_on_route([[], ["wp_a"]], ROADS) == set()
    assert edges_on_route([], ROADS) == set()


# --- reloj ----------------------------------------------------------------------------


def test_en_vivo_todo_sale_ya():
    a = make_anchor()  # sin reference_start
    assert due_t_sim(a, datetime(2025, 8, 14, 15, 0, tzinfo=UTC)) == 0.0
    assert due_t_sim(a, None) == 0.0


def test_fechado_un_dato_sale_en_su_t_sim():
    a = make_anchor(
        reference_start=datetime(2025, 8, 14, 12, 0, tzinfo=CEST), time_scale=60
    )
    # 12:00 CEST = 10:00 UTC. Diez minutos reales después = 10 s de t_sim a 60x.
    assert due_t_sim(a, datetime(2025, 8, 14, 10, 10, tzinfo=UTC)) == pytest.approx(10.0)


def test_los_csv_de_firms_sin_zona_se_leen_como_utc():
    a = make_anchor(
        reference_start=datetime(2025, 8, 14, 12, 0, tzinfo=CEST), time_scale=60
    )
    assert due_t_sim(a, datetime(2025, 8, 14, 10, 10)) == pytest.approx(10.0)  # noqa: DTZ001 — FIRMS da UTC sin zona


def test_un_dato_anterior_al_arranque_sale_ya():
    a = make_anchor(reference_start=datetime(2025, 8, 14, 12, 0, tzinfo=CEST))
    assert due_t_sim(a, datetime(2025, 8, 13, 0, 0, tzinfo=UTC)) == 0.0


def test_la_cola_emite_en_orden_y_no_antes_de_tiempo():
    a = make_anchor(
        reference_start=datetime(2025, 8, 14, 12, 0, tzinfo=CEST), time_scale=60
    )
    at = lambda minutes: (
        datetime(2025, 8, 14, 10, 0, tzinfo=UTC) + timedelta(minutes=minutes)
    )
    s = Schedule()
    for minutes, key in [(30, "c"), (10, "a"), (20, "b")]:
        s.push(Observation(at(minutes), fact(key)), a)

    assert s.pop_due(5.0) == []  # a sale en t_sim=10
    assert [o.fact.key for o in s.pop_due(10.0)] == ["a"]
    assert [o.fact.key for o in s.pop_due(100.0)] == ["b", "c"]
    assert len(s) == 0


def test_dos_datos_con_el_mismo_t_sim_no_revientan_la_cola():
    a = make_anchor()  # en vivo: todos con due=0
    s = Schedule()
    s.push(Observation(None, fact("a")), a)
    s.push(Observation(None, fact("b")), a)
    assert [o.fact.key for o in s.pop_due(0.0)] == ["a", "b"]  # en orden de llegada
