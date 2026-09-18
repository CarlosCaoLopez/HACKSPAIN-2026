"""La geometría provisional del escenario. P4. **Se apaga sola.**

`scenarios/*.yaml` es de P2 y hoy tiene `pois`, `units`, `waypoints`, `roads` y
`civilians` a `[]`. Sin coordenadas de waypoint no hay flechas de asignación, y las
flechas cambiando de destino son el clímax visual del dashboard: por eso esto existe.

Dos reglas que lo hacen honesto:

- **Relleno por lista y solo si viene vacía** (`fill`). Un dato de P2 nunca se
  sustituye por uno inventado; si rellena solo los POIs, se sirven los suyos y se
  completan los waypoints.
- **Lo que se ha rellenado se dice**: `fill` devuelve la lista de listas rellenadas, el
  endpoint la publica como `provisional`, y el mapa lo enseña. Un mapa inventado
  presentado como real es peor que un mapa vacío: si en la demo alguien pregunta por el
  pueblo y no existe, se cae el pitch.

Es también **la única fuente de ids y coordenadas** de la maqueta: `scripts/fake_journal.py`
importa de aquí en vez de declarar las suyas, o el fixture y el mapa se separan sin que
nadie se entere hasta la demo.
"""

from __future__ import annotations

import math

from contracts.scenario import Scenario, Waypoint
from contracts.world import POI, CivilianGroup, RoadEdge, Unit

# --- Waypoints ------------------------------------------------------------------
#
# Coordenadas del mundo Minecraft (x, z), con y implícita. La carretera en Y del
# backbone: base al oeste, un ramal sur que baja a Pueblo A y un ramal norte que sube a
# Pueblo B (el que se corta en el inject de t=210).

WAYPOINT_XZ: dict[str, tuple[float, float]] = {
    "wp_base": (100.0, 20.0),
    "wp_sur_01": (140.0, 60.0),
    "wp_sur_02": (180.0, 80.0),
    "wp_sur_03": (220.0, 100.0),
    "wp_sur_04": (260.0, 120.0),
    "wp_norte_01": (140.0, -20.0),
    "wp_norte_02": (180.0, -40.0),
    "wp_norte_03": (220.0, -60.0),
}

ROUTE_SUR = ["wp_base", "wp_sur_01", "wp_sur_02", "wp_sur_03", "wp_sur_04"]
ROUTE_NORTE = ["wp_base", "wp_norte_01", "wp_norte_02", "wp_norte_03"]

# --- Ids ------------------------------------------------------------------------
#
# Con la convención de prefijos de CLAUDE.md. `unit_truck2` y
# `wp_norte_02-wp_norte_03` salen del YAML de P2 (sus injects ya los nombran), así que
# esos dos no son inventados: son los que el escenario real va a usar.

TRUCK1 = "unit_truck1"
TRUCK2 = "unit_truck2"  # el que se avería en el inject de t=240
AMBULANCE = "unit_ambulance1"
DRONE = "unit_drone1"

PUEBLO_A = "poi_pueblo_a"
PUEBLO_B = "poi_pueblo_b"
HOSPITAL = "poi_hospital"
REFUGIO = "poi_refugio"
BASE = "poi_base"

CIV_A = "civ_pueblo_a"
CIV_B = "civ_pueblo_b"

ROAD_NORTE = "wp_norte_02-wp_norte_03"


def _edge(a: str, b: str) -> RoadEdge:
    """Una arista entre dos waypoints, con el id `<a>-<b>` que usan los eventos.

    `world.road.changed` trae solo el `edge_id`, así que el id **es** la forma de
    encontrar sus dos extremos en el mapa: si el formato cambia, el corte de carretera
    deja de dibujarse.
    """
    return RoadEdge(
        id=f"{a}-{b}",
        a=a,
        b=b,
        length_m=round(math.dist(WAYPOINT_XZ[a], WAYPOINT_XZ[b]), 1),
    )


def _chain(route: list[str]) -> list[RoadEdge]:
    return [_edge(a, b) for a, b in zip(route, route[1:])]


WAYPOINTS: list[Waypoint] = [
    Waypoint(id=wp, x=x, z=z) for wp, (x, z) in WAYPOINT_XZ.items()
]

POIS: list[POI] = [
    POI(
        id=PUEBLO_A,
        name="Pueblo A",  # lo que dice el agente por teléfono
        kind="village",
        x=270.0,
        z=125.0,
        waypoint_id="wp_sur_04",
        contact_phone="+34600111222",
    ),
    POI(
        id=PUEBLO_B,
        name="Pueblo B",
        kind="village",
        x=230.0,
        z=-65.0,
        waypoint_id="wp_norte_03",
        contact_phone="+34600111333",
    ),
    POI(
        id=HOSPITAL,
        name="Hospital comarcal",
        kind="hospital",
        x=60.0,
        z=10.0,
        waypoint_id="wp_base",
        min_coverage=1,  # la restricción `hospital_min_coverage:1` del catálogo
    ),
    POI(id=REFUGIO, name="Polideportivo", kind="shelter", x=20.0, z=60.0, waypoint_id="wp_base"),
    POI(id=BASE, name="Base de bomberos", kind="base", x=100.0, z=20.0, waypoint_id="wp_base"),
]

ROADS: list[RoadEdge] = _chain(ROUTE_SUR) + _chain(ROUTE_NORTE)

UNITS: list[Unit] = [
    Unit(
        id=TRUCK1,
        kind="fire_truck",
        x=100.0,
        z=20.0,
        capabilities=["extinguish", "transport"],
        capacity=6,
    ),
    Unit(
        id=TRUCK2, kind="fire_truck", x=100.0, z=20.0, capabilities=["extinguish"], capacity=4
    ),
    Unit(
        id=AMBULANCE,
        kind="ambulance",
        x=100.0,
        z=20.0,
        capabilities=["transport", "medical"],
        capacity=2,
    ),
    Unit(id=DRONE, kind="drone", x=100.0, z=20.0, capabilities=["recon"]),
]

CIVILIANS: list[CivilianGroup] = [
    CivilianGroup(id=CIV_A, poi_id=PUEBLO_A, count=18, immobile=2),
    CivilianGroup(id=CIV_B, poi_id=PUEBLO_B, count=6),
]

FILLABLE = ("pois", "waypoints", "roads", "units", "civilians")
"""Las cinco listas de `Scenario` que esto sabe rellenar. `injects` no: los tiene el
YAML y son de P2."""


def fill(sc: Scenario) -> tuple[Scenario, list[str]]:
    """El escenario completado y qué listas se rellenaron.

    Lista por lista: lo que P2 haya declarado se respeta siempre. `WorldState` es
    inmutable por contrato y `Scenario` se trata igual aquí — se devuelve una copia con
    `model_copy`, no se muta el original.
    """
    provisional = [name for name in FILLABLE if not getattr(sc, name)]
    if not provisional:
        return sc, []
    defaults = {
        "pois": POIS,
        "waypoints": WAYPOINTS,
        "roads": ROADS,
        "units": UNITS,
        "civilians": CIVILIANS,
    }
    return sc.model_copy(update={name: defaults[name] for name in provisional}), provisional
