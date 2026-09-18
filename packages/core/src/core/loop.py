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

from datetime import UTC, datetime

from contracts.calls import Fact
from contracts.events import (
    ActionRequested,
    DivergenceReport,
    Event,
    EventType,
    FactAsserted,
    HumanOverride,
    ReplanStarted,
)
from contracts.plan import MAX_REPLAN_ROUNDS, Plan, Violation
from contracts.scenario import Scenario
from contracts.world import WorldState
from core import belief, planner
from core.divergence import divergence, should_replan
from core.planner import neutral_policy
from core.solver import RoadGraph, solve
from core.verifiers import verify

VETO_TTL_S = 300.0
"""Lo que dura un `veto_assignment` humano."""

SUBSCRIBED: tuple[EventType, ...] = tuple(
    t for t in EventType if t.value.startswith("world.")
) + (EventType.CALL_ENDED, EventType.HUMAN_OVERRIDE)
"""`world.*` (incluye `world.fact.asserted`), `call.ended` y `human.override`."""


class Core:
    def __init__(self, bus, scenario: Scenario) -> None:
        self.bus = bus
        self.scenario = scenario
        self.run_id = bus.current_run_id()
        self.graph = RoadGraph.from_scenario(scenario)
        self._state = belief.initial_state(self.run_id, scenario)
        self._plan: Plan | None = None
        self._last_actions: dict[str, str] = {}  # unit_id -> task_id ya ordenado
        self._vetoes: dict[tuple[str, str], float] = {}  # (unit, task) -> expiry t_sim
        self._action_seq = 0

    async def run(self) -> None:
        """Consume el bus indefinidamente."""
        async for ev in self.bus.subscribe(*SUBSCRIBED):
            await self.on_event(ev)

    def state(self) -> WorldState:
        """El dashboard lo pide al arrancar."""
        return self._state

    def current_plan(self) -> Plan | None:
        return self._plan

    async def on_event(self, ev: Event) -> None:
        """Un evento del bus: aplica, mide divergencia, decide si replanifica."""
        if ev.type == EventType.HUMAN_OVERRIDE:
            ov = HumanOverride.model_validate(ev.payload)
            await self.handle_override(ov.kind, ov.target, ov.value, ov.note, ev)
            return

        self._state = belief.apply(self._state, ev)
        self._purge_vetoes()

        # Plan inicial: en cuanto hay tareas abiertas y aún no hay plan.
        if self._plan is None:
            if any(not t.done for t in self._state.tasks.values()):
                await self.replan("plan inicial", "divergence", ev)
            return

        value, broken = divergence(self._state, self._plan.context)
        await self._emit(EventType.PLAN_DIVERGENCE, DivergenceReport(value=value, broken=broken), ev)

        hard = [v for v in verify(self._state, self._plan, self.graph) if v.severity == "hard"]
        criticals = self._critical_facts_of(ev)

        flag, reason = should_replan(value, len(hard), criticals)
        if flag:
            await self.replan(reason, _trigger(hard, criticals, value), ev)

    async def replan(self, reason: str, trigger: str, cause: Event) -> Plan:
        """Planner → solver → verifiers, máximo dos vueltas. Siempre devuelve un
        plan: a la tercera, el del solver con pesos neutros."""
        await self._emit(
            EventType.PLAN_REPLAN_STARTED,
            ReplanStarted(reason=reason, trigger=trigger),
            cause,
        )

        policy = await planner.plan(self._state, reason)
        await self._emit(EventType.PLAN_POLICY_EMITTED, policy, cause)

        vetoes = self._active_vetoes()
        plan = solve(self._state, policy, self.graph, vetoes=vetoes)
        for _round in range(MAX_REPLAN_ROUNDS):
            viols = verify(self._state, plan, self.graph)
            for v in viols:
                await self._emit(EventType.PLAN_VIOLATION, v, cause)
            if not viols:
                break
            if _round == MAX_REPLAN_ROUNDS - 1:
                # Vueltas agotadas: plan neutro, siempre factible.
                plan = solve(self._state, neutral_policy(), self.graph, vetoes=vetoes)
                break
            policy = await planner.replan_with_critique(self._state, viols)
            await self._emit(EventType.PLAN_POLICY_EMITTED, policy, cause)
            plan = solve(self._state, policy, self.graph, vetoes=vetoes)

        self._plan = plan
        await self._emit(EventType.PLAN_EMITTED, plan, cause)
        await self._emit_actions(plan, cause)
        return plan

    async def handle_override(
        self, kind: str, target: str, value, note: str, cause: Event
    ) -> None:
        """Prioridad máxima. `assert_fact` con confidence=1.0, `veto_assignment`
        pone coste infinito a ese par durante 5 minutos, `force_replan` salta el
        umbral de divergencia."""
        if kind == "assert_fact":
            fact = Fact(
                key=target,
                value=value,
                confidence=1.0,
                source="human",
                severity="critical",
                t_sim=self._state.t_sim,
            )
            self._state = belief.apply_fact(self._state, fact)
            await self.replan("override: hecho humano", "human_override", cause)
        elif kind == "veto_assignment":
            # target = unit_id, value = task_id.
            self._vetoes[(target, str(value))] = self._state.t_sim + VETO_TTL_S
            await self.replan("override: veto", "human_override", cause)
        elif kind == "force_replan":
            await self.replan(note or "override: replan forzado", "human_override", cause)
        # force_assignment y set_priority no se implementan en este hito: se ignoran.

    # --- internos ----------------------------------------------------------

    def _active_vetoes(self) -> set[tuple[str, str]]:
        return {pair for pair, exp in self._vetoes.items() if exp > self._state.t_sim}

    def _purge_vetoes(self) -> None:
        self._vetoes = {
            pair: exp for pair, exp in self._vetoes.items() if exp > self._state.t_sim
        }

    def _critical_facts_of(self, ev: Event) -> list[Fact]:
        """Los hechos con `severity="critical"` que trae este evento."""
        if ev.type != EventType.WORLD_FACT_ASSERTED:
            return []
        fa = FactAsserted.model_validate(ev.payload)
        if fa.severity != "critical":
            return []
        return [
            Fact(
                key=fa.key,
                value=fa.value,
                confidence=fa.confidence,
                source=fa.source,
                severity=fa.severity,
                t_sim=self._state.t_sim,
            )
        ]

    async def _emit_actions(self, plan: Plan, cause: Event) -> None:
        """Un `goto` por asignación nueva o cambiada. Diffear evita reenviar la misma
        orden en cada replan."""
        current = {a.unit_id: a.task_id for a in plan.assignments}
        for a in plan.assignments:
            if self._last_actions.get(a.unit_id) == a.task_id:
                continue
            self._action_seq += 1
            await self._emit(
                EventType.ACTION_REQUESTED,
                ActionRequested(
                    action_id=f"act_{self.run_id}_{self._action_seq}",
                    verb="goto",
                    args={"unit_id": a.unit_id, "route": a.route},
                ),
                cause,
            )
        self._last_actions = current

    async def _emit(self, etype: EventType, payload, cause: Event) -> None:
        """Envuelve un payload Pydantic en un `Event` de source=core y lo publica.
        `causes=[cause.seq]` da al dashboard la cadena hecho → violación → replan → orden."""
        ev = Event(
            run_id=self.run_id,
            seq=0,  # lo sella el bus
            t_wall=datetime.now(UTC),
            t_sim=self._state.t_sim,
            type=etype,
            source="core",
            payload=payload.model_dump(mode="json"),
            causes=[cause.seq],
        )
        await self.bus.publish(ev)


def _trigger(hard: list[Violation], criticals: list[Fact], value: float) -> str:
    """El disparador que levantó la bandera, con la misma prioridad que `should_replan`."""
    if hard:
        return "hard_violation"
    if criticals:
        return "critical_fact"
    return "divergence"
