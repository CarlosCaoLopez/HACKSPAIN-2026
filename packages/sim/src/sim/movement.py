"""Interpolador a 5 Hz. El armor stand se desliza por la carretera.

Cinco veces por segundo: `/tp @e[tag=unit_truck1] x y z <yaw> 0`. Perfectamente
determinista. Al llegar emite `world.unit.arrived`; cada segundo,
`world.unit.position`, que es lo que alimenta el mapa del dashboard.
"""

from collections.abc import Iterator

from sim.graph import RoadGraph

TICK_HZ = 5
"""Frecuencia de interpolación. `world.unit.position` sale a 1 Hz, no a 5."""


class Movement:
    """Un desplazamiento en curso de una unidad sobre una polilínea."""

    def __init__(
        self, unit_id: str, route: list[str], speed_mps: float, graph: RoadGraph
    ) -> None:
        raise NotImplementedError

    def step(self, dt: float) -> tuple[float, float, float]:
        """Avanza dt segundos. Devuelve (x, z, yaw)."""
        raise NotImplementedError

    @property
    def done(self) -> bool:
        raise NotImplementedError

    @property
    def eta_s(self) -> float:
        raise NotImplementedError


def tp_command(unit_id: str, x: float, z: float, y: float, yaw: float) -> str:
    """El `/tp` con selector por tag. Un solo sitio donde se escribe."""
    raise NotImplementedError


def interpolate(
    route: list[str], graph: RoadGraph, speed_mps: float
) -> Iterator[tuple[float, float, float]]:
    """Puntos (x, z, yaw) a TICK_HZ. Puro: testeable sin RCON."""
    raise NotImplementedError
