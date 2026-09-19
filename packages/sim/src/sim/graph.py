"""Carreteras y Dijkstra. Veinte líneas, y ahí está toda la magia del replan.

Si una arista está marcada `cut`, Dijkstra simplemente no la usa.

Módulo **puro** (D5): sin red, sin reloj, sin `random`. No publica eventos ni habla
con RCON; `cut()` marca la arista y quien llame decide si emite `world.road.changed`.
Eso es lo que lo hace testeable sin Paper y sin bus.
"""

import heapq
from itertools import pairwise

from contracts.scenario import Scenario, Waypoint
from contracts.world import RoadEdge


class RoadGraph:
    """Grafo de waypoints del YAML. `goto` no dispara pathfinding: resuelve aquí."""

    def __init__(self, waypoints: list[Waypoint], edges: list[RoadEdge]) -> None:
        self._waypoints: dict[str, Waypoint] = {w.id: w for w in waypoints}
        self._edges: dict[str, RoadEdge] = {e.id: e for e in edges}

        for edge in edges:
            for end in (edge.a, edge.b):
                if end not in self._waypoints:
                    raise ValueError(
                        f"la arista {edge.id!r} apunta a {end!r}, que no es un waypoint"
                    )

        # Adyacencia no dirigida: una carretera se recorre en los dos sentidos.
        self._adjacent: dict[str, list[str]] = {w: [] for w in self._waypoints}
        for edge in edges:
            self._adjacent[edge.a].append(edge.id)
            self._adjacent[edge.b].append(edge.id)

    @classmethod
    def from_scenario(cls, scenario: Scenario) -> "RoadGraph":
        return cls(scenario.waypoints, scenario.roads)

    def shortest_path(self, a: str, b: str) -> list[str] | None:
        """Polilínea de waypoint ids, o None si no hay ruta viva.

        Dijkstra con desempate por id: dos rutas de igual coste devuelven siempre
        la misma, que es lo que permite ensayar el mismo escenario veinte veces y
        ver exactamente lo mismo.
        """
        self._require(a)
        self._require(b)
        if a == b:
            return [a]

        distances: dict[str, float] = {a: 0.0}
        previous: dict[str, str] = {}
        visited: set[str] = set()
        heap: list[tuple[float, str]] = [(0.0, a)]

        while heap:
            distance, current = heapq.heappop(heap)
            if current in visited:
                continue
            visited.add(current)
            if current == b:
                return self._rebuild(previous, a, b)

            for edge in self._live_edges(current):
                neighbour = edge.b if edge.a == current else edge.a
                if neighbour in visited:
                    continue
                candidate = distance + edge.length_m
                if candidate < distances.get(neighbour, float("inf")):
                    distances[neighbour] = candidate
                    previous[neighbour] = current
                    heapq.heappush(heap, (candidate, neighbour))

        return None

    def route_length_m(self, route: list[str]) -> float:
        total = 0.0
        for origin, target in pairwise(route):
            edge = self.edge_between(origin, target)
            if edge is None:
                raise ValueError(f"no hay carretera viva entre {origin!r} y {target!r}")
            total += edge.length_m
        return total

    def resolve_edge(self, reference: str) -> str | None:
        """Id de arista a partir de cualquiera de las formas que circulan.

        Acepta el id tal cual (`road:wp_a-wp_b`), la pareja de extremos sin
        prefijo (`wp_a-wp_b`, que es como lo escribe el ejemplo de inject del
        backbone) y el sentido contrario (`wp_b-wp_a`). Devuelve `None` si no
        hay tal carretera.

        Existe porque quien nombra una carretera desde fuera —una llamada, un
        `human.override`, el YAML de otro— no tiene por qué saber cómo la
        escribimos aquí, y un id que no casa es un fallo mudo.
        """
        if reference in self._edges:
            return reference
        cuerpo = reference.removeprefix("road:")
        if "-" not in cuerpo:
            return None
        a, b = cuerpo.split("-", 1)
        for edge in self._edges.values():
            if {edge.a, edge.b} == {a, b}:
                return edge.id
        return None

    def cut(self, edge_id: str, cause: str) -> None:
        """Marca la arista. Publica `world.road.changed` quien llame, no esto."""
        edge_id = self.resolve_edge(edge_id) or edge_id
        edge = self._edge(edge_id)
        self._edges[edge_id] = edge.model_copy(update={"cut": True, "cut_cause": cause})

    def restore(self, edge_id: str) -> None:
        edge_id = self.resolve_edge(edge_id) or edge_id
        edge = self._edge(edge_id)
        self._edges[edge_id] = edge.model_copy(update={"cut": False, "cut_cause": None})

    def position_of(self, waypoint_id: str) -> tuple[float, float]:
        """(x, z) del mundo Minecraft."""
        self._require(waypoint_id)
        waypoint = self._waypoints[waypoint_id]
        return waypoint.x, waypoint.z

    # --- consultas que necesitan movement.py y el runner ---

    def edge_between(self, a: str, b: str) -> RoadEdge | None:
        """La arista viva más corta entre dos waypoints contiguos, o None."""
        candidates = [
            edge
            for edge in self._live_edges(a)
            if (edge.b if edge.a == a else edge.a) == b
        ]
        return min(candidates, key=lambda e: e.length_m) if candidates else None

    def is_cut(self, edge_id: str) -> bool:
        """Acepta las mismas formas que `cut` y `restore`: quien pregunta por una
        carretera no tiene por qué nombrarla distinto que quien la corta."""
        return self._edge(self.resolve_edge(edge_id) or edge_id).cut

    @property
    def waypoint_ids(self) -> list[str]:
        return list(self._waypoints)

    # --- interno ---

    def _live_edges(self, waypoint_id: str) -> list[RoadEdge]:
        return [
            self._edges[edge_id]
            for edge_id in self._adjacent[waypoint_id]
            if not self._edges[edge_id].cut
        ]

    def _edge(self, edge_id: str) -> RoadEdge:
        if edge_id not in self._edges:
            raise ValueError(f"no existe la arista {edge_id!r}")
        return self._edges[edge_id]

    def _require(self, waypoint_id: str) -> None:
        if waypoint_id not in self._waypoints:
            raise ValueError(f"no existe el waypoint {waypoint_id!r}")

    @staticmethod
    def _rebuild(previous: dict[str, str], a: str, b: str) -> list[str]:
        route = [b]
        while route[-1] != a:
            route.append(previous[route[-1]])
        return list(reversed(route))
