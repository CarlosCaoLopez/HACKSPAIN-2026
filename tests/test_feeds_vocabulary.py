"""La correspondencia 1 a 1 entre Minecraft y las APIs reales (SPEC-007 REQ-311…313). P4.

Minecraft y las fuentes reales son **dos fuentes separadas** de la misma clase de información:
las fuentes no escriben en Minecraft. Lo que se exige es que puedan mostrar los mismos tipos
de dato, y este fichero lo hace verificable en los dos sentidos:

- **Lo que publica una API tiene equivalente en Minecraft** (a y b).
- **Lo que Minecraft acepta como entrada del entorno tiene equivalente en las APIs, o un
  motivo escrito de por qué no** (c). Añadir un inject al sim sin decidir qué pasa del lado
  real rompe este test, y es justo lo que se quiere: que nadie cambie un lado en silencio.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from contracts.world import CellState
from gateway.feeds import ENVIRONMENT, SIM_ONLY, FeedContext, dgt, firms, open_meteo
from gateway.feeds.anchor import EdgeRef, GeoAnchor
from sim.injects import KNOWN as SIM_INJECTS

FIX = Path("fixtures/feeds/_test")


def matches(template: str, key: str) -> bool:
    """Una clave concreta casa con una plantilla de `ENVIRONMENT` (`<x>` es una variable)."""
    tpl, seg = template.split(":"), key.split(":")
    return len(tpl) == len(seg) and all(
        (t.startswith("<") and t.endswith(">")) or t == s for t, s in zip(tpl, seg)
    )


def published_keys() -> set[str]:
    """Todas las claves que publican los tres adaptadores sobre sus fixtures."""
    anchor = GeoAnchor(
        id="t",
        place="Sitio",
        lat0=40.0,
        lon0=-4.0,
        meters_per_block=25,
        reference_start=datetime(2025, 8, 14, tzinfo=UTC),
        edges={"road:wp_a-wp_b": EdgeRef(road_name="A-8005", pk_from=1.0, pk_to=2.5)},
    )
    ctx = FeedContext(origin_cell="cell_18_7", cell_size=4)
    meteo = open_meteo.parse_hourly(
        json.loads((FIX / "open_meteo_hourly.json").read_text())
    )
    cut = dgt.parse_situations((FIX / "dgt_sample.xml").read_bytes())
    fires = firms.parse_csv((FIX / "firms_viirs_synthetic.csv").read_text())
    obs = (
        open_meteo.to_facts(meteo.records, anchor, ctx)
        + dgt.to_facts(cut.records, anchor, ctx)
        + firms.to_facts(fires.records, anchor, ctx)
    )
    assert obs, "sin hechos no se prueba nada"
    return {o.fact.key for o in obs}


def test_toda_clave_que_publica_una_api_es_un_dato_del_entorno():
    """(a) Una fuente que publicara la causa de un corte, un aviso o cualquier otra cosa que
    Minecraft no muestra tendría que pasar por aquí y rompería."""
    templates = [e.fact_key for e in ENVIRONMENT]
    outside = {k for k in published_keys() if not any(matches(t, k) for t in templates)}
    assert outside == set(), f"claves sin equivalente en Minecraft: {sorted(outside)}"


def test_cada_fuente_publica_su_parte_del_vocabulario_y_nada_mas():
    keys = published_keys()
    for info in ENVIRONMENT:
        assert any(matches(info.fact_key, k) for k in keys), (
            f"{info.fact_key} está en el vocabulario y ninguna fuente lo publica"
        )


def test_cada_entrada_del_vocabulario_existe_de_verdad_en_el_sim():
    """(b) El nombre del lado de Minecraft no es una promesa: se busca en el sim."""
    cell_states = set(get_args(CellState))
    for info in ENVIRONMENT:
        if info.sim_input in {"wind_shift", "road_cut"}:
            assert info.sim_input in SIM_INJECTS, f"el sim ya no acepta {info.sim_input}"
        else:
            assert info.sim_input in cell_states, f"{info.sim_input} no es un CellState"


def test_toda_entrada_del_entorno_del_sim_esta_en_el_vocabulario_o_tiene_motivo():
    """(c) El sentido contrario. Si Luis añade un inject, este test obliga a decidir qué pasa
    del lado real: o entra en `ENVIRONMENT` con su fuente, o entra en `SIM_ONLY` con su motivo."""
    in_vocabulary = {e.sim_input for e in ENVIRONMENT}
    undecided = set(SIM_INJECTS) - in_vocabulary - set(SIM_ONLY)
    assert undecided == set(), (
        f"inject del sim sin decidir del lado real: {sorted(undecided)}"
    )


def test_solo_minecraft_lleva_un_motivo_y_no_es_un_hueco_disfrazado():
    assert set(SIM_ONLY) <= set(SIM_INJECTS), (
        "SIM_ONLY nombra algo que el sim ya no tiene"
    )
    assert all(len(reason) > 20 for reason in SIM_ONLY.values()), (
        "un motivo de verdad, no una etiqueta"
    )
    assert not set(SIM_ONLY) & {e.sim_input for e in ENVIRONMENT}, (
        "o es de los dos, o solo del sim"
    )


def test_las_plantillas_del_vocabulario_son_claves_validas_del_contrato():
    """Si `belief` no conoce la clave, la descarta en silencio: el vocabulario tiene que ser
    el que el core sabe aplicar."""
    from contracts.factkeys import FACT_KEYS

    for info in ENVIRONMENT:
        assert info.fact_key in FACT_KEYS, (
            f"{info.fact_key} no está en contracts.factkeys"
        )


def test_lo_retirado_por_la_correspondencia_no_vuelve_a_colarse():
    """AEMET, la causa del corte y la huella no tienen equivalente en Minecraft (REQ-312)."""
    keys = published_keys()
    assert not any(k.startswith("alert:") or k.endswith(":cause") for k in keys)
    detections = firms.detections(
        firms.parse_csv((FIX / "firms_viirs_synthetic.csv").read_text()).records,
        GeoAnchor(id="t", place="Sitio", lat0=40.0, lon0=-4.0, meters_per_block=25),
    )
    assert detections and all("footprint_blocks" not in d for d in detections)
