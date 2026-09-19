"""FIRMS: focos por satélite (SPEC-007, F2 · REQ-250…252). P4.

`fixtures/feeds/_test/firms_viirs_synthetic.csv` es **sintético**: hasta tener la MAP_KEY no
hay captura real. Trae seis filas escogidas para probar cada rama: dentro y fuera del cuadro,
dentro y fuera de la rejilla del valle, las tres confianzas y una fila rota. Se sustituye por
una captura real (fichero nuevo, sin tocar este) al fijar el ancla.

Lo que se protege:

- **Que la ausencia de focos no publique nada.** Un satélite pasa cada pocas horas y no ve a
  través de nubes ni de humo denso: «sin focos» no es «no hay fuego» (REQ-251).
- **La celda**: un foco en la celda equivocada corrobora una llamada que no era.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from contracts.factkeys import validate_fact_key
from gateway.feeds import FeedContext
from gateway.feeds.anchor import GeoAnchor
from gateway.feeds.firms import detections, parse_csv, source_of, to_facts

CSV = Path("fixtures/feeds/_test/firms_viirs_synthetic.csv").read_text()


def anchor() -> GeoAnchor:
    return GeoAnchor(
        id="t", place="Sitio", lat0=40.0, lon0=-4.0, meters_per_block=25, firms_radius_km=10
    )


def ctx() -> FeedContext:
    return FeedContext(origin_cell="cell_18_7", cell_size=4)


# --- parseo ---------------------------------------------------------------------------


def test_cinco_filas_buenas_y_una_rota():
    parsed = parse_csv(CSV)
    assert len(parsed.records) == 5 and parsed.malformed == 1  # la de confianza `x`


def test_la_hora_sin_cero_a_la_izquierda_se_lee_y_es_utc():
    third = parse_csv(CSV).records[2]
    assert third.t == datetime(2025, 8, 14, 7, 45, tzinfo=UTC)  # `745` = 07:45


def test_un_200_con_texto_de_error_lanza_con_el_texto():
    with pytest.raises(ValueError, match="faltan columnas"):
        parse_csv("Invalid MAP_KEY.")


def test_un_csv_solo_con_cabecera_es_una_lista_vacia():
    header = CSV.splitlines()[0]
    parsed = parse_csv(header + "\n")
    assert parsed.records == [] and parsed.malformed == 0


# --- hechos -----------------------------------------------------------------------------


def test_los_focos_dentro_del_cuadro_y_de_la_rejilla_dan_hechos_burning():
    obs = to_facts(parse_csv(CSV).records, anchor(), ctx())
    # Quedan fuera: la fila a 111 km del ancla, y la que cae al norte del origen (z < 0),
    # que está en el cuadro pero no es una celda del valle.
    assert len(obs) == 3
    for o in obs:
        assert o.fact.kind == "observed"
        assert o.fact.value == "burning"
        assert validate_fact_key(o.fact.key) is str


def test_la_confianza_sale_de_l_n_h():
    obs = to_facts(parse_csv(CSV).records, anchor(), ctx())
    assert sorted(o.fact.confidence for o in obs) == [0.4, 0.7, 0.9]


def test_solo_el_primer_foco_n_h_de_cada_pase_es_critico():
    """El core replanifica ante cada hecho crítico y no los agrupa: con un crítico por foco, un
    incendio grande (cientos de focos por pase) serían cientos de llamadas al modelo. Visto con
    la API real: ~390 críticos en un solo día sobre el valle."""
    obs = to_facts(parse_csv(CSV).records, anchor(), ctx())
    by_conf = {o.fact.confidence: o.fact.severity for o in obs}
    # r1 (n) y r2 (h) son del mismo pase (N, 10:30): el primero avisa, el otro es detalle.
    assert by_conf == {0.7: "critical", 0.9: "medium", 0.4: "low"}


def test_un_pase_ya_avisado_no_vuelve_a_ser_critico_en_el_siguiente_sondeo():
    c = ctx()
    to_facts(parse_csv(CSV).records[:1], anchor(), c)  # avisa el pase N de las 10:30
    second = to_facts(parse_csv(CSV).records[1:2], anchor(), c)  # otro foco del mismo pase
    assert second[0].fact.severity == "medium"


def test_un_pase_distinto_vuelve_a_avisar():
    rows = parse_csv(CSV).records
    same_pass = [r for r in rows if r.satellite == "N"]
    other = [r for r in rows if r.satellite == "1"]
    c = ctx()
    critical = [o for o in to_facts(same_pass + other, anchor(), c) if o.fact.severity == "critical"]
    assert len(critical) == 1  # `l` es `low`: de los n/h solo el pase N tiene uno crítico


def test_solo_se_publican_los_focos_de_la_caja_del_valle():
    """Con la API real la sonda publicaba focos a 5-10 km del valle (`cell_97_10`)."""
    everywhere = to_facts(parse_csv(CSV).records, anchor(), ctx())
    boxed = FeedContext(origin_cell="cell_18_7", cell_size=4, world_box=(0.0, 60.0, 0.0, 30.0))
    inside = to_facts(parse_csv(CSV).records, anchor(), boxed)
    assert len(everywhere) == 3 and len(inside) == 1  # solo (x~51, z~22) cae en la caja
    assert inside[0].fact.confidence == 0.4


def test_la_celda_sale_de_la_proyeccion_y_del_formato_del_escenario():
    obs = to_facts(parse_csv(CSV).records, anchor(), ctx())
    # (40.0, -3.9765) está a ~2 km al este del ancla: x ≈ 80, z = 0 → celda (20, 0)
    assert obs[0].fact.key == "cell:cell_20_0:state"


def test_el_momento_real_es_el_del_satelite_y_no_el_de_ahora():
    obs = to_facts(parse_csv(CSV).records, anchor(), ctx())
    assert obs[0].t_real == datetime(2025, 8, 14, 10, 30, tzinfo=UTC)


def test_la_procedencia_identifica_satelite_pase_y_posicion():
    rec = parse_csv(CSV).records[0]
    assert source_of(rec) == "api:firms:N:2025-08-14T1030:40.0000,-3.9765"


def test_el_mismo_foco_en_dos_sondeos_no_se_republica():
    c = ctx()
    assert to_facts(parse_csv(CSV).records, anchor(), c)
    assert to_facts(parse_csv(CSV).records, anchor(), c) == []


def test_sin_focos_no_se_publica_nada_ni_un_hecho_negativo():
    """REQ-251: la ausencia de detección no es evidencia."""
    empty = parse_csv(CSV.splitlines()[0] + "\n")
    assert to_facts(empty.records, anchor(), ctx()) == []


# --- el mapa ------------------------------------------------------------------------------


def test_las_detecciones_del_mapa_llevan_posicion_y_no_huella():
    det = detections(parse_csv(CSV).records, anchor())
    assert len(det) == 4  # el mapa enseña también el de z < 0; solo se filtra el cuadro
    assert all("footprint_blocks" not in d for d in det)  # sin huella: Minecraft no la tiene (REQ-312)
    # SPEC-008 REQ-293: el mapa real necesita la coordenada, no solo (x, z).
    assert all(isinstance(d["lat"], float) and isinstance(d["lon"], float) for d in det)
    assert det[0]["satellite"] == "N" and det[0]["confidence"] == "n"
