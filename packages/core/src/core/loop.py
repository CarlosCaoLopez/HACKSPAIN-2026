"""El bucle del core y la clase `Core`. P1.

El ciclo de un tick:
1. `sim` publica `world.*`.
2. `belief` aplica los eventos sobre el `WorldState`.
3. `tasks` decide qué tareas nacen o se cierran; se publican como `task.changed` y
   se pliegan (el estado es inmutable y el journal append-only).
4. `divergence` lo compara contra `PlanContext.assumptions`.
5. Si se levanta la bandera: `planner` → `Policy`, `solver` → `Plan`,
   `verifiers` lo validan. Dos vueltas máximo, luego plan con pesos neutros.
   Sin bandera pero con tareas nuevas o cerradas: solo el solver, con la política
   vigente (ninguna llamada al modelo).
6. `core` publica `action.*`, `call.requested` (orden de evacuación al POI de cada
   evacuación recién asignada) y, si el replan lo provocó un hecho de una llamada
   en curso, `call.signal.requested` para que el agente lo cuente en vivo.

Si la bandera no se levanta, no se llama al modelo.
"""

import logging
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
    SignalRequested,
    TaskChanged,
)
from contracts.plan import MAX_REPLAN_ROUNDS, Assignment, Plan, Policy, Violation
from contracts.scenario import Scenario
from contracts.settings import settings
from contracts.world import Task, WorldState
from core import belief, calls, memory, planner, tasks
from core.divergence import _angular_gap, divergence, should_replan
from core.planner import neutral_policy
from core.solver import RoadGraph, solve_with_violations
from core.verifiers import verify

log = logging.getLogger("core.loop")

VETO_TTL_S = 300.0
"""Lo que dura un `veto_assignment` humano."""

CALL_SOURCE_PREFIX = "call:"
"""`source` de un hecho que vino de una llamada en curso: `call:<session_id>`."""

