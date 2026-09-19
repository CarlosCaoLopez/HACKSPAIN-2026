"""El ancla: dónde del mundo real está el valle de Minecraft. SPEC-007 · REQ-238…240.

El mundo de `vela` se declara, no se genera: no es un sitio real. Para que un dato real
signifique algo en el grafo hace falta decir qué punto real es el `(0,0)` del mundo, a qué
escala y qué aristas del escenario se corresponden con qué tramos de carretera. Esa
correspondencia es **declarada** y el dashboard la enseña (REQ-265): presentarla como
geografía real sería la misma mentira que prohíbe REQ-066.

Proyección equirectangular alrededor del ancla, sin rotación (+X este, +Z sur, como
Minecraft). Vale para < 50 km, que es de sobra para un valle.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from itertools import pairwise
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from contracts.world import Cell, RoadEdge

log = logging.getLogger("vela.feeds")

ANCHORS_DIR = Path(__file__).parent / "anchors"
EARTH_RADIUS_M = 6_371_008.8


class EdgeRef(BaseModel):
    """Un tramo real de carretera al que se declara equivalente una arista del escenario."""

    road_name: str  # "N-400"
    pk_from: float
    pk_to: float


class GeoAnchor(BaseModel):
    id: str
    place: str  # nombre humano del sitio: es lo que lee la sala
    lat0: float
    lon0: float
    meters_per_block: float = Field(gt=0)
    # `False` = ancla marcador: las fuentes quedan `off`. Existe para que un placeholder
    # con coordenadas inventadas no consulte un punto falso y lo presente como real.
    fixed: bool = True
    reference_start: datetime | None = None  # con fecha = modo fechado; sin ella, en vivo
    time_scale: float = Field(default=60.0, gt=0)  # segundos reales por segundo de t_sim
    spread_ratio: float = 0.10  # el fuego avanza ~10 % de la velocidad del viento
    edges: dict[str, EdgeRef] = {}
    firms_radius_km: float = 10.0
    aemet_area: str = ""
    aemet_zones: list[str] = []
    aemet_events: list[str] = []

    @field_validator("reference_start")
    @classmethod
    def _needs_timezone(cls, value: datetime | None) -> datetime | None:
        # Sin zona horaria, `t_real - reference_start` mezcla naive y aware y revienta
        # en mitad del run. Mejor que reviente al cargar el ancla.
        if value is not None and value.tzinfo is None:
            raise ValueError(
                "reference_start necesita zona horaria (p. ej. 2025-08-14T12:00:00+02:00)"
            )
        return value


def anchor_view(anchor: GeoAnchor) -> dict[str, object]:
    """Lo que el dashboard enseña del ancla (REQ-265): dónde, a qué escala y de qué día.
    `fixed=false` es el marcador: el dashboard lo dice en vez de enseñar un sitio."""
    return {
        "id": anchor.id,
        "place": anchor.place,
        "fixed": anchor.fixed,
        "meters_per_block": anchor.meters_per_block,
        "reference_start": anchor.reference_start.isoformat() if anchor.reference_start else None,
    }


def load_anchor(scenario_id: str) -> GeoAnchor | None:
    """El ancla de un escenario, o `None` si no tiene (las fuentes quedan `off`)."""
    path = ANCHORS_DIR / f"{scenario_id}.yaml"
    if not path.exists():
        return None
    return GeoAnchor.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# --- proyección ----------------------------------------------------------------------


def _meters_per_degree_lon(anchor: GeoAnchor) -> float:
    return math.radians(1) * EARTH_RADIUS_M * math.cos(math.radians(anchor.lat0))


def to_world(anchor: GeoAnchor, lat: float, lon: float) -> tuple[float, float]:
    """`(lat, lon)` → `(x, z)` del mundo Minecraft."""
    north_m = math.radians(lat - anchor.lat0) * EARTH_RADIUS_M
    east_m = (lon - anchor.lon0) * _meters_per_degree_lon(anchor)
    return east_m / anchor.meters_per_block, -north_m / anchor.meters_per_block


def to_geo(anchor: GeoAnchor, x: float, z: float) -> tuple[float, float]:
    """`(x, z)` del mundo → `(lat, lon)`. Inversa exacta de `to_world`."""
    north_m = -z * anchor.meters_per_block
    east_m = x * anchor.meters_per_block
    lat = anchor.lat0 + math.degrees(north_m / EARTH_RADIUS_M)
    lon = anchor.lon0 + east_m / _meters_per_degree_lon(anchor)
    return lat, lon


def bbox(anchor: GeoAnchor) -> tuple[float, float, float, float]:
    """`(oeste, sur, este, norte)` en grados: el cuadro de `firms_radius_km` en torno al
    ancla, en el orden que pide el Area API de FIRMS."""
    radius_m = anchor.firms_radius_km * 1000
    dlat = math.degrees(radius_m / EARTH_RADIUS_M)
    dlon = radius_m / _meters_per_degree_lon(anchor)
    return (anchor.lon0 - dlon, anchor.lat0 - dlat, anchor.lon0 + dlon, anchor.lat0 + dlat)


# --- celdas y aristas ------------------------------------------------------------------

_CELL_ID = re.compile(r"^(?P<prefix>.*?)(?P<cx>-?\d+)_(?P<cz>-?\d+)$")


def _pad_width(part: str) -> int:
    """Ancho de relleno de un índice de celda: `08` y `00` se rellenan a 2; `18` y `7`, no."""
    return len(part) if len(part) > 1 and part.startswith("0") else 1


def cell_id_at(
    x: float,
    z: float,
    cell_size: int,
    cells: Mapping[str, Cell],
    origin_cell: str,
) -> str | None:
    """El id de la celda que contiene `(x, z)`.

    Los escenarios no formatean igual (`cell_18_7` en `wildfire_ridge`, `cell_08_00` en
    `blackout_grid`), así que el id se **busca** por `(cx, cz)` en las celdas del estado.
    Si no está —el estado aún no la tiene—, se formatea imitando el relleno de ceros de
    `origin_cell` y el hecho se publica igual: `belief` decide si la crea.

    `None` si el punto cae fuera de la rejilla (índices negativos): eso no es una celda.
    """
    cx, cz = math.floor(x / cell_size), math.floor(z / cell_size)
    if cx < 0 or cz < 0:
        return None
    for cell in cells.values():
        if cell.cx == cx and cell.cz == cz:
            return cell.id
    match = _CELL_ID.match(origin_cell)
    if match is None:
        log.warning("origin_cell con formato desconocido, uso cell_<cx>_<cz>: %s", origin_cell)
        return f"cell_{cx}_{cz}"
    wx, wz = _pad_width(match["cx"]), _pad_width(match["cz"])
    return f"{match['prefix']}{cx:0{wx}d}_{cz:0{wz}d}"


def edges_on_route(routes: Iterable[Sequence[str]], roads: Iterable[RoadEdge]) -> set[str]:
    """Los ids de las aristas que recorre alguna ruta del plan, en cualquier sentido.

    Lo usa la severidad: un corte sobre una arista que un camión está usando es
    `critical`; sobre una que nadie usa, no.
    """
    by_pair = {frozenset((r.a, r.b)): r.id for r in roads}
    used: set[str] = set()
    for route in routes:
        for a, b in pairwise(route):
            edge = by_pair.get(frozenset((a, b)))
            if edge is not None:
                used.add(edge)
    return used


__all__ = [
    "EdgeRef",
    "GeoAnchor",
    "anchor_view",
    "bbox",
    "cell_id_at",
    "edges_on_route",
    "load_anchor",
    "to_geo",
    "to_world",
]
