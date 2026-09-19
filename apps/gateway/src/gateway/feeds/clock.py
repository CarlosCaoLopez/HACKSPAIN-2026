"""El reloj fechado. SPEC-007 · REQ-241.

Un dato real lleva su marca de tiempo (`acq_date`+`acq_time` de FIRMS, la hora del modelo
de Open-Meteo). Para reproducir un día real a velocidad de demo hay que decidir **cuándo,
en `t_sim`, se emite cada dato**: `t_real = reference_start + t_sim · time_scale`.

Sin `reference_start` el modo es en vivo y todo se emite al llegar.

`Schedule` es una cola de prioridad y no una lista ordenada a mano porque los datos
llegan de cuatro fuentes a ritmos distintos y tienen que salir en orden de `t_sim`.
"""

from __future__ import annotations

import heapq
from datetime import UTC, datetime

from gateway.feeds import Observation
from gateway.feeds.anchor import GeoAnchor


def due_t_sim(anchor: GeoAnchor, t_real: datetime | None) -> float:
    """El `t_sim` en el que toca emitir un dato con esa marca de tiempo real.

    En vivo, o sin marca de tiempo, `0.0`: ahora. Un dato anterior al arranque del run
    también sale ya, porque ya era verdad cuando empezó (se clampa a 0).
    """
    if anchor.reference_start is None or t_real is None:
        return 0.0
    if t_real.tzinfo is None:  # los CSV de FIRMS dan UTC sin zona
        t_real = t_real.replace(tzinfo=UTC)
    return max(0.0, (t_real - anchor.reference_start).total_seconds() / anchor.time_scale)


class Schedule:
    """Cola de observaciones ordenadas por el `t_sim` en el que les toca salir."""

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, Observation]] = []
        # El contador desempata: sin él, dos datos con el mismo `t_sim` se compararían
        # por `Observation`, que no es ordenable, y `heapq` reventaría.
        self._n = 0

    def push(self, obs: Observation, anchor: GeoAnchor) -> None:
        heapq.heappush(self._heap, (due_t_sim(anchor, obs.t_real), self._n, obs))
        self._n += 1

    def pop_due(self, t_sim_now: float) -> list[Observation]:
        out: list[Observation] = []
        while self._heap and self._heap[0][0] <= t_sim_now:
            out.append(heapq.heappop(self._heap)[2])
        return out

    def __len__(self) -> int:
        return len(self._heap)


__all__ = ["Schedule", "due_t_sim"]