SIGNAL_UNIT_DISPATCHED = "unit_dispatched"
PEOPLE_TASKS = frozenset({"evacuate", "rescue"})
RESOLVE_GAP_S = 5.0
"""Cadencia mínima (segundos de sim) entre re-solves por altas de extinción."""
"""Tareas que van hacia personas: las únicas que se cuentan como "ya va" por teléfono."""


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
        self._rules = memory.load_rules()  # memoria entre runs; vacía en el run 1
        self._plan: Plan | None = None
        self._last_actions: dict[
            str, tuple[str, tuple[str, ...]]
        ] = {}  # unit_id -> (task_id, ruta) ya ordenados
        self._vetoes: dict[tuple[str, str], float] = {}  # (unit, task) -> expiry t_sim
        self._action_seq = 0
        self._called: set[str] = set()  # task_ids con `call.requested` ya emitido
        # call_id → (unit_id, task_id, ruta) de la última señal `unit_dispatched`:
        # tres hechos de una misma llamada no producen tres veces la misma frase.
        self._signalled: dict[str, tuple[str, str, str]] = {}
        self._pending_resolve: list[Task] = []
        self._burst: tuple[str, float] | None = None  # (source, t_sim) del último replan
        self._last_resolve_t = -1e9
        # call_id → (poi_id, hecho): la llamada nombra un POI cuya tarea sigue sin
        # unidad; se le dirá "ya va" con el primer plan que la asigne.
        self._awaiting: dict[str, tuple[str, Event]] = {}
        # Violaciones duras que el plan vigente ya traía cuando se adoptó: el
        # planner tuvo sus dos vueltas y no pudo con ellas. Verlas otra vez en el
        # siguiente evento no es novedad, y no vuelve a llamar al modelo.
        self._residual: set[str] = set()
        self._warned_no_phone = False

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
        """Un evento del bus: aplica, sincroniza tareas, mide divergencia, decide si
        replanifica."""
        if ev.type == EventType.HUMAN_OVERRIDE:
            ov = HumanOverride.model_validate(ev.payload)
            await self.handle_override(ov.kind, ov.target, ov.value, ov.note, ev)
            return

        self._state = belief.apply(self._state, ev)
        self._purge_vetoes()
        before = self._state.tasks
        changed = await self._sync_tasks(ev)

        # Plan inicial: en cuanto hay tareas abiertas y aún no hay plan.
        if self._plan is None:
            if any(not t.done for t in self._state.tasks.values()):
                await self.replan("plan inicial", "divergence", ev)
            return

        value, broken = divergence(self._state, self._plan.context)
        await self._emit(
            EventType.PLAN_DIVERGENCE, DivergenceReport(value=value, broken=broken), ev
        )

        hard = [
            v
            for v in verify(self._state, self._plan, self.graph)
            if v.severity == "hard" and v.message not in self._residual
        ]
        criticals = self._critical_facts_of(ev)

        flag, reason = should_replan(value, len(hard), criticals)
        trigger = _trigger(hard, criticals, value) if flag else ""
        if flag and trigger == "critical_fact" and self._same_burst(ev):
            # Un `report_fact` publica tres hechos seguidos (arista, causa, inmóviles)
            # desde la misma llamada y el mismo `t_sim`: el modelo ya replanificó con
            # el primero; los demás solo mueven al solver (que sí ve la tarea nueva).
            await self.resolve(changed, ev, reason=f"{reason} (misma llamada)")
            self._pending_resolve = []
        elif flag:
            await self.replan(reason, trigger, ev)
            self._burst = (ev.source, self._state.t_sim)
            self._pending_resolve = []
        elif changed:
            self._pending_resolve.extend(changed)
            if self._resolve_due(changed, before):
                await self.resolve(self._pending_resolve, ev)
                self._pending_resolve = []
                self._last_resolve_t = self._state.t_sim

    def _same_burst(self, ev: Event) -> bool:
        return self._burst == (ev.source, self._state.t_sim)

    def _resolve_due(self, changed: list[Task], before: dict[str, Task]) -> bool:
        """Re-resolver por cada tarea que cambia era un plan por segundo (141 en
        142 s de sim cuando había una tarea por celda). Los cambios de extinción (un
        frente que nace o mueve su celda objetivo) se agrupan y se resuelven cada
        `RESOLVE_GAP_S`; un cierre, una tarea hacia personas, un frente que sube a
        `critical` (`before` es lo que había antes de sincronizar) o el primer cambio
        tras un silencio van al momento."""
        urgent = any(
            t.done
            or t.kind in PEOPLE_TASKS
            or (t.severity == "critical" and _severity_before(before, t) != "critical")
            for t in changed
        )
        return urgent or self._state.t_sim - self._last_resolve_t >= RESOLVE_GAP_S

    async def replan(self, reason: str, trigger: str, cause: Event) -> Plan:
        """Planner → solver → verifiers, máximo dos vueltas. Siempre devuelve un
        plan: a la tercera, el del solver con pesos neutros."""
        # Recall condicional: solo las reglas cuyo trigger casa con el estado. El
        # planner recibe únicamente esas, y sus slugs van al journal como lineage.
        selected = memory.select(self._state, self._rules, extra=self._recall_extra())
        fired = [r.slug for r in selected]
        await self._emit(
            EventType.PLAN_REPLAN_STARTED,
            ReplanStarted(reason=reason, trigger=trigger, fired_rules=fired),
            cause,
        )

        policy = await planner.plan(self._state, reason, memory.render(selected))
        await self._emit(EventType.PLAN_POLICY_EMITTED, policy, cause)

        vetoes = self._active_vetoes()
        plan, unknown = self._solve(policy, vetoes)
        for _round in range(MAX_REPLAN_ROUNDS):
            # Las restricciones desconocidas (blandas) vuelven al planner como
            # crítica igual que las duras: no se ignoran en silencio.
            viols = verify(self._state, plan, self.graph) + unknown
            for v in viols:
                await self._emit(EventType.PLAN_VIOLATION, v, cause)
            if not viols:
                break
            if all(v.message in self._residual for v in viols):
                # Solo lo que ya no tenía arreglo la última vez (Pueblo B sin unidad
                # que dejar): otra vuelta de crítica son 2 s de modelo para nada.
                break
            if _round == MAX_REPLAN_ROUNDS - 1:
                # Vueltas agotadas: plan neutro, siempre factible.
                plan, _ = self._solve(neutral_policy(), vetoes)
                break
            policy = await planner.replan_with_critique(self._state, viols)
            await self._emit(EventType.PLAN_POLICY_EMITTED, policy, cause)
            plan, unknown = self._solve(policy, vetoes)

        await self._adopt(plan, cause)
        self._residual = {
            v.message
            for v in verify(self._state, plan, self.graph)
            if v.severity == "hard"
        }
        return plan

    async def resolve(
        self, changed: list[Task], cause: Event, reason: str | None = None
    ) -> Plan:
        """Solo el solver, con la política vigente: las tareas cambiaron (nace una,
        se cierra otra) pero no hay bandera, así que no se llama al modelo. Sin
        esto, una unidad que acaba su tarea se queda parada hasta el siguiente
        replan."""
        assert self._plan is not None
        ids = ", ".join(t.id + (" ✓" if t.done else "") for t in changed)
        await self._emit(
            EventType.PLAN_REPLAN_STARTED,
            ReplanStarted(reason=reason or f"tareas: {ids}", trigger="tasks_changed"),
            cause,
        )
        plan, unknown = self._solve(self._plan.policy, self._active_vetoes())
        for v in unknown:
            await self._emit(EventType.PLAN_VIOLATION, v, cause)
        await self._adopt(plan, cause)
        return plan

    async def _adopt(self, plan: Plan, cause: Event) -> None:
        """El plan pasa a ser el vigente: se publica, se pliega en el propio estado
        (`unit.task_id`/`status`, igual que lo verá el dashboard) y salen sus
        consecuencias: órdenes, llamadas y señal."""
        self._plan = plan
        pev = await self._emit(EventType.PLAN_EMITTED, plan, cause)
        self._state = belief.apply(self._state, pev)
        await self._after_plan(plan, cause)

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
            await self._sync_tasks(cause)
            await self.replan("override: hecho humano", "human_override", cause)
        elif kind == "veto_assignment":
            # target = unit_id, value = task_id.
            self._vetoes[(target, str(value))] = self._state.t_sim + VETO_TTL_S
            await self.replan("override: veto", "human_override", cause)
        elif kind == "force_replan":
            await self.replan(note or "override: replan forzado", "human_override", cause)
        # force_assignment y set_priority no se implementan en este hito: se ignoran.

    # --- internos ----------------------------------------------------------

    def _solve(
        self, policy: Policy, vetoes: set[tuple[str, str]]
    ) -> tuple[Plan, list[Violation]]:
        return solve_with_violations(self._state, policy, self.graph, vetoes=vetoes)

    async def _sync_tasks(self, cause: Event) -> list[Task]:
        """Publica un `task.changed` por cada alta, cambio o cierre y lo pliega en el
        estado. `belief` sigue siendo un fold: la política vive en `core.tasks`."""
        changed = tasks.sync(self._state, self.graph, cause)
        for t in changed:
            tev = await self._emit(EventType.TASK_CHANGED, TaskChanged(task=t), cause)
            self._state = belief.apply(self._state, tev)
        return changed

    def _recall_extra(self) -> dict[str, float]:
        """Features de trigger que no salen del estado por sí solo. `wind_shift_deg`
        es el giro del viento respecto al rumbo que el plan vigente daba por bueno;
        sin plan aún, no hay giro que medir."""
        if self._plan is None:
            return {}
        for a in self._plan.context.assumptions:
            if a.key == "wind:bearing_deg" and isinstance(a.expected, (int, float)):
                gap = _angular_gap(self._state.wind.bearing_deg, float(a.expected))
                return {"wind_shift_deg": gap}
        return {}

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

    async def _after_plan(self, plan: Plan, cause: Event) -> None:
        """Lo que sale de un plan nuevo, en este orden: órdenes al sim, llamadas
        salientes y la señal a la llamada en curso que lo provocó."""
        await self._emit_actions(plan, cause)
        await self._emit_calls(plan, cause)
        await self._emit_signal(plan, cause)
        await self._emit_awaited_signals(plan)

    async def _emit_actions(self, plan: Plan, cause: Event) -> None:
        """Un `goto` por asignación con ruta nueva. Diffear evita reenviar la misma
        orden en cada replan; pero una arista cortada por un hecho de llamada cambia
        la ruta sin cambiar la tarea, y sin reenviar el `goto` el sim sigue por la
        pista cortada (visto en la integración 1). Una ruta que es un sufijo de la ya
        ordenada (la unidad avanza por ella, o dos frentes se atacan desde el mismo
        waypoint) no se reenvía: cada `goto` repetido ponía al camión `moving` un
        segundo y le cortaba el sofocado."""
        current = {a.unit_id: (a.task_id, tuple(a.route)) for a in plan.assignments}
        for a in plan.assignments:
            last = self._last_actions.get(a.unit_id)
            if last is not None and _same_way(last[1], current[a.unit_id][1]):
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

    async def _emit_calls(self, plan: Plan, cause: Event) -> None:
        """Una orden de evacuación (`call.requested`) por tarea `evacuate` recién
        asignada. Una sola llamada por tarea y run. El número: `JUDGE_PHONE` si está
        (existe para cambiarlo cinco minutos antes de subir al escenario) y, si no,
        el `contact_phone` del POI; sin ninguno se anota una vez y no se llama."""
        for a in plan.assignments:
            task = self._state.tasks.get(a.task_id)
            if task is None or task.kind != "evacuate" or task.id in self._called:
                continue
            poi = self._state.pois.get(task.target_poi or "")
            if poi is None:
                continue
            to = settings.judge_phone or poi.contact_phone
            if not to:
                if not self._warned_no_phone:
                    log.warning(
                        "sin JUDGE_PHONE ni contact_phone: no se llama (tarea %s)",
                        task.id,
                    )
                    self._warned_no_phone = True
                continue
            req = calls.evacuation_call(
                task, poi, a, to, self.scenario.hazard.kind, self._state.roads
            )
            self._called.add(task.id)
            await self._emit(EventType.CALL_REQUESTED, req, cause)

    async def _emit_signal(self, plan: Plan, cause: Event) -> None:
        """Si el plan lo provocó un hecho de una llamada en curso (`source=call:<id>`),
        el agente lo cuenta en vivo: `unit_dispatched` con la unidad que va al POI del
        hecho (o, si el hecho no nombra POI, la de menor ETA). `causes` apunta al
        hecho, que es lo que cierra la cadena llamada → hecho → replan → señal."""
        if cause.type != EventType.WORLD_FACT_ASSERTED:
            return
        if not cause.source.startswith(CALL_SOURCE_PREFIX):
            return
        call_id = cause.source.removeprefix(CALL_SOURCE_PREFIX)
        key = str(cause.payload.get("key", ""))
        a = self._assignment_for_fact(plan, key)
        if a is None:
            poi_id = _poi_of_key(key)
            if poi_id is not None:
                # Su tarea existe pero nadie la sirve todavía (la única ambulancia
                # está acabando otra cosa): se le contará con el plan que la asigne.
                self._awaiting[call_id] = (poi_id, cause)
            log.info("replan por la llamada %s sin asignación que contar", call_id)
            return
        self._awaiting.pop(call_id, None)
        await self._signal_assignment(call_id, a, cause)

    async def _emit_awaited_signals(self, plan: Plan) -> None:
        """Llamadas que esperan a que su POI tenga unidad: con este plan, si la tiene,
        salen. `causes` sigue apuntando al hecho de la llamada."""
        for call_id, (poi_id, fact_ev) in list(self._awaiting.items()):
            a = self._assignment_for_poi(plan, poi_id)
            if a is None:
                continue
            del self._awaiting[call_id]
            await self._signal_assignment(call_id, a, fact_ev)

    async def _signal_assignment(self, call_id: str, a: Assignment, cause: Event) -> None:
        unit = self._state.units.get(a.unit_id)
        if unit is None:
            return
        route = calls.route_name(a.route, self._state.roads)
        novelty = (a.unit_id, a.task_id, route)
        if self._signalled.get(call_id) == novelty:
            return  # ya se le dijo: otro hecho de la misma llamada no lo repite
        self._signalled[call_id] = novelty
        await self._emit(
            EventType.CALL_SIGNAL_REQUESTED,
            SignalRequested(
                call_id=call_id,
                key=SIGNAL_UNIT_DISPATCHED,
                payload={
                    "unit": calls.unit_name(unit),
                    "route": route,
                    "eta_s": int(a.eta_s),
                },
            ),
            cause,
        )

    def _assignment_for_fact(self, plan: Plan, key: str) -> Assignment | None:
        """La asignación que se le cuenta al vecino: la que sirve al POI del hecho
        (`poi:<id>:...`) aunque la unidad ya esté allí; si el hecho es de otra cosa
        (una arista cortada), la unidad en marcha hacia personas (evacuate/rescue)
        con menor ETA, que es la que ha cambiado de pista. Un camión parado en su
        celda de extinción (ETA 0) no es "ya va", y no se cuenta."""
        if not plan.assignments:
            return None
        poi_id = _poi_of_key(key)
        if poi_id is not None:
            return self._assignment_for_poi(plan, poi_id)
        moving = [
            a
            for a in plan.assignments
            if a.eta_s > 0
            and (t := self._state.tasks.get(a.task_id)) is not None
            and t.kind in PEOPLE_TASKS
        ]
        return min(moving, key=lambda a: a.eta_s) if moving else None

    def _assignment_for_poi(self, plan: Plan, poi_id: str) -> Assignment | None:
        for a in plan.assignments:
            task = self._state.tasks.get(a.task_id)
            if task is not None and task.target_poi == poi_id:
                return a
        return None

    async def _emit(self, etype: EventType, payload, cause: Event) -> Event:
        """Envuelve un payload Pydantic en un `Event` de source=core y lo publica.
        `causes=[cause.seq]` da al dashboard la cadena hecho → violación → replan → orden.
        Devuelve el evento ya sellado por el bus (con `seq`)."""
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
        return ev


def _same_way(ordered: tuple[str, ...], route: tuple[str, ...]) -> bool:
    """¿`route` es la ruta ya ordenada o un tramo final de ella?"""
    return len(route) <= len(ordered) and ordered[len(ordered) - len(route) :] == route


def _severity_before(before: dict[str, Task], task: Task) -> str | None:
    prev = before.get(task.id)
    return prev.severity if prev is not None else None


def _poi_of_key(key: str) -> str | None:
    """`poi:<id>:<attr>` → `<id>`; cualquier otra clave (arista, viento) → None."""
    seg = key.split(":")
    return seg[1] if len(seg) == 3 and seg[0] == "poi" else None


def _trigger(hard: list[Violation], criticals: list[Fact], value: float) -> str:
    """El disparador que levantó la bandera, con la misma prioridad que `should_replan`."""
    if hard:
        return "hard_violation"
    if criticals:
        return "critical_fact"
    return "divergence"
