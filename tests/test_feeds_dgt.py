"""DGT como segunda fuente de un corte (SPEC-007, F2 · REQ-246…249). P4.

`fixtures/feeds/_test/dgt_sample.xml` es una **captura real** del feed v3.7 recortada a seis
registros. Los casos que la captura no trae (inactivo, otra versión, otra probabilidad) se
fabrican editando su XML aquí mismo, a la vista, y no en un fichero aparte.

Lo que se protege, en orden de lo que más duele:

- **Que un cierre de carril no corte una arista.** Es lo más fácil de confundir con un
  corte y lo más caro de equivocar: el solver dejaría de usar una carretera que funciona.
- **Que desaparecer no reabra** (invariante 8): «ya no está en el feed» no es «lo he visto
  abierto».
- **Que las claves sean las del contrato.** Un hecho con una clave que `belief` no conoce se
  descarta en silencio.
"""

from pathlib import Path

import pytest

from contracts.factkeys import validate_fact_key
from gateway.feeds import FeedContext
from gateway.feeds.anchor import EdgeRef, GeoAnchor
from gateway.feeds.dgt import (
    ALL_LANES,
    SituationRecord,
    is_cut,
    norm_road,
    parse_situations,
    to_facts,
)

FIXTURE = Path("fixtures/feeds/_test/dgt_sample.xml")
XML = FIXTURE.read_bytes()

EDGE = "road:wp_a-wp_b"


def anchor(road: str = "A-8005", pk_from: float = 1.0, pk_to: float = 2.5, edge: str = EDGE) -> GeoAnchor:
    return GeoAnchor(
        id="t",
        place="Sitio",
        lat0=40.0,
        lon0=-4.0,
        meters_per_block=25,
        edges={edge: EdgeRef(road_name=road, pk_from=pk_from, pk_to=pk_to)},
    )


def facts_of(xml: bytes = XML, a: GeoAnchor | None = None, ctx: FeedContext | None = None):
    records = parse_situations(xml).records
    return to_facts(records, a or anchor(), ctx or FeedContext())


def by_key(obs) -> dict:
    return {o.fact.key: o.fact for o in obs}


# --- parseo ---------------------------------------------------------------------------


def test_la_captura_real_se_lee_entera():
    parsed = parse_situations(XML)
    assert parsed.malformed == 0
    assert [r.id for r in parsed.records] == [
        "5684393", "16676270", "17769357", "27494457", "20413525", "20413490",
    ]
    first = parsed.records[0]
    assert (first.road_name, first.management, first.validity) == ("A-8005", "roadClosed", "active")
    assert first.pks == (2.16, 0.0)  # un tramo lleva dos PK
    assert first.detailed_cause == "roadworks"


def test_un_xml_que_no_parsea_lanza():
    with pytest.raises(Exception):  # noqa: B017  # ParseError: el feed entero es inválido
        parse_situations(b"<esto no es xml")


def test_las_carreteras_se_comparan_sin_guiones_ni_mayusculas():
    assert norm_road("N-400") == norm_road("n 400") == norm_road("N400") == "N400"


# --- qué es un corte --------------------------------------------------------------------


def test_solo_lo_que_corta_es_un_corte():
    cuts = {r.id: is_cut(r) for r in parse_situations(XML).records}
    assert cuts == {
        "5684393": True,  # roadClosed
        "16676270": True,  # roadClosed
        "17769357": False,  # laneClosures por obras
        "27494457": False,  # forestFire, pero solo cierra el carril derecho
        "20413525": True,  # inundación sobre toda la calzada
        "20413490": True,
    }


def record(management: str, cause: str = "rockfalls", lanes=(ALL_LANES,)) -> SituationRecord:
    return SituationRecord(
        id="1", version="1", validity="active", probability="certain", road_name="A-1",
        pks=(1.0,), cause_type="environmentalObstruction", detailed_cause=cause,
        management=management, lane_usages=tuple(lanes),
    )


@pytest.mark.parametrize(
    ("management", "cuts"),
    [
        # Combinaciones que existen en el feed real con `rockfalls` sobre toda la calzada:
        ("roadClosed", True),
        ("", True),  # la incidencia no dice qué se hace: la causa manda
        ("doNotUseSpecifiedLanesOrCarriageways", True),
        ("singleAlternateLineTraffic", False),  # hay roca, pero se pasa por turnos
        ("lanesDeviated", False),  # desvío por el arcén o la otra calzada
        ("narrowLanes", False),
        ("useOfSpecifiedLanesOrCarriagewaysAllowed", False),
    ],
)
def test_una_causa_de_corte_sobre_toda_la_calzada_solo_corta_si_la_gestion_no_deja_pasar(
    management: str, cuts: bool
):
    """Contra el feed real de 811 registros, 8 de 9 registros con causa de corte eran de los
    que se circula. Es el fallo que más cuesta: le quita al solver una carretera que va."""
    assert is_cut(record(management)) is cuts


def test_una_causa_de_corte_que_solo_afecta_a_un_carril_no_corta():
    assert is_cut(record("", lanes=("rightLane",))) is False


# --- hechos: el corte de DGT ---------------------------------------------------------------


