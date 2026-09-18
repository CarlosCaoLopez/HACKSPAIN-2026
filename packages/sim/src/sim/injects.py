"""La línea temporal de sorpresas, más el botón de emergencia del pitch.

Los tres del YAML: `wind_shift`, `road_cut`, `unit_failure`. Y uno más que no está
en el YAML porque lo dispara la llamada entrante. Ese es el importante.

`POST /control/inject` lanza cualquiera a mano. En el ensayo lo vais a usar
constantemente.
"""

from contracts.scenario import InjectSpec

WIND_SHIFT = "wind_shift"
ROAD_CUT = "road_cut"
UNIT_FAILURE = "unit_failure"


class InjectScheduler:
    """Dispara los injects vencidos, una sola vez cada uno."""

    def __init__(self, injects: list[InjectSpec]) -> None:
        raise NotImplementedError

    def due(self, t_sim: float) -> list[InjectSpec]:
        """Los que vencen en o antes de t_sim y no se han disparado."""
        raise NotImplementedError

    def arm(self, spec: InjectSpec) -> None:
        """Añade uno a mano, desde `POST /control/inject`."""
        raise NotImplementedError
