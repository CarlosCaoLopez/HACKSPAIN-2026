"""La línea temporal de sorpresas, más el botón de emergencia del pitch.

Los tres del YAML: `wind_shift`, `road_cut`, `unit_failure`. Y uno más que no está
en el YAML porque lo dispara la llamada entrante. Ese es el importante.

`POST /control/inject` lanza cualquiera a mano. En el ensayo lo vais a usar
constantemente.

Módulo puro (D5): decide *qué* vence, nunca lo ejecuta. Quien lo aplica es el
runner, que es el único que habla con el mundo y con el bus.
"""

from contracts.scenario import InjectSpec

WIND_SHIFT = "wind_shift"
ROAD_CUT = "road_cut"
UNIT_FAILURE = "unit_failure"

KNOWN = {WIND_SHIFT, ROAD_CUT, UNIT_FAILURE}
"""Los del YAML. `inject()` acepta otros: el de la llamada entrante no está aquí."""


class InjectScheduler:
    """Dispara los injects vencidos, una sola vez cada uno."""

    def __init__(self, injects: list[InjectSpec]) -> None:
        # Orden estable por tiempo y tipo: dos injects en el mismo `at` tienen que
        # salir siempre en el mismo orden o el run deja de ser reproducible.
        self._pending = sorted(injects, key=lambda i: (i.at, i.type))
        self._fired: list[InjectSpec] = []

    def due(self, t_sim: float) -> list[InjectSpec]:
        """Los que vencen en o antes de t_sim y no se han disparado."""
        ready = [spec for spec in self._pending if spec.at <= t_sim]
        self._pending = [spec for spec in self._pending if spec.at > t_sim]
        self._fired.extend(ready)
        return ready

    def arm(self, spec: InjectSpec) -> None:
        """Añade uno a mano, desde `POST /control/inject`."""
        self._pending.append(spec)
        self._pending.sort(key=lambda i: (i.at, i.type))

    @property
    def pending(self) -> list[InjectSpec]:
        return list(self._pending)

    @property
    def fired(self) -> list[InjectSpec]:
        """Lo ya disparado, para `snapshot()` y para depurar un ensayo."""
        return list(self._fired)
