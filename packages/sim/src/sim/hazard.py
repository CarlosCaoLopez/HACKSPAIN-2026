"""El autómata del peligro. Tres implementaciones, una interfaz.

`Wildfire` es una rejilla de celdas de 4x4 bloques. Cada tick, una celda `burning`
intenta propagarse a cada vecina con probabilidad
`base * (1 + cos(ángulo entre viento y dirección)) * fuel`.

La semilla del RNG va en el YAML: el mismo escenario produce el mismo incendio en
cada ensayo. Con `doFireTick false` ese fuego no se propaga solo ni quema nada.
"""

from typing import Protocol

from pydantic import BaseModel

from contracts.scenario import HazardSpec
from contracts.world import CellState, Wind


class CellChange(BaseModel):
    """Lo que devuelve un tick del autómata. El runner lo traduce a
    `world.cell.changed` y a comandos de render."""

    cell_id: str
    state: CellState
    hazard: str


class Hazard(Protocol):
    """Interfaz común. `Wildfire | Flood | Blackout` la cumplen."""

    def tick(self, dt: float) -> list[CellChange]: ...

    def cells_at_risk(self, horizon_s: float) -> list[str]: ...

    def set_wind(self, wind: Wind) -> None: ...

    def render_commands(self, change: CellChange) -> list[str]: ...


class Wildfire:
    """`/fill netherrack` + `/fill fire` encima, más humo. `burnt` es `coal_block`,
    que deja una cicatriz negra vista desde arriba."""

    def __init__(self, spec: HazardSpec, seed: int) -> None:
        raise NotImplementedError

    def tick(self, dt: float) -> list[CellChange]:
        raise NotImplementedError

    def cells_at_risk(self, horizon_s: float) -> list[str]:
        raise NotImplementedError

    def set_wind(self, wind: Wind) -> None:
        """Cambiar el viento es cambiar un vector en memoria."""
        raise NotImplementedError

    def render_commands(self, change: CellChange) -> list[str]:
        raise NotImplementedError


class Flood:
    def __init__(self, spec: HazardSpec, seed: int) -> None:
        raise NotImplementedError

    def tick(self, dt: float) -> list[CellChange]:
        raise NotImplementedError

    def cells_at_risk(self, horizon_s: float) -> list[str]:
        raise NotImplementedError

    def set_wind(self, wind: Wind) -> None:
        raise NotImplementedError

    def render_commands(self, change: CellChange) -> list[str]:
        raise NotImplementedError


class Blackout:
    def __init__(self, spec: HazardSpec, seed: int) -> None:
        raise NotImplementedError

    def tick(self, dt: float) -> list[CellChange]:
        raise NotImplementedError

    def cells_at_risk(self, horizon_s: float) -> list[str]:
        raise NotImplementedError

    def set_wind(self, wind: Wind) -> None:
        raise NotImplementedError

    def render_commands(self, change: CellChange) -> list[str]:
        raise NotImplementedError


def build_hazard(spec: HazardSpec, seed: int) -> Hazard:
    """`spec.kind` → la implementación. Falla fuerte si no existe."""
    raise NotImplementedError
