"""El bucle del core y la clase `Core`. P1.

El ciclo de un tick:
1. `sim` publica `world.*`.
2. `belief` aplica los eventos sobre el `WorldState`.
3. `divergence` lo compara contra `PlanContext.assumptions`.
4. Si se levanta la bandera: `planner` → `Policy`, `solver` → `Plan`,
   `verifiers` lo validan. Dos vueltas máximo, luego plan con pesos neutros.
5. `core` publica `action.*`. `sim` y `voice` ejecutan y confirman.

Si la bandera no se levanta, no se llama al modelo.
"""

from contracts.events import Event
from contracts.plan import Plan
from contracts.scenario import Scenario
from contracts.world import WorldState


class Core:
    def __init__(self, bus, scenario: Scenario) -> None:
        raise NotImplementedError

    async def run(self) -> None:
        """Consume el bus indefinidamente."""
        raise NotImplementedError

    def state(self) -> WorldState:
        """El dashboard lo pide al arrancar."""
        raise NotImplementedError

    def current_plan(self) -> Plan | None:
        raise NotImplementedError

    async def on_event(self, ev: Event) -> None:
        """Un evento del bus: aplica, mide divergencia, decide si replanifica."""
        raise NotImplementedError

    async def replan(self, reason: str) -> Plan:
        """Planner → solver → verifiers, máximo dos vueltas. Siempre devuelve un
        plan: a la tercera, el del solver con pesos neutros."""
        raise NotImplementedError

    async def handle_override(self, kind: str, target: str, value, note: str) -> None:
        """Prioridad máxima. `assert_fact` con confidence=1.0, `veto_assignment`
        pone coste infinito a ese par durante 5 minutos, `force_replan` salta el
        umbral de divergencia."""
        raise NotImplementedError


VETO_TTL_S = 300.0
"""Lo que dura un `veto_assignment` humano."""
