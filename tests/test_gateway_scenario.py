"""La capa estática del escenario: `GET /api/scenario` y el relleno provisional.

El mapa del dashboard no puede dibujar rutas ni cortes de carretera solo con eventos:
`world.road.changed` trae un `edge_id` y nada más, y las celdas necesitan `origin` y
`cell_size`. Todo eso lo sirve este endpoint.

El test que de verdad importa es el último: **los ids de la capa son los que usa el
fixture**. Si alguien renombra un waypoint en un sitio y no en el otro, el camión se
mueve hacia algo que no existe, el mapa lo pinta en el carril *sin ubicar*, y eso se
descubre en la demo. Aquí salta antes.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contracts.scenario import Scenario
from contracts.world import POI
from gateway.main import app
from gateway.scenario_fallback import FILLABLE, WAYPOINT_XZ, fill
from gateway.scenarios import load_scenario

FAKE = Path("fixtures/run_fake.jsonl")
FAKE_V2 = Path("fixtures/run_fake_v2.jsonl")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_sirve_la_geometria_que_el_mapa_necesita(client: TestClient) -> None:
    body = client.get("/api/scenario").json()
    assert body["id"] == "wildfire_ridge"
    assert body["origin"] == [0.0, 0.0]
    assert body["hazard"]["cell_size"] == 4  # sin esto no se proyecta una celda
    assert len(body["waypoints"]) == len(WAYPOINT_XZ)
    assert {p["id"] for p in body["pois"]} >= {"poi_pueblo_a", "poi_pueblo_b"}


def test_dice_que_es_provisional(client: TestClient) -> None:
    """Mientras P2 tenga las listas a `[]`, el mapa tiene que avisarlo en pantalla."""
    body = client.get("/api/scenario").json()
    assert body["provisional"] is True
    assert set(body["provisional_lists"]) == set(FILLABLE)
    assert body["of_run"] is None  # sin run en curso, es el escenario por defecto


def test_escenario_por_id_y_404(client: TestClient) -> None:
    assert client.get("/api/scenario/blackout_grid").json()["id"] == "blackout_grid"
    assert client.get("/api/scenario/no_existe").status_code == 404


def test_el_relleno_no_pisa_los_datos_de_p2() -> None:
    """Lista por lista: lo que declare el escenario manda siempre."""
    sc = load_scenario("wildfire_ridge")
    mio = POI(id="poi_suyo", name="El de P2", kind="village", x=1.0, z=2.0, waypoint_id="wp_x")
    con_pois = sc.model_copy(update={"pois": [mio]})

    filled, provisional = fill(con_pois)

    assert filled.pois == [mio], "un POI de P2 no se sustituye por uno inventado"
    assert "pois" not in provisional
    assert filled.waypoints, "las listas que sí estaban vacías se completan"
    assert "waypoints" in provisional


def test_un_escenario_completo_no_es_provisional() -> None:
    sc = load_scenario("wildfire_ridge")
    completo, _ = fill(sc)  # relleno una vez...
    _, provisional = fill(completo)  # ...y ya no hay nada que rellenar
    assert provisional == []


@pytest.mark.parametrize("fixture", [FAKE, FAKE_V2])
def test_los_ids_del_fixture_existen_en_la_capa(fixture: Path) -> None:
    """Una sola fuente de ids: si el fixture y la capa se separan, el mapa miente."""
    if not fixture.exists():
        pytest.skip(f"falta {fixture}")
    scenario: Scenario = fill(load_scenario("wildfire_ridge"))[0]
    units = {u.id for u in scenario.units}
    pois = {p.id for p in scenario.pois}
    waypoints = {w.id for w in scenario.waypoints}
    edges = {r.id for r in scenario.roads}

    referenced: dict[str, set[str]] = {"unit": set(), "poi": set(), "wp": set(), "edge": set()}
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

    assert referenced["unit"] <= units, f"unidades sin declarar: {referenced['unit'] - units}"
    assert referenced["poi"] <= pois, f"POIs sin declarar: {referenced['poi'] - pois}"
    assert referenced["wp"] <= waypoints, f"waypoints sin coordenadas: {referenced['wp'] - waypoints}"
    assert referenced["edge"] <= edges, f"aristas sin geometría: {referenced['edge'] - edges}"
