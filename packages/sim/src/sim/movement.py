"""Interpolador a 5 Hz. El armor stand se desliza por la carretera.

Cinco veces por segundo: `tp @e[tag=unit_truck1] x y z <yaw> 0`. Perfectamente
determinista. Al llegar emite `world.unit.arrived`; cada segundo,
`world.unit.position`, que es lo que alimenta el mapa del dashboard.

Módulo **puro** (D5): geometría y nada más. No habla con RCON ni publica eventos;
devuelve posiciones y cadenas de comando, y el runner decide qué hacer con ellas.

La interpolación va por la **geometría** de los waypoints (distancia euclídea entre
sus coordenadas), no por `RoadEdge.length_m`. `length_m` es el coste que usa
Dijkstra y puede describir una carretera con curvas; lo que se ve en pantalla es
el segmento recto entre dos waypoints. Si en el YAML los dos valores divergen, el
camión llega antes o después de lo que dice el ETA del plan: usa
`euclidean_length_m` al rellenar `scenarios/*.yaml` y no tendrás esa discusión.
"""

import math
from collections.abc import Iterator

from sim.graph import RoadGraph

TICK_HZ = 5
"""Frecuencia de interpolación. `world.unit.position` sale a 1 Hz, no a 5."""


def yaw_between(x1: float, z1: float, x2: float, z2: float) -> float:
    """Yaw de Minecraft mirando de (x1,z1) a (x2,z2).

    En Minecraft 0 es sur (+Z), 90 oeste (-X), 180 norte (-Z), -90 este (+X).
    De ahí el `-dx`, que es el error clásico al orientar entidades por comando.
    """
    return math.degrees(math.atan2(-(x2 - x1), z2 - z1))


def euclidean_length_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.dist(a, b)


def tp_command(unit_id: str, x: float, z: float, y: float, yaw: float) -> str:
    """El `tp` con selector por tag. Un solo sitio donde se escribe.

    Sin barra inicial: por RCON los comandos van como en la consola del servidor.
    **Sin `limit`**: una unidad son varias entidades —las piezas del vehículo más
    su cartel— y el tag las mueve todas de golpe. Cada pieza guarda su sitio en la
    `transformation`, que gira con el yaw, así que la forma se conserva.
    """
    return f"tp @e[tag={unit_id}] {x:.2f} {y:.2f} {z:.2f} {yaw:.1f} 0"


class Movement:
    """Un desplazamiento en curso de una unidad sobre una polilínea."""

    def __init__(
        self, unit_id: str, route: list[str], speed_mps: float, graph: RoadGraph
    ) -> None:
        if not route:
            raise ValueError("una ruta vacía no es un movimiento")
        if speed_mps <= 0:
            raise ValueError(f"velocidad no positiva: {speed_mps}")

        self.unit_id = unit_id
        self.route = route
        self.speed_mps = speed_mps

        self._points = [graph.position_of(waypoint) for waypoint in route]
        # Longitudes acumuladas: _marks[i] es la distancia recorrida al llegar a
        # _points[i]. Los tramos de longitud cero (dos waypoints encima) no rompen.
        self._marks = [0.0]
        for start, end in zip(self._points, self._points[1:]):
            self._marks.append(self._marks[-1] + euclidean_length_m(start, end))
        self._travelled = 0.0

    @property
    def total_m(self) -> float:
        return self._marks[-1]

    @property
    def done(self) -> bool:
        return self._travelled >= self.total_m

    @property
    def eta_s(self) -> float:
        return max(0.0, (self.total_m - self._travelled) / self.speed_mps)

    def step(self, dt: float) -> tuple[float, float, float]:
        """Avanza dt segundos. Devuelve (x, z, yaw)."""
        self._travelled = min(self._travelled + self.speed_mps * dt, self.total_m)
        return self.position()

    def position(self) -> tuple[float, float, float]:
        """(x, z, yaw) en el punto actual, sin avanzar."""
        if len(self._points) == 1:
            # `goto` al waypoint donde ya está: ruta válida, sin dirección.
            x, z = self._points[0]
            return x, z, 0.0
        index = self._segment_at(self._travelled)
        (x1, z1), (x2, z2) = self._points[index], self._points[index + 1]
        segment = self._marks[index + 1] - self._marks[index]
        ratio = (self._travelled - self._marks[index]) / segment if segment else 0.0
        return (
            x1 + (x2 - x1) * ratio,
            z1 + (z2 - z1) * ratio,
            yaw_between(x1, z1, x2, z2),
        )

    def _segment_at(self, distance: float) -> int:
        """Índice del tramo que contiene esa distancia. El último tramo se queda
        con el final de la ruta, para que `position()` no se salga del array."""
        for index in range(len(self._marks) - 2, -1, -1):
            if distance >= self._marks[index]:
                return index
        return 0


def interpolate(
    route: list[str], graph: RoadGraph, speed_mps: float
) -> Iterator[tuple[float, float, float]]:
    """Puntos (x, z, yaw) a TICK_HZ. Puro: testeable sin RCON.

    El primer punto es el origen; el último, el destino exacto.
    """
    movement = Movement("unit_preview", route, speed_mps, graph)
    yield movement.position()
    while not movement.done:
        yield movement.step(1 / TICK_HZ)
