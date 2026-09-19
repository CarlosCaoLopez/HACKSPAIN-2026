"""La capa estática del escenario: `GET /api/scenario`.

El mapa del dashboard no puede dibujar rutas ni cortes de carretera solo con eventos:
`world.road.changed` trae un `edge_id` y nada más, y las celdas necesitan `origin` y
`cell_size`. Todo eso lo sirve este endpoint, tal cual lo declara P2 en el YAML.

El test que de verdad importa es el último: **los ids de los fixtures son los del YAML**.
Si alguien renombra un waypoint o una carretera en un sitio y no en el otro, el camión se
mueve hacia algo que no existe, el mapa lo pinta en el carril *sin ubicar*, y eso se
descubre en la demo. Aquí salta antes.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contracts.scenario import Scenario
from gateway.main import app
from gateway.scenarios import load_scenario

# Solo el v3: los v1 y v2 están congelados con los ids de carretera de antes del renombrado
# a `road:wp_a-wp_b` (`fixtures/**` solo se añade) y por diseño ya no casan con el YAML.
FAKE_V3 = Path("fixtures/run_fake_v3.jsonl")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_sirve_la_geometria_que_el_mapa_necesita(client: TestClient) -> None:
    body = client.get("/api/scenario").json()
    sc = load_scenario("wildfire_ridge")
    assert body["id"] == "wildfire_ridge"
    assert body["origin"] == [0.0, 0.0]
    assert body["hazard"]["cell_size"] == 4  # sin esto no se proyecta una celda
    assert {w["id"] for w in body["waypoints"]} == {w.id for w in sc.waypoints}
    assert {r["id"] for r in body["roads"]} == {r.id for r in sc.roads}
    assert {p["id"] for p in body["pois"]} >= {"poi_pueblo_a", "poi_pueblo_b"}


def test_sirve_lo_que_dice_p2_sin_rellenar_nada(client: TestClient) -> None:
    """Sin relleno provisional: lo que sale es el YAML, y el mapa no avisa de nada."""
    body = client.get("/api/scenario").json()
    assert "provisional" not in body
    assert body["of_run"] is None  # sin run en curso, es el escenario por defecto


def test_escenario_por_id_y_404(client: TestClient) -> None:
    assert client.get("/api/scenario/blackout_grid").json()["id"] == "blackout_grid"
    assert client.get("/api/scenario/no_existe").status_code == 404


def test_los_escenarios_de_p2_traen_la_geometria_completa() -> None:
    """Si P2 vacía una lista, el mapa se queda sin flechas y sin cortes: que salte aquí."""
    for scenario_id in ("wildfire_ridge", "blackout_grid"):
        sc = load_scenario(scenario_id)
        for name in ("pois", "waypoints", "roads", "units", "civilians"):
            assert getattr(sc, name), f"{scenario_id}: `{name}` viene vacío"


@pytest.mark.parametrize("fixture", [FAKE_V3])
def test_los_ids_del_fixture_existen_en_el_escenario(fixture: Path) -> None:
    """Una sola fuente de ids: si el fixture y el YAML se separan, el mapa miente."""
    if not fixture.exists():
        pytest.skip(f"falta {fixture}")
    scenario: Scenario = load_scenario("wildfire_ridge")
    units = {u.id for u in scenario.units}
    pois = {p.id for p in scenario.pois}
    waypoints = {w.id for w in scenario.waypoints}
    edges = {r.id for r in scenario.roads}

    referenced: dict[str, set[str]] = {
        "unit": set(),
        "poi": set(),
        "wp": set(),
        "edge": set(),
    }
    for line in fixture.read_text(encoding="utf-8").splitlines():
        ev = json.loads(line)
        payload, type_ = ev["payload"], ev["type"]
        if "unit_id" in payload:
            referenced["unit"].add(payload["unit_id"])
        if "poi_id" in payload:
            referenced["poi"].add(payload["poi_id"])
        if type_ == "world.unit.arrived":
            referenced["wp"].add(payload["waypoint_id"])
        if type_ == "world.road.changed":
            referenced["edge"].add(payload["edge_id"])
        if type_ == "plan.emitted":
            for a in payload["assignments"]:
                referenced["wp"].update(a["route"])

    assert referenced["unit"] <= units, (
        f"unidades sin declarar: {referenced['unit'] - units}"
    )
    assert referenced["poi"] <= pois, f"POIs sin declarar: {referenced['poi'] - pois}"
    assert referenced["wp"] <= waypoints, (
        f"waypoints sin coordenadas: {referenced['wp'] - waypoints}"
    )
    assert referenced["edge"] <= edges, (
        f"aristas sin geometría: {referenced['edge'] - edges}"
    )
