"""Fuentes reales como hechos con procedencia (SPEC-007). P4.

Patrón: **adaptador**. Cada fuente (Open-Meteo, DGT, FIRMS) traduce su formato a
`FactAsserted` y el resto de `vela` no sabe que existe: el core ve un hecho más, con
`source: api:<fuente>:<id>` y su `kind` honesto (invariante 8).

Cada módulo separa dos cosas y no se mezclan:

- `fetch_*`: la E/S. Devuelve bytes o JSON crudos. Es lo único que toca la red.
- `to_facts(...)`: pura. Sin red y sin reloj de pared, así que se prueba con capturas.

Con `VELA_FEEDS=off` (el default) nada de esto arranca.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import NamedTuple

from contracts.events import FactAsserted
from contracts.world import Cell


class Observation(NamedTuple):
    """Un hecho listo para publicar y el momento real al que se refiere.

    `t_real=None` significa «en vivo, publícalo ya». Con fecha, el reloj fechado
    (`clock.Schedule`) lo retiene hasta que el `t_sim` del run llegue a ese instante.
    """

    t_real: datetime | None
    fact: FactAsserted


class Parsed[T](NamedTuple):
    """Lo que devuelve un `parse_*`: los registros buenos y cuántos venían rotos.

    Un payload entero inválido lanza. Una fila rota dentro de uno válido se cuenta en
    `malformed` y no impide leer las demás: quien llama decide si en desarrollo eso es un
    error (REQ-258) o en la demo solo un contador.
    """

    records: list[T]
    malformed: int


@dataclass
class FeedContext:
    """Lo que un `to_facts` necesita saber del mundo y de lo que ya publicó.

    Las fuentes lo rellenan desde `WorldState` y el plan en cada ciclo. `to_facts` no hace
    E/S ni mira el reloj de pared; lo único que muta de aquí es la deduplicación
    (`seen`, `last_wind`), que es justo lo que no puede vivir fuera sin que dos ciclos
    publiquen el mismo hecho dos veces.
    """

    cells: Mapping[str, Cell] = field(default_factory=dict)
    origin_cell: str = ""
    cell_size: int = 4
    route_edges: frozenset[str] = frozenset()  # ids de aristas que recorre el plan vigente
    last_wind: dict[str, float] = field(default_factory=dict)  # lo último publicado
    seen: set[str] = field(default_factory=set)  # `<fuente>:<id>` ya publicados


class EnvironmentInfo(NamedTuple):
    """Un tipo de dato del entorno que **Minecraft y las fuentes reales pueden mostrar**."""

    fact_key: str  # plantilla, como en `contracts.factkeys.FACT_KEYS`
    sim_input: str  # lo que del lado de Minecraft expresa el mismo dato
    real_source: str


# La correspondencia 1 a 1 entre Minecraft y las APIs (SPEC-007 REQ-311…313). Son dos fuentes
# separadas de la misma clase de información: las fuentes reales NO escriben en Minecraft, pero
# solo pueden traer tipos de dato que Minecraft también sabe mostrar. Añadir una clave aquí sin
# su contraparte del sim, o un inject al sim sin decidir qué pasa de este lado, rompe
# `tests/test_feeds_vocabulary.py`.
ENVIRONMENT: tuple[EnvironmentInfo, ...] = (
    EnvironmentInfo("wind:bearing_deg", "wind_shift", "open_meteo"),
    EnvironmentInfo("wind:speed", "wind_shift", "open_meteo"),
    EnvironmentInfo("road:<edge_id>:cut", "road_cut", "dgt"),
    EnvironmentInfo("cell:<cell_id>:state", "burning", "firms"),  # un `CellState`, no un inject
)

# Lo que Minecraft acepta como entrada y ninguna API real puede dar, con el motivo.
SIM_ONLY: dict[str, str] = {
    "unit_failure": "la avería de una unidad de la flota de vela: ninguna API real la da",
}

__all__ = ["ENVIRONMENT", "SIM_ONLY", "EnvironmentInfo", "FeedContext", "Observation", "Parsed"]
