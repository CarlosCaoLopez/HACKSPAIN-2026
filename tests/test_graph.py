"""El grafo de carreteras. Puro: ni Paper, ni bus, ni reloj.

La topología del backbone: carretera en Y con desvío norte y desvío sur. El sur es
más corto, así que es el que sale por defecto; cortarlo obliga al norte. Eso es
literalmente el mecanismo del replan.

    base ── cruce ─┬─ nor1 ── nor2 ─┐
                   └─ sur1 ─────────┴─ pueblo
"""

import pytest

from contracts.scenario import Waypoint
from contracts.world import RoadEdge
from sim.graph import RoadGraph

WAYPOINTS = [
    Waypoint(id="wp_base", x=0, z=0),
    Waypoint(id="wp_cruce", x=100, z=0),
    Waypoint(id="wp_nor_01", x=150, z=-80),
    Waypoint(id="wp_nor_02", x=250, z=-80),
    Waypoint(id="wp_sur_01", x=180, z=60),
    Waypoint(id="wp_pueblo", x=300, z=0),
]
ROADS = [
    RoadEdge(id="rd_base_cruce", a="wp_base", b="wp_cruce", length_m=100),
    RoadEdge(id="rd_cruce_nor1", a="wp_cruce", b="wp_nor_01", length_m=95),
    RoadEdge(id="rd_nor1_nor2", a="wp_nor_01", b="wp_nor_02", length_m=100),
    RoadEdge(id="rd_nor2_pueblo", a="wp_nor_02", b="wp_pueblo", length_m=95),
    RoadEdge(id="rd_cruce_sur1", a="wp_cruce", b="wp_sur_01", length_m=100),
    RoadEdge(id="rd_sur1_pueblo", a="wp_sur_01", b="wp_pueblo", length_m=130),
]


@pytest.fixture
def graph() -> RoadGraph:
    return RoadGraph(WAYPOINTS, ROADS)


def test_la_ruta_corta_es_la_sur(graph):
    assert graph.shortest_path("wp_base", "wp_pueblo") == [
        "wp_base", "wp_cruce", "wp_sur_01", "wp_pueblo",
    ]
    assert graph.route_length_m(graph.shortest_path("wp_base", "wp_pueblo")) == 330


def test_cortar_el_sur_manda_el_camion_por_el_norte(graph):
    """El mecanismo del replan, en una aserción."""
    graph.cut("rd_sur1_pueblo", "árbol caído")
    assert graph.shortest_path("wp_base", "wp_pueblo") == [
        "wp_base", "wp_cruce", "wp_nor_01", "wp_nor_02", "wp_pueblo",
    ]
    assert graph.is_cut("rd_sur1_pueblo")


def test_restore_devuelve_la_ruta_original(graph):
    graph.cut("rd_sur1_pueblo", "árbol caído")
    graph.restore("rd_sur1_pueblo")
    assert graph.shortest_path("wp_base", "wp_pueblo")[2] == "wp_sur_01"


def test_sin_ruta_viva_devuelve_none(graph):
    graph.cut("rd_sur1_pueblo", "fuego")
    graph.cut("rd_nor2_pueblo", "fuego")
    assert graph.shortest_path("wp_base", "wp_pueblo") is None


def test_es_determinista_con_rutas_empatadas():
    """Dos rutas de igual coste devuelven siempre la misma: si no, el mismo
    escenario no se ve igual en dos ensayos."""
    waypoints = [Waypoint(id=f"wp_{n}", x=0, z=0) for n in ("a", "b", "c", "d")]
    roads = [
        RoadEdge(id="rd_ab", a="wp_a", b="wp_b", length_m=50),
        RoadEdge(id="rd_ac", a="wp_a", b="wp_c", length_m=50),
        RoadEdge(id="rd_bd", a="wp_b", b="wp_d", length_m=50),
        RoadEdge(id="rd_cd", a="wp_c", b="wp_d", length_m=50),
    ]
    rutas = {
        tuple(RoadGraph(waypoints, roads).shortest_path("wp_a", "wp_d"))
        for _ in range(50)
    }
    assert len(rutas) == 1


def test_ida_y_vuelta_por_la_misma_carretera(graph):
    """Las aristas son no dirigidas: una carretera se recorre en los dos sentidos."""
    ida = graph.shortest_path("wp_base", "wp_pueblo")
    assert graph.shortest_path("wp_pueblo", "wp_base") == list(reversed(ida))


def test_el_mismo_waypoint_es_ruta_de_un_paso(graph):
    assert graph.shortest_path("wp_base", "wp_base") == ["wp_base"]


def test_position_of_da_coordenadas_del_mundo(graph):
    assert graph.position_of("wp_pueblo") == (300.0, 0.0)


def test_ids_desconocidos_fallan_claro(graph):
    with pytest.raises(ValueError, match="no existe el waypoint"):
        graph.shortest_path("wp_base", "wp_fantasma")
    with pytest.raises(ValueError, match="no existe la arista"):
        graph.cut("rd_fantasma", "x")


def test_una_arista_huerfana_falla_al_construir():
    """Un YAML malo falla aquí y no a los tres minutos de demo."""
    with pytest.raises(ValueError, match="no es un waypoint"):
        RoadGraph(
            [Waypoint(id="wp_a", x=0, z=0)],
            [RoadEdge(id="rd_x", a="wp_a", b="wp_no_existe", length_m=10)],
        )