def test_un_roadclosed_sobre_la_arista_declarada_da_los_dos_hechos():
    facts = by_key(facts_of())
    cut, cause = facts[f"road:{EDGE.removeprefix('road:')}:cut"], facts[
        f"road:{EDGE.removeprefix('road:')}:cause"
    ]
    assert cut.value is True and cut.kind == "observed" and cut.confidence == 0.9
    assert cut.source == "api:dgt:5684393v1"
    assert cut.severity == "medium"  # ninguna unidad del plan la usa
    assert cause.value == "roadworks · DGT 5684393"


def test_solo_el_corte_lleva_la_gravedad_para_no_provocar_dos_replanes():
    """El core replanifica ante cada hecho crítico y no los agrupa: si `cut` y `cause` fueran
    los dos críticos, un corte serían dos llamadas al modelo (invariante 7)."""
    ctx = FeedContext(route_edges=frozenset({EDGE}))
    sev = {o.fact.key.rsplit(":", 1)[1]: o.fact.severity for o in facts_of(ctx=ctx)}
    assert sev == {"cut": "critical", "cause": "low"}


def test_las_claves_son_las_del_contrato():
    for o in facts_of():
        assert validate_fact_key(o.fact.key) is not None, o.fact.key
    # y el id del escenario NO duplica el prefijo: `road:road:…` se descartaría en silencio
    assert all(":road:" not in o.fact.key for o in facts_of())


def test_un_cierre_de_carril_no_corta_la_arista_aunque_case_en_carretera_y_pk():
    a = anchor(road="A-67", pk_from=191.0, pk_to=192.5)
    assert facts_of(a=a) == []


def test_un_incendio_que_solo_cierra_un_carril_no_corta():
    a = anchor(road="FV-1", pk_from=15.0, pk_to=17.0)
    assert facts_of(a=a) == []


def test_fuera_del_tramo_declarado_no_casa():
    assert facts_of(a=anchor(pk_from=50.0, pk_to=60.0)) == []


def test_un_registro_lineal_casa_si_su_tramo_solapa_con_el_declarado():
    # El registro cubre el PK 0,0–2,16. La arista declarada empieza en 2,0: solapan.
    assert facts_of(a=anchor(pk_from=2.0, pk_to=3.0))
    # Y el orden de los PK declarados da igual.
    assert facts_of(a=anchor(pk_from=3.0, pk_to=2.0))


def test_otra_carretera_no_casa():
    assert facts_of(a=anchor(road="A-92")) == []


def test_el_nombre_de_carretera_del_ancla_puede_escribirse_a_su_manera():
    assert facts_of(a=anchor(road="a 8005"))


def test_una_inundacion_sobre_toda_la_calzada_corta_y_es_critica():
    a = anchor(road="MA-8306", pk_from=1.0, pk_to=2.0)
    facts = [o.fact for o in facts_of(a=a) if o.fact.key.endswith(":cut")]
    assert len(facts) == 2  # dos registros de la misma inundación, dos versiones distintas
    assert {f.severity for f in facts} == {"critical"}
    cause = next(o.fact for o in facts_of(a=a) if o.fact.key.endswith(":cause"))
    assert cause.value.startswith("flooding · DGT 20413")


def test_un_corte_sobre_una_arista_que_usa_el_plan_es_critico():
    ctx = FeedContext(route_edges=frozenset({EDGE}))
    assert by_key(facts_of(ctx=ctx))["road:wp_a-wp_b:cut"].severity == "critical"


def test_la_ruta_del_plan_casa_con_o_sin_prefijo_road():
    ctx = FeedContext(route_edges=frozenset({"wp_a-wp_b"}))
    assert by_key(facts_of(ctx=ctx))["road:wp_a-wp_b:cut"].severity == "critical"


# --- confianza y estado --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("probability", "expected"),
    [("certain", 0.9), ("probable", 0.7), ("riskOf", 0.4), ("algoNuevo", 0.5)],
)
def test_la_confianza_sale_de_la_probabilidad_del_registro(probability: str, expected: float):
    xml = XML.replace(b">certain<", f">{probability}<".encode())
    assert by_key(facts_of(xml))["road:wp_a-wp_b:cut"].confidence == expected


def test_un_registro_inactivo_no_se_publica():
    xml = XML.replace(b"<com:validityStatus>active<", b"<com:validityStatus>suspended<")
    assert facts_of(xml) == []


# --- deduplicación y desaparición --------------------------------------------------------------


def test_la_misma_version_se_publica_una_sola_vez():
    ctx = FeedContext()
    assert facts_of(ctx=ctx)
    assert facts_of(ctx=ctx) == []  # el siguiente sondeo trae el mismo feed


def test_una_version_nueva_del_mismo_registro_se_publica_de_nuevo():
    ctx = FeedContext()
    facts_of(ctx=ctx)
    bumped = XML.replace(b'id="5684393" version="1"', b'id="5684393" version="2"')
    again = by_key(facts_of(bumped, ctx=ctx))
    assert again["road:wp_a-wp_b:cut"].source == "api:dgt:5684393v2"


def test_que_el_registro_desaparezca_del_feed_no_publica_cut_false():
    """REQ-249 / invariante 8. Es el test que más importa de este fichero."""
    ctx = FeedContext()
    first = facts_of(ctx=ctx)
    gone = to_facts([], anchor(), ctx)  # el corte ya no aparece
    assert gone == []
    assert all(o.fact.value is True for o in first if o.fact.key.endswith(":cut"))
    assert not any(o.fact.value is False for o in first + gone)
