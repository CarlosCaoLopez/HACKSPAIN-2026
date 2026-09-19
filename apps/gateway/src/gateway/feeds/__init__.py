"""Fuentes reales como hechos con procedencia (SPEC-007). P4.

Patrón: **adaptador**. Cada fuente (Open-Meteo, DGT, FIRMS, AEMET) traduce su formato a
`FactAsserted` y el resto de `vela` no sabe que existe: el core ve un hecho más, con
`source: api:<fuente>:<id>` y su `kind` honesto (invariante 8).

Cada módulo separa dos cosas y no se mezclan:

- `fetch_*`: la E/S. Devuelve bytes o JSON crudos. Es lo único que toca la red.
- `to_facts(...)`: pura. Sin red y sin reloj de pared, así que se prueba con capturas.

Con `VELA_FEEDS=off` (el default) nada de esto arranca.
"""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

from contracts.events import FactAsserted


class Observation(NamedTuple):
    """Un hecho listo para publicar y el momento real al que se refiere.

    `t_real=None` significa «en vivo, publícalo ya». Con fecha, el reloj fechado
    (`clock.Schedule`) lo retiene hasta que el `t_sim` del run llegue a ese instante.
    """

    t_real: datetime | None
    fact: FactAsserted


__all__ = ["Observation"]
