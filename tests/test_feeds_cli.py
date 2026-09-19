"""`python -m gateway.feeds --probe/--capture` (SPEC-007, F5 · REQ-268/269). P4.

La sonda es lo que se ejecuta al llegar a la sala. Lo que se protege:

- **Que no mienta**: sale con 1 si alguna fuente falla, y también si no había ninguna que
  probar. Una sonda que dice «bien» sin haber probado nada es peor que no tenerla.
- **Que `--probe` no escriba nada** y `--capture` sí: mezclar los dos ensucia `fixtures/`.
- **Que el resumen sirva para elegir el ancla**: cuántos focos hay y si el viento gira.
"""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from gateway.feeds.__main__ import parse_override, probe
from gateway.feeds.anchor import EdgeRef, GeoAnchor

FIX = Path("fixtures/feeds/_test")
DGT = (FIX / "dgt_sample.xml").read_bytes()
FIRMS = (FIX / "firms_viirs_synthetic.csv").read_bytes()
CURRENT = b'{"current":{"time":"2026-09-19T08:45","wind_speed_10m":5.0,"wind_direction_10m":270}}'


def anchor(**over) -> GeoAnchor:
    base = {
        "id": "t",
        "place": "Sitio de prueba",
        "lat0": 40.0,
        "lon0": -4.0,
        "meters_per_block": 25,
        "edges": {"road:wp_a-wp_b": EdgeRef(road_name="A-8005", pk_from=1.0, pk_to=2.5)},
    }
    return GeoAnchor(**{**base, **over})


def client(routes: dict[str, bytes | Exception]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        for needle, answer in routes.items():
            if needle in str(request.url):
                if isinstance(answer, Exception):
                    raise answer
                return httpx.Response(200, content=answer, request=request)
        return httpx.Response(404, request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def run_probe(tmp_path: Path, routes, *, capture=False, a=None, **kw) -> tuple[int, str]:
    lines: list[str] = []
    code = await probe(
        a or anchor(),
        "wildfire_ridge",
        capture=capture,
        client=client(routes),
        feeds_dir=tmp_path,
        firms_key=kw.pop("firms_key", ""),
        aemet_key=kw.pop("aemet_key", ""),
        out=lines.append,
        **kw,
    )
    return code, "\n".join(lines)


# --- el override ----------------------------------------------------------------------------


def test_override_con_y_sin_fecha():
    assert parse_override("40.5,-4.2") == (40.5, -4.2, None)
    assert parse_override("40.5, -4.2, 2025-08-14") == (40.5, -4.2, datetime(2025, 8, 14, tzinfo=UTC))


@pytest.mark.parametrize("bad", ["40.5", "a,b", "91,0", "40,-4,ayer", "1,2,3,4"])
def test_override_mal_formado_lanza_con_un_mensaje(bad: str):
    with pytest.raises(ValueError):
        parse_override(bad)


# --- la sonda ---------------------------------------------------------------------------------


async def test_todo_responde_sale_con_0_e_imprime_los_hechos_que_publicaria(tmp_path: Path):
    code, out = await run_probe(tmp_path, {"nap.dgt.es": DGT, "open-meteo": CURRENT})
    assert code == 0, out
    assert "road:wp_a-wp_b:cut = True" in out and "wind:bearing_deg = 270.0" in out
    assert "inferred 0.80" in out and "observed 0.90" in out  # el kind honesto se ve
    assert "hechos que se publicarían (4)" in out  # 2 de DGT y 2 de viento


async def test_probe_no_escribe_nada_y_capture_guarda_lo_crudo(tmp_path: Path):
    await run_probe(tmp_path, {"nap.dgt.es": DGT, "open-meteo": CURRENT}, capture=False)
    assert list(tmp_path.rglob("*")) == [], "--probe no puede dejar nada en disco"

    await run_probe(tmp_path, {"nap.dgt.es": DGT, "open-meteo": CURRENT}, capture=True)
    assert (next((tmp_path / "t" / "dgt").glob("*.xml"))).read_bytes() == DGT
    assert len(list((tmp_path / "t" / "open_meteo").glob("*.json"))) == 1


async def test_una_fuente_caida_hace_que_la_sonda_salga_con_1_y_diga_cual(tmp_path: Path):
    code, out = await run_probe(
        tmp_path, {"nap.dgt.es": httpx.ConnectError("sin red"), "open-meteo": CURRENT}
    )
    assert code == 1
    assert "dgt         degraded" in out and "sin red" in out
    assert "open_meteo  ok" in out  # y lo que sí funcionó se sigue viendo


async def test_sin_ninguna_fuente_activa_no_dice_que_va_bien(tmp_path: Path):
    code, out = await run_probe(tmp_path, {}, a=anchor(fixed=False))
    assert code == 1
    assert "ninguna fuente activa" in out and "ancla sin fijar" in out


async def test_el_resumen_del_ancla_cuenta_focos_y_giros_de_viento(tmp_path: Path):
    """Es lo que decide qué incendio real se elige: >= 5 focos n/h y un giro visible."""
    dated = anchor(reference_start=datetime(2025, 8, 14, tzinfo=UTC), edges={})
    hourly = (FIX / "open_meteo_hourly.json").read_bytes()
    code, out = await run_probe(
        tmp_path, {"firms.modaps": FIRMS, "open-meteo": hourly}, a=dated, firms_key="k"
    )
    assert code == 0, out
    assert "focos FIRMS en el cuadro: 4 (n/h: 3) · en la rejilla del valle: 3" in out
    # La serie real de ese día tiene un giro de verdad: 135 -> 50 grados a las 21:00Z.
    assert "giros de viento >= 60 grados: 1" in out
