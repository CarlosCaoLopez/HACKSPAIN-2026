"""Interpolación a 5 Hz. Pura: ni RCON, ni bus, ni Paper levantado."""

import math

import pytest

from contracts.scenario import Waypoint
from contracts.world import RoadEdge
from sim.graph import RoadGraph
from sim.movement import TICK_HZ, Movement, interpolate, tp_command, yaw_between

# Una recta de 300 m hacia el este, partida en dos tramos.
WAYPOINTS = [
    Waypoint(id="wp_a", x=0, z=0),
    Waypoint(id="wp_b", x=100, z=0),
    Waypoint(id="wp_c", x=300, z=0),
]
ROADS = [
    RoadEdge(id="rd_ab", a="wp_a", b="wp_b", length_m=100),
    RoadEdge(id="rd_bc", a="wp_b", b="wp_c", length_m=200),
]
RUTA = ["wp_a", "wp_b", "wp_c"]


@pytest.fixture
def graph() -> RoadGraph:
    return RoadGraph(WAYPOINTS, ROADS)


def test_avanza_a_velocidad_constante(graph):
    m = Movement("unit_truck1", RUTA, speed_mps=10, graph=graph)
    assert m.total_m == 300
    x, z, _ = m.step(1.0)
    assert (round(x), round(z)) == (10, 0)
    m.step(4.0)
    assert round(m.position()[0]) == 50


def test_cruza_el_vertice_sin_saltos(graph):
    """El punto de unión entre dos tramos no puede teletransportar al camión."""
    m = Movement("unit_truck1", RUTA, speed_mps=10, graph=graph)
    m.step(9.9)
    antes = m.position()[0]
    m.step(0.2)
    assert antes < m.position()[0] < antes + 3


def test_no_se_pasa_del_destino(graph):
    m = Movement("unit_truck1", RUTA, speed_mps=10, graph=graph)
    m.step(1000)
    assert m.done and m.eta_s == 0
    assert m.position()[:2] == (300.0, 0.0)


def test_eta_baja_conforme_avanza(graph):
    m = Movement("unit_truck1", RUTA, speed_mps=10, graph=graph)
    assert m.eta_s == 30
    m.step(10)
    assert m.eta_s == 20


def test_interpolate_da_5_puntos_por_segundo(graph):
    puntos = list(interpolate(RUTA, graph, speed_mps=10))
    # 300 m a 10 m/s = 30 s = 150 pasos, más el punto de salida.
    assert len(puntos) == 30 * TICK_HZ + 1
    assert puntos[0][:2] == (0.0, 0.0)
    assert puntos[-1][:2] == (300.0, 0.0)


def test_interpolate_es_determinista(graph):
    assert list(interpolate(RUTA, graph, 10)) == list(interpolate(RUTA, graph, 10))


def test_ruta_de_un_solo_waypoint_no_revienta(graph):
    """`goto` al sitio donde la unidad ya está: ruta válida, cero metros."""
    m = Movement("unit_truck1", ["wp_a"], speed_mps=10, graph=graph)
    assert m.done and m.eta_s == 0
    assert m.position() == (0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    "destino, yaw",
    [((0, 10), 0), ((-10, 0), 90), ((0, -10), 180), ((10, 0), -90)],
)
def test_yaw_sigue_la_convencion_de_minecraft(destino, yaw):
    """0 sur, 90 oeste, 180 norte, -90 este. El `-dx` es el error clásico."""
    assert math.isclose(yaw_between(0, 0, *destino), yaw)


def test_tp_command_lleva_tag_y_limite():
    assert tp_command("unit_truck1", 10.5, -3.25, 64, 90) == (
        "tp @e[tag=unit_truck1,limit=1] 10.50 64.00 -3.25 90.0 0"
    )


def test_rutas_y_velocidades_invalidas_fallan(graph):
    with pytest.raises(ValueError, match="ruta vacía"):
        Movement("unit_truck1", [], 10, graph)
    with pytest.raises(ValueError, match="velocidad no positiva"):
        Movement("unit_truck1", RUTA, 0, graph)
