"""Carreteras y Dijkstra. Veinte líneas, y ahí está toda la magia del replan.

Si una arista está marcada `cut`, Dijkstra simplemente no la usa.
"""

from contracts.scenario import Scenario, Waypoint
from contracts.world import RoadEdge


class RoadGraph:
    """Grafo de waypoints del YAML. `goto` no dispara pathfinding: resuelve aquí."""

    def __init__(self, waypoints: list[Waypoint], edges: list[RoadEdge]) -> None:
        raise NotImplementedError

    @classmethod
    def from_scenario(cls, scenario: Scenario) -> "RoadGraph":
        raise NotImplementedError

    def shortest_path(self, a: str, b: str) -> list[str] | None:
        """Polilínea de waypoint ids, o None si no hay ruta viva."""
        raise NotImplementedError

    def route_length_m(self, route: list[str]) -> float:
        raise NotImplementedError

    def cut(self, edge_id: str, cause: str) -> None:
        """Marca la arista. Publica `world.road.changed` quien llame, no esto."""
        raise NotImplementedError

    def restore(self, edge_id: str) -> None:
        raise NotImplementedError

    def position_of(self, waypoint_id: str) -> tuple[float, float]:
        """(x, z) del mundo Minecraft."""
        raise NotImplementedError
