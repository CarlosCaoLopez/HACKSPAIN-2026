"""Open-Meteo como viento `inferred` (SPEC-007, F2 · REQ-242…245). P4.

Lo que se protege:

- **Que sea `inferred`, siempre.** Es salida de un modelo, no una estación: `observed`
  sería justo el disfraz que prohíbe la invariante 8.
- **La conversión de unidades**, que es el número que se ve en la brújula. Un factor mal
  puesto da un viento que el sim no puede seguir y nadie lo nota.
- **El umbral del giro**: es lo que decide si el viento real dispara un replan.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gateway.feeds import FeedContext
from gateway.feeds.anchor import GeoAnchor
from gateway.feeds.open_meteo import (
    WindSample,
    parse_current,
    parse_hourly,
    speed_cells_per_min,
    to_facts,
    turn_deg,
)

FIXTURE = Path("fixtures/feeds/_test/open_meteo_hourly.json")


def anchor(**over) -> GeoAnchor:
    base = {
        "id": "t",
        "place": "Sitio",
        "lat0": 40.0,
        "lon0": -4.0,
        "meters_per_block": 25,
    }
    return GeoAnchor(**{**base, **over})


def sample(bearing: float, speed_ms: float = 5.0, hour: int = 12) -> WindSample:
    return WindSample(datetime(2025, 8, 14, hour, tzinfo=UTC), speed_ms, bearing)


def by_key(obs) -> dict:
    return {o.fact.key: o.fact for o in obs}


# --- parseo ---------------------------------------------------------------------------


def test_la_serie_real_se_lee_entera_y_en_utc():
    parsed = parse_hourly(json.loads(FIXTURE.read_text()))
    assert parsed.malformed == 0 and len(parsed.records) == 24
    first = parsed.records[0]
    assert first.t == datetime(2025, 8, 14, 0, tzinfo=UTC)
    assert (first.bearing_deg, first.speed_ms) == (49.0, 1.13)


def test_los_null_del_final_de_un_forecast_no_son_datos_rotos():
    data = {
        "hourly": {
            "time": ["2025-08-14T00:00", "2025-08-14T01:00"],
            "wind_speed_10m": [3.0, None],
            "wind_direction_10m": [90, None],
        }
    }
    parsed = parse_hourly(data)
    assert len(parsed.records) == 1 and parsed.malformed == 0


def test_una_hora_ilegible_se_cuenta_y_no_tumba_las_demas():
    data = {
        "hourly": {
            "time": ["no-es-una-hora", "2025-08-14T01:00"],
            "wind_speed_10m": [3.0, 3.0],
            "wind_direction_10m": [90, 90],
        }
    }
    parsed = parse_hourly(data)
    assert len(parsed.records) == 1 and parsed.malformed == 1


def test_una_respuesta_sin_hourly_lanza_con_un_mensaje_legible():
    with pytest.raises(ValueError, match="hourly"):
        parse_hourly({"error": True, "reason": "cupo agotado"})


def test_current_da_una_muestra():
    data = {
        "current": {
            "time": "2026-09-19T08:45",
            "wind_speed_10m": 1.32,
            "wind_direction_10m": 81,
        }
    }
    parsed = parse_current(data)
    assert parsed.records == [
        WindSample(datetime(2026, 9, 19, 8, 45, tzinfo=UTC), 1.32, 81.0)
    ]


# --- unidades ---------------------------------------------------------------------------


def test_5_ms_con_25_metros_por_bloque_dan_0_3_celdas_por_minuto():
    # 5 m/s · 10 % = 0,5 m/s de frente de fuego = 30 m/min = 1,2 bloques/min = 0,3 celdas/min
    assert speed_cells_per_min(5.0, anchor(), cell_size=4) == pytest.approx(0.3)


def test_el_giro_se_mide_por_el_camino_corto():
    assert turn_deg(350, 10) == 20
    assert turn_deg(10, 350) == 20
    assert turn_deg(0, 180) == 180
    assert turn_deg(90, 90) == 0


# --- hechos -----------------------------------------------------------------------------


def test_siempre_inferred_con_confianza_0_8_y_procedencia():
    parsed = parse_hourly(json.loads(FIXTURE.read_text()))
    obs = to_facts(parsed.records, anchor(), FeedContext())
    assert obs, "la serie real cambia de rumbo: tiene que salir algo"
    for o in obs:
        assert o.fact.kind == "inferred"
        assert o.fact.confidence == 0.8
        assert o.fact.key in ("wind:bearing_deg", "wind:speed")
        assert o.fact.source.startswith("api:open-meteo:2025-08-14T")
        assert o.t_real is not None


def test_el_primer_viento_sale_completo_y_no_es_critico():
    facts = by_key(to_facts([sample(270)], anchor(), FeedContext()))
    assert set(facts) == {"wind:bearing_deg", "wind:speed"}
    assert facts["wind:bearing_deg"].severity == "low"  # sin viento previo no hay giro
    assert facts["wind:bearing_deg"].value == 270.0
    assert facts["wind:speed"].value == pytest.approx(0.3)


def test_un_giro_de_90_grados_es_critico():
    obs = to_facts([sample(270, hour=12), sample(0, hour=13)], anchor(), FeedContext())
    bearings = [o.fact for o in obs if o.fact.key == "wind:bearing_deg"]
    assert [b.severity for b in bearings] == ["low", "critical"]


def test_el_umbral_critico_es_60_y_esta_incluido():
    ctx = FeedContext()
    to_facts([sample(100)], anchor(), ctx)
    assert (
        by_key(to_facts([sample(159)], anchor(), ctx))["wind:bearing_deg"].severity
        == "low"
    )
    ctx = FeedContext()
    to_facts([sample(100)], anchor(), ctx)
    assert (
        by_key(to_facts([sample(160)], anchor(), ctx))["wind:bearing_deg"].severity
        == "critical"
    )


def test_un_giro_de_3_grados_no_se_publica():
    ctx = FeedContext()
    to_facts([sample(270)], anchor(), ctx)
    assert to_facts([sample(273)], anchor(), ctx) == []


def test_un_giro_de_5_grados_si_se_publica_y_no_es_critico():
    ctx = FeedContext()
    to_facts([sample(270)], anchor(), ctx)
    facts = by_key(to_facts([sample(275)], anchor(), ctx))
    assert set(facts) == {"wind:bearing_deg"}  # el módulo no cambió
    assert facts["wind:bearing_deg"].severity == "low"


def test_solo_cambia_la_velocidad_solo_sale_la_velocidad():
    ctx = FeedContext()
    to_facts([sample(270, speed_ms=5.0)], anchor(), ctx)
    facts = by_key(to_facts([sample(270, speed_ms=8.0)], anchor(), ctx))
    assert set(facts) == {"wind:speed"}


def test_la_misma_muestra_dos_ciclos_seguidos_no_se_republica():
    ctx = (
        FeedContext()
    )  # en vivo: cada ciclo trae la misma hora hasta que cambia el modelo
    assert to_facts([sample(270)], anchor(), ctx)
    assert to_facts([sample(270)], anchor(), ctx) == []


def test_el_giro_se_mide_contra_lo_ultimo_publicado_y_no_contra_la_muestra_anterior():
    ctx = FeedContext()
    to_facts([sample(0)], anchor(), ctx)
    to_facts(
        [sample(3)], anchor(), ctx
    )  # no se publica: el último publicado sigue siendo 0
    facts = by_key(to_facts([sample(65)], anchor(), ctx))
    assert facts["wind:bearing_deg"].severity == "critical"  # 65 desde 0, no 62 desde 3
