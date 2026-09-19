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
import math
from datetime import UTC, datetime

from contracts.calls import CallResult, Fact
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
from contracts.world import POI, Task, Unit, WorldState
from core import belief, calls, memory, planner, tasks
from core.divergence import _angular_gap, divergence, should_replan
from core.planner import neutral_policy
from core.solver import (
    RoadGraph,
    attack_waypoint,
    intercept_waypoint,
    solve_with_violations,
)
from core.verifiers import verify

log = logging.getLogger("core.loop")

VETO_TTL_S = 300.0
"""Lo que dura un `veto_assignment` humano."""

CALL_SOURCE_PREFIX = "call:"
"""`source` de un hecho que vino de una llamada en curso: `call:<session_id>`."""

SIGNAL_UNIT_DISPATCHED = "unit_dispatched"
PEOPLE_TASKS = frozenset({"evacuate", "rescue"})
"""Tareas que van hacia personas: las únicas que se cuentan como "ya va" por teléfono."""
AT_THE_DOOR_M = 60.0
"""Metros a los que el frente deja de ser una amenaza y es una emergencia propia: el
pueblo recibe su orden de evacuación aunque otro esté peor. Mismo umbral que
`tasks.CRITICAL_DISTANCE_M`, y por el mismo motivo."""

RESOLVE_GAP_S = 5.0
"""Cadencia mínima (segundos de sim) entre re-solves por altas de extinción."""

DWELL_S = 25.0
"""Permanencia mínima: una unidad en marcha hacia un destino, o parada en él, no cambia
de destino hasta que pasen estos segundos de sim desde su último `goto`, salvo que su
tarea cierre, que aparezca una tarea crítica sin unidad que ella pueda servir, o que
su frente ya no se ataque desde ahí. Se aplica como coste de cambio en el solver
(`solver.HOLD_PENALTY_M`), así el plan y lo que hacen las unidades no divergen."""

AT_WAYPOINT_M = 3.0
"""A menos de esto de un waypoint, la unidad está en él (el sim la deja clavada en
sus coordenadas al llegar)."""

DISPATCH_RING_S = 45.0
"""Lo que se espera a que DESCUELGUEN, desde que se pide la llamada.

Si en este plazo no ha llegado `call.started`, nadie va a coger: la unidad sale y se
anota. Mismo plazo que el `no_answer` de una saliente en `docs/interfaces.md`."""

DISPATCH_TALK_S = 150.0
"""Lo que se espera a que CUELGUEN, desde que descuelgan.

Un teléfono que suena sin que nadie lo coja y una conversación en curso no son lo
mismo, y medirlos con la misma vara fue un error: medido en
`runs/run_4699e9e46f2f.jsonl`, la llamada real al retén duró 66 s —descolgó a los 3 y
colgó a los 69— y un plazo único de 45 la cortaba por la mitad, soltando los camiones
con `dispatch_confirmed=False` mientras la dotación seguía hablando. Una llamada
enlatada dura 30 s; una con una persona delante, el doble. Este plazo solo existe
para que una sesión que se queda abierta no congele la demo."""

DISPATCH_ROLES = frozenset({"fire_crew", "ambulance"})
"""Los `role` de llamada que retienen a su unidad. `ambulance_queued` no: esa unidad
está ocupada en otra cosa y la llamada solo pregunta cuándo quedará libre."""

RETURN_TASK = "return_to_base"
"""Marcador en `_last_actions` de un `goto` de vuelta a base: no es una tarea."""

HOME_CAPABILITIES = frozenset({"transport", "medical"})
"""Unidades que vuelven a su base cuando se quedan libres: la ambulancia al hospital.
Un camión libre se queda donde está (el fuego vuelve)."""


RESCUES: frozenset[str] = frozenset({"evacuate", "rescue"})
"""Tareas cuya llegada mueve gente. Espejo de `tasks.ARRIVAL_CLOSES`: lo que se
cierra al llegar es también lo que hay que ejecutar al llegar."""

SUBSCRIBED: tuple[EventType, ...] = tuple(
    t for t in EventType if t.value.startswith("world.")
) + (EventType.CALL_STARTED, EventType.CALL_ENDED, EventType.HUMAN_OVERRIDE)
"""`world.*` (incluye `world.fact.asserted`), `call.started`, `call.ended` y
`human.override`. Las dos de llamada, porque una unidad retenida se suelta al colgar
y el plazo cambia al descolgar."""


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
        self._goto_t: dict[str, float] = {}  # unit_id -> t_sim del último goto
        # unit_id -> waypoint de origen (el más cercano a su posición del escenario):
        # a donde vuelve una unidad de transporte cuando se queda libre.
        self._home: dict[str, str] = {
            u.id: wp
            for u in scenario.units
            if (wp := self.graph.nearest_waypoint(u.x, u.z)) is not None
        }
        self._last_divergence: tuple[float, tuple[str, ...]] | None = None
        self._vetoes: dict[tuple[str, str], float] = {}  # (unit, task) -> expiry t_sim
        self._action_seq = 0
        self._called: set[str] = set()  # task_ids con `call.requested` ya emitido
        # pares (poi_id que arde, poi_id vecino) ya avisados de que puede llegarles
        # gente: una vez por par y run, igual que `_called`.
        self._neighbor_called: set[tuple[str, str]] = set()
        # Medios ya avisados por teléfono (unit_id): al retén se le llama una vez por
        # run, y a la ambulancia una vez por rescate.
        self._crew_called = False
        self._ambulance_called: set[str] = set()
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
        # --- despacho por teléfono: nadie sale hasta que su dotación conteste ---
        # unit_id → task_id de la llamada que la retiene, y t_sim en que empezó.
        self._held: dict[str, str] = {}
        self._held_since: dict[str, float] = {}
        # task_ids cuya llamada ya está descolgada: a partir de ahí el plazo que
        # aplica es el de conversación, no el de que suene.
        self._talking: set[str] = set()
        # unit_id → (task_id, ruta) del `goto` que se le debe cuando se suelte. Se
        # sobreescribe en cada replan, así una unidad retenida sale por la ruta
        # buena y no por la que se calculó al descolgar.
        self._pending_goto: dict[str, tuple[str, tuple[str, ...]]] = {}
        # unit_id → si salió con confirmación. Viaja al `action.requested`.
        self._dispatch_confirmed: dict[str, bool] = {}
        self._dispatch_called: set[str] = set()  # unidades ya telefoneadas
        # task_id → (poi_id, asignación) de la orden de evacuación que espera a que
        # los medios confirmen. Se construye al soltar, no al diferir: así la orden
        # lleva los medios que van de verdad y no los que se pensaban mandar.
        self._deferred_calls: dict[str, tuple[str, Assignment | None]] = {}

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
        # Despacho: un «no podemos» quita la retención, colgar la suelta, y el plazo
        # la suelta igual. Va antes de planificar para que el plan de este evento ya
        # vea las unidades liberadas.
        self._drop_unavailable_holds()
        if ev.type == EventType.CALL_STARTED:
            self._on_call_started(ev)
        if ev.type == EventType.CALL_ENDED:
            await self._on_call_ended(ev)
        await self._expire_holds(ev)
        if not self._held and self._deferred_calls:
            await self._flush_deferred_calls(ev)
        before = self._state.tasks
        changed = await self._sync_tasks(ev)
        await self._emit_rescue(changed, ev)

        # Plan inicial: en cuanto hay tareas abiertas y aún no hay plan.
        if self._plan is None:
            if any(not t.done for t in self._state.tasks.values()):
                await self.replan("plan inicial", "divergence", ev)
            return

        value, broken = divergence(self._state, self._plan.context)
        # Solo cuando cambia: publicarlo por cada evento era el 46 % del journal
        # (1831 informes con 0.0 seguidos).
        if self._last_divergence != (value, tuple(broken)):
            self._last_divergence = (value, tuple(broken))
            await self._emit(
                EventType.PLAN_DIVERGENCE,
                DivergenceReport(value=value, broken=broken),
                ev,
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
        return solve_with_violations(
            self._state, policy, self.graph, vetoes=vetoes, holds=self._holds()
        )

    def _holds(self) -> dict[str, str]:
        """Unidad → waypoint que no debe abandonar todavía (`DWELL_S` desde su último
        `goto`, en marcha hacia él o parada en él). Se levanta si su tarea cerró, si
        hay una tarea crítica sin unidad que ella pueda servir (con el plan vigente)
        o si su frente ya no se ataca desde ese waypoint."""
        holds: dict[str, str] = {}
        served = (
            {a.task_id for a in self._plan.assignments}
            if self._plan is not None
            else set()
        )
        orphans = [
            t
            for t in self._state.tasks.values()
            if not t.done and t.severity == "critical" and t.id not in served
        ]
        for unit_id, (task_id, route) in self._last_actions.items():
            t_goto = self._goto_t.get(unit_id)
            if t_goto is None or self._state.t_sim - t_goto >= DWELL_S:
                continue
            unit = self._state.units.get(unit_id)
            if unit is None or not route:
                continue
            dest = route[-1]
            en_route = unit.status == "moving"
            parked = unit.status in ("idle", "working") and self._at_waypoint(unit, dest)
            if not (en_route or parked):
                continue
            if task_id == RETURN_TASK:
                continue  # una vuelta a base cede ante cualquier tarea
            task = self._state.tasks.get(task_id)
            if task is None or task.done:
                continue
            if any(o.required_capability in unit.capabilities for o in orphans):
                continue
            if not self._useful_at(task, dest):
                continue
            holds[unit_id] = dest
        return holds

    def _useful_at(self, task: Task, wp_id: str) -> bool:
        """¿Sigue teniendo sentido que la unidad esté (o vaya) a ese waypoint por
        esa tarea? Para una tarea sobre un POI, si es su waypoint. Para un frente,
        si es desde donde se ataca su celda objetivo, si es donde el fuego va a
        tocar carretera (cortafuegos) o si desde allí sofoca alguna celda que arde:
        el segundo camión trabaja el mismo frente desde otro sitio, y no hay que
        moverlo porque la cabeza del frente se haya corrido una celda."""
        if task.target_cell is None:
            return self._task_waypoint(task) == wp_id
        cell = self._state.cells.get(task.target_cell)
        live = self.graph.with_cuts(self._state)
        if cell is not None:
            x, z = self.graph.cell_center(cell)
            if wp_id in (
                attack_waypoint(x, z, self._state.wind, live),
                intercept_waypoint(x, z, self._state.wind, live),
            ):
                return True
        if wp_id not in self.graph.coords:
            return False
        return any(
            self.graph.waypoint_distance(wp_id, *self.graph.cell_center(c))
            <= self.graph.reach_m
            for c in self._state.cells.values()
            if c.state == "burning"
        )

    def _task_waypoint(self, task: Task) -> str | None:
        if task.target_poi is not None:
            poi = self._state.pois.get(task.target_poi)
            return poi.waypoint_id if poi is not None else None
        if task.target_cell is not None:
            cell = self._state.cells.get(task.target_cell)
            if cell is None:
                return None
            x, z = self.graph.cell_center(cell)
            return attack_waypoint(
                x, z, self._state.wind, self.graph.with_cuts(self._state)
            )
        return None

    def _at_waypoint(self, unit: Unit, wp_id: str) -> bool:
        if wp_id not in self.graph.coords:
            return False
        return self.graph.waypoint_distance(wp_id, unit.x, unit.z) <= AT_WAYPOINT_M

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
        """Lo que sale de un plan nuevo, en este orden: primero se PIDEN los medios,
        después se mueve lo que ya está confirmado, y solo entonces se le dicta la
        orden al pueblo, con los medios que van de verdad.

        El orden es el arreglo. Antes las acciones salían las primeras y las llamadas
        detrás: medido en `runs/run_25b73a7f8a7d.jsonl`, el camión llevaba 30 s
        conduciendo cuando el retén dijo «vamos», y la orden de evacuación colgaba en
        el mismo segundo en que el retén confirmaba, así que nadie llegó a contarle al
        pueblo qué le iba a llegar."""
        await self._emit_crew_call(plan, cause)
        await self._emit_ambulance_call(plan, cause)
        await self._emit_actions(plan, cause)
        await self._return_home(plan, cause)
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
        segundo y le cortaba el sofocado. Tampoco se ordena ir a donde ya está (ruta
        de un waypoint, unidad parada en él). Las unidades que quedan fuera del plan
        conservan su última orden: es lo que siguen haciendo."""
        self._inherit_holds(plan)
        self._drop_stale_holds(plan)
        for a in plan.assignments:
            route = tuple(a.route)
            if a.unit_id in self._held:
                # Aún no ha dicho «vamos». Se guarda la ruta ÚLTIMA, no la primera:
                # un replan mientras está al teléfono la saca por la pista buena.
                self._pending_goto[a.unit_id] = (a.task_id, route)
                continue
            last = self._last_actions.get(a.unit_id)
            if last is not None and _same_way(last[1], route):
                self._last_actions[a.unit_id] = (a.task_id, last[1])
                continue
            unit = self._state.units.get(a.unit_id)
            if (
                len(route) == 1
                and unit is not None
                and unit.status != "moving"
                and self._at_waypoint(unit, route[0])
            ):
                self._last_actions[a.unit_id] = (a.task_id, route)
                continue
            await self._goto(a.unit_id, a.task_id, list(route), cause)

    async def _goto(
        self,
        unit_id: str,
        task_id: str,
        route: list[str],
        cause: Event,
        confirmed: bool | None = None,
    ) -> None:
        self._action_seq += 1
        await self._emit(
            EventType.ACTION_REQUESTED,
            ActionRequested(
                action_id=f"act_{self.run_id}_{self._action_seq}",
                verb="goto",
                args={"unit_id": unit_id, "route": route},
                dispatch_confirmed=confirmed,
            ),
            cause,
        )
        self._last_actions[unit_id] = (task_id, tuple(route))
        self._goto_t[unit_id] = self._state.t_sim

    async def _return_home(self, plan: Plan, cause: Event) -> None:
        """Una unidad de transporte libre (sin asignación, parada) que no está en su
        waypoint de origen vuelve a él, una vez. La ambulancia evacuaba Pueblo B y se
        quedaba allí: cuando llegaba el pin del vecino en Pueblo B «se le mandaba» una
        unidad con ETA 0 y nadie la veía llegar. Ahora sale del hospital."""
        assigned = {a.unit_id for a in plan.assignments}
        live = self.graph.with_cuts(self._state)
        for unit in sorted(self._state.units.values(), key=lambda u: u.id):
            if unit.id in assigned or unit.status != "idle":
                continue
            if not HOME_CAPABILITIES & set(unit.capabilities):
                continue
            home = self._home.get(unit.id)
            if home is None or self._at_waypoint(unit, home):
                continue
            last = self._last_actions.get(unit.id)
            if last is not None and last[0] == RETURN_TASK and last[1][-1] == home:
                continue  # ya se le ordenó una vez
            here = live.nearest_waypoint(unit.x, unit.z)
            route = live.shortest_path(here, home) if here else None
            if not route:
                log.info("%s libre sin ruta viva de vuelta a %s", unit.id, home)
                continue
            await self._goto(unit.id, RETURN_TASK, route, cause)

    async def _emit_rescue(self, changed: list[Task], cause: Event) -> None:
        """Llegar a un POI cierra su evacuación; mover a la gente es otra cosa.

        `sim.execute` acepta cuatro verbos y el core solo pedía `goto`, así que
        `rescue` no se ejecutaba nunca: la demo cantaba "Pueblo B evacuado" en el
        minuto 5 con los aldeanos plantados donde estaban, que es justo lo que un
        jurado mirando el mundo —y no el dashboard— nota. Y la condición de
        `tasks.py` de cerrar una evacuación "cuando todos sus grupos están `safe`"
        no podía cumplirse jamás, porque solo ese verbo pone un grupo a `safe`.

        Se emite con el CIERRE de la tarea, no con el plan: es la consecuencia de que
        la evacuación esté resuelta, no una asignación nueva. Dos cosas la cierran, y
        por eso hay dos causas válidas:

        - un `rescue`, cuando la ambulancia **llega** (`WORLD_UNIT_ARRIVED`);
        - una `evacuate`, cuando el pueblo **acepta la orden** por teléfono
          (`WORLD_FACT_ASSERTED` con `poi:<id>:confirmed`). Sin esta segunda causa,
          quitarle el vehículo a la evacuación dejaba a los vecinos plantados en el
          pueblo: el `/tp` al refugio solo lo hace este verbo.
        """
        if cause.type not in (
            EventType.WORLD_UNIT_ARRIVED,
            EventType.WORLD_FACT_ASSERTED,
        ):
            return
        shelter = next(
            (p.id for p in self._state.pois.values() if p.kind == "shelter"), None
        )
        if shelter is None:
            return
        for task in changed:
            if not task.done or task.kind not in RESCUES or task.target_poi is None:
                continue
            groups = sorted(
                g.id
                for g in self._state.civilians.values()
                if g.poi_id == task.target_poi and g.state != "safe"
            )
            if not groups:
                continue
            self._action_seq += 1
            await self._emit(
                EventType.ACTION_REQUESTED,
                ActionRequested(
                    action_id=f"act_{self.run_id}_{self._action_seq}",
                    verb="rescue",
                    args={"civ_ids": groups, "shelter_id": shelter},
                ),
                cause,
            )

    def _fire_distance(self, poi: POI) -> float:
        return calls.nearest_fire(self._state, poi, self.graph)[0]

    def _most_threatened_village(self) -> str | None:
        """El pueblo con el frente más cerca. Es el que se evacúa; a los demás se les
        avisa de que puede llegarles gente hasta que el fuego llegue a su puerta.

        `tasks._evacuate` abre una tarea de evacuación por cada pueblo en cuanto arde
        una celda en cualquier parte del mapa, así que sin esto los dos pueblos del
        valle reciben la misma orden con quince segundos de diferencia: medido en el
        ensayo de las 16:40, la segunda llamada dio «ocupado» porque la primera seguía
        abierta en el mismo teléfono."""
        villages = [p for p in self._state.pois.values() if p.kind == "village"]
        if not villages:
            return None
        best = min(villages, key=lambda p: (self._fire_distance(p), p.id))
        return best.id if math.isfinite(self._fire_distance(best)) else None

    def _fire_at_the_door(self, poi: POI) -> bool:
        """El frente encima: ya no vale avisarle, hay que sacarlos."""
        return self._fire_distance(poi) <= AT_THE_DOOR_M

    # --- despacho por teléfono: nadie sale hasta que su dotación conteste ------

    def _hold_unit(self, unit_id: str, task_id: str) -> None:
        """Retiene a una unidad hasta que contesten al teléfono. Idempotente: un
        replan que vuelve a pasar por aquí no reinicia el plazo."""
        if not unit_id or unit_id in self._held:
            return
        self._held[unit_id] = task_id
        self._held_since[unit_id] = self._state.t_sim
        self._talking.discard(task_id)
        self._dispatch_called.add(unit_id)
        log.info("%s retenida a la espera de la llamada de %s", unit_id, task_id)

    async def _release_unit(self, unit_id: str, confirmed: bool, cause: Event) -> None:
        """Suelta a la unidad y le manda el `goto` que le debía."""
        self._held.pop(unit_id, None)
        self._held_since.pop(unit_id, None)
        pending = self._pending_goto.pop(unit_id, None)
        self._dispatch_confirmed[unit_id] = confirmed
        if pending is None:
            return
        task_id, route = pending
        await self._goto(unit_id, task_id, list(route), cause, confirmed=confirmed)

    def _drop_hold(self, unit_id: str) -> None:
        """Quita la retención sin mandar nada: esa unidad ya no sale. Los dos casos
        son «no podemos» y «se cayó del plan» (la avería de los 240 s)."""
        self._held.pop(unit_id, None)
        self._held_since.pop(unit_id, None)
        self._pending_goto.pop(unit_id, None)

    def _inherit_holds(self, plan: Plan) -> None:
        """Si el solver releva a la unidad de una tarea que está al teléfono, la que
        entra hereda la retención.

        La retención es de la TAREA, no de la unidad: quien la sirva, espera. Medido en
        `runs/run_bc8247ef1ed1.jsonl`, la llamada salió pidiendo `unit_ambulance2`
        (seq 519) y seis eventos después un replan puso a `unit_ambulance` en el mismo
        rescate (seq 524): salió con `dispatch_confirmed=null` mientras la dotación
        todavía estaba descolgando, que es justo lo que el despacho existe para evitar.

        No se toca `_talking` ni se reinicia el reloj: es la misma llamada, y un relevo
        no le puede regalar otros 45 segundos a quien ya está sonando."""
        en_llamada = set(self._held.values())
        if not en_llamada:
            return
        for a in plan.assignments:
            if a.task_id not in en_llamada or a.unit_id in self._held:
                continue
            desde = min(
                (
                    t0
                    for u, t0 in self._held_since.items()
                    if self._held.get(u) == a.task_id
                ),
                default=self._state.t_sim,
            )
            self._held[a.unit_id] = a.task_id
            self._held_since[a.unit_id] = desde
            self._dispatch_called.add(a.unit_id)
            log.info(
                "%s releva en %s mientras se telefonea: hereda la espera",
                a.unit_id,
                a.task_id,
            )

    def _drop_stale_holds(self, plan: Plan) -> None:
        """Una unidad retenida que ya no está en el plan deja de estarlo: no hay nada
        que mandarle y no tiene sentido que bloquee la orden del pueblo."""
        asignadas = {a.unit_id for a in plan.assignments}
        for unit_id in [u for u in self._held if u not in asignadas]:
            log.info("%s sale del plan mientras estaba retenida: se suelta", unit_id)
            self._drop_hold(unit_id)

    def _drop_unavailable_holds(self) -> None:
        """Un «no podemos salir» entra como `unit:<id>:available=false` por el tool,
        antes de que cuelguen. Esa unidad no sale: el solver reparte sin ella."""
        for unit_id in list(self._held):
            unit = self._state.units.get(unit_id)
            if unit is not None and unit.status == "unavailable":
                log.info("%s no puede salir: se retira su orden pendiente", unit_id)
                self._drop_hold(unit_id)

    def _on_call_started(self, ev: Event) -> None:
        """Han descolgado: el plazo pasa a ser el de conversación y el reloj vuelve a
        cero. Sin esto, los segundos que tardan en coger el teléfono se le descontaban
        a la conversación y la llamada se cortaba a media frase."""
        task_id = str(ev.payload.get("task_id") or "")
        if not task_id or task_id not in set(self._held.values()):
            return
        self._talking.add(task_id)
        for unit_id, t in self._held.items():
            if t == task_id:
                self._held_since[unit_id] = self._state.t_sim

    async def _on_call_ended(self, ev: Event) -> None:
        """Colgar suelta a las unidades de esa llamada. `call.ended` llevaba en
        `SUBSCRIBED` desde el principio y no lo trataba nadie."""
        result = CallResult.model_validate(ev.payload)
        if result.task_id is None:
            return
        self._talking.discard(result.task_id)
        # Contestaron y no dijeron que no (un «no podemos» ya habrá quitado la
        # retención por `unit:<id>:available`): sale confirmada.
        confirmed = result.outcome in ("answered", "hung_up")
        for unit_id in [u for u, t in self._held.items() if t == result.task_id]:
            await self._release_unit(unit_id, confirmed, ev)

    async def _expire_holds(self, cause: Event) -> None:
        """El plazo. Nadie contestó: la unidad sale igual y el `action.requested` lo
        dice (`dispatch_confirmed=False`). Degradar y anotar, nunca congelarse."""
        vencidas = [
            u
            for u, t0 in self._held_since.items()
            if self._state.t_sim - t0
            >= (DISPATCH_TALK_S if self._held.get(u) in self._talking else DISPATCH_RING_S)
        ]
        for unit_id in vencidas:
            hablando = self._held.get(unit_id) in self._talking
            log.warning(
                "%s en %.0f s de sim: %s sale sin confirmar",
                "la llamada no cerró" if hablando else "nadie descuelga",
                DISPATCH_TALK_S if hablando else DISPATCH_RING_S,
                unit_id,
            )
            await self._release_unit(unit_id, False, cause)

    def _committed_resources(self) -> str:
        """Los medios que van de verdad, para la orden de evacuación: los del plan
        vigente cuya unidad sigue en servicio, y los telefoneados que se han caído."""
        if self._plan is None:
            return ""
        vivos = [
            a
            for a in self._plan.assignments
            if (u := self._state.units.get(a.unit_id)) is not None
            and u.status != "unavailable"
        ]
        caidos = [
            u
            for uid in sorted(self._dispatch_called)
            if (u := self._state.units.get(uid)) is not None and u.status == "unavailable"
        ]
        return calls.committed_resources_line(
            self._state, vivos, caidos, self._state.roads, self.scenario.road_aliases
        )

    async def _flush_deferred_calls(self, cause: Event) -> None:
        """Las órdenes de evacuación que esperaban a los medios. Se construyen AHORA,
        no cuando se difirieron: por eso llevan los medios confirmados."""
        for task_id, (poi_id, guardada) in list(self._deferred_calls.items()):
            del self._deferred_calls[task_id]
            task = self._state.tasks.get(task_id)
            poi = self._state.pois.get(poi_id)
            if task is None or poi is None:
                continue
            vigente = (
                self._assignment_for_poi(self._plan, poi_id)
                if self._plan is not None
                else None
            )
            await self._emit_evacuation(task, poi, vigente or guardada, cause)

    async def _emit_evacuation(
        self, task: Task, poi: POI, a: Assignment | None, cause: Event
    ) -> None:
        """La orden al pueblo, y detrás el aviso a sus vecinos."""
        to = settings.judge_phone or poi.contact_phone
        if not to:
            return
        req = calls.evacuation_call(
            task,
            poi,
            a,
            to,
            self.scenario.hazard.kind,
            self._state.roads,
            self.scenario.road_aliases,
            state=self._state,
            graph=self.graph,
            committed=self._committed_resources(),
        )
        await self._emit(EventType.CALL_REQUESTED, req, cause)
        await self._emit_neighbor_calls(task, poi, cause)

    async def _emit_calls(self, plan: Plan, cause: Event) -> None:
        """Una orden de evacuación (`call.requested`) por tarea `evacuate` abierta,
        y solo al pueblo que la necesita: el que tiene el fuego más cerca o
        el que ya lo tiene en la puerta. Al resto se les llama aparte, con el aviso de
        que pueden recibir gente (`_emit_neighbor_calls`).

        Una sola llamada por tarea y run. El número: `JUDGE_PHONE` si está (existe
        para cambiarlo cinco minutos antes de subir al escenario) y, si no, el
        `contact_phone` del POI; sin ninguno se anota una vez y no se llama.

        **Espera a los medios.** Mientras haya una unidad retenida al teléfono no se
        sabe qué va a llegarle al pueblo, y una orden de evacuación que promete un
        camión que no ha confirmado es justo la clase de promesa que no se puede
        cumplir. La orden se difiere y sale con `committed_resources` de verdad."""
        amenazado = self._most_threatened_village()
        # Se itera la TAREA, no la asignación: una evacuación ya no lleva vehículo
        # (los vecinos que pueden andar se van solos), y colgar la llamada de
        # `plan.assignments` dejaba al pueblo sin orden justo cuando se le quitó la
        # ambulancia. La asignación, si la hay, sigue viajando para el `unit_eta`.
        abiertas = [
            t
            for t in sorted(self._state.tasks.values(), key=lambda t: t.id)
            if t.kind == "evacuate" and not t.done and t.id not in self._called
        ]
        for task in abiertas:
            poi = self._state.pois.get(task.target_poi or "")
            if poi is None:
                continue
            a = self._assignment_for_poi(plan, poi.id)
            # Todavía no es su emergencia: a ese le toca el aviso, no la orden.
            if (
                poi.kind == "village"
                and poi.id != amenazado
                and not self._fire_at_the_door(poi)
            ):
                continue
            if not (settings.judge_phone or poi.contact_phone):
                if not self._warned_no_phone:
                    log.warning(
                        "sin JUDGE_PHONE ni contact_phone: no se llama (tarea %s)",
                        task.id,
                    )
                    self._warned_no_phone = True
                continue
            # `_called` se marca al DECIDIR llamar, no al emitir: si no, el siguiente
            # tick volvería a decidir lo mismo y la orden se duplicaría. La que se
            # difiere sale igual, por `_flush_deferred_calls`, y los plazos de
            # `DISPATCH_RING_S`/`DISPATCH_TALK_S` garantizan que ese momento llega.
            self._called.add(task.id)
            if self._held:
                self._deferred_calls[task.id] = (poi.id, a)
                log.info(
                    "orden de evacuación de %s en espera: %d medio(s) sin confirmar",
                    poi.id,
                    len(self._held),
                )
                continue
            await self._emit_evacuation(task, poi, a, cause)

    async def _emit_neighbor_calls(
        self, source_task: Task, source_poi: POI, cause: Event
    ) -> None:
        """El pueblo vecino de uno que se evacúa se entera de que puede llegarle
        gente: una llamada aparte, distinta de la orden de evacuación, una vez por
        par de pueblos.

        Quién es "el vecino" no lo decide la existencia de su tarea de evacuación
        —`tasks._evacuate` abre una por cada pueblo en cuanto hay fuego en el mapa—
        sino la distancia al frente: se evacúa al que lo tiene más cerca y se avisa a
        los demás. Cuando el fuego llega a la puerta de uno de ellos
        (`AT_THE_DOOR_M`), `_emit_calls` le llama con su propia orden."""
        for poi in sorted(self._state.pois.values(), key=lambda p: p.id):
            if poi.id == source_poi.id or poi.kind != "village":
                continue
            pair = (source_poi.id, poi.id)
            if pair in self._neighbor_called:
                continue
            if tasks.evac_task_id(poi.id) in self._called:
                continue  # ya se le ha dictado su propia orden de evacuación
            if self._fire_at_the_door(poi):
                continue  # el fuego ya le llega: lo suyo es una orden, no un aviso
            # `NEIGHBOR_PHONE` antes que `JUDGE_PHONE`: en el ensayo las dos llamadas
            # iban al mismo móvil y se pisaban. Quien está a salvo tiene su número.
            to = settings.neighbor_phone or settings.judge_phone or poi.contact_phone
            if not to:
                continue
            self._neighbor_called.add(pair)
            req = calls.neighbor_alert_call(
                source_task,
                poi,
                source_poi,
                to,
                self.scenario.hazard.kind,
                state=self._state,
                graph=self.graph,
                aliases=self.scenario.road_aliases,
            )
            await self._emit(EventType.CALL_REQUESTED, req, cause)

    def _free_unit(self, capability: str, for_task: str) -> Unit | None:
        """La unidad que puede coger el teléfono y salir a ESA tarea: ni averiada, ni
        metida en OTRA tarea abierta.

        «Libre» no es «sin asignar»: cuando se llama al retén el solver ya le ha dado
        el frente, y de eso va la llamada — confirmar que pueden ir. Lo que descarta
        a una unidad es estar en otra cosa distinta, que es la condición del guion
        para la ambulancia («no ocupada con otra cosa»)."""
        for unit in sorted(self._state.units.values(), key=lambda u: u.id):
            if capability not in unit.capabilities or unit.status == "unavailable":
                continue
            otra = self._state.tasks.get(unit.task_id or "")
            if otra is not None and not otra.done and otra.id != for_task:
                continue  # ocupada con otra cosa
            return unit
        return None

    async def _emit_crew_call(self, plan: Plan, cause: Event) -> None:
        """Al retén, en cuanto hay una tarea de extinción: hay fuego, ¿pueden salir?

        Una vez por run. Si no hay ningún camión en condiciones no se llama: no se
        moviliza a quien no puede ir, y el teléfono queda libre para el resto."""
        if self._crew_called or not settings.fire_crew_phone:
            return
        task = next(
            (
                t
                for t in sorted(self._state.tasks.values(), key=lambda t: t.id)
                if t.kind == "extinguish" and not t.done
            ),
            None,
        )
        if task is None:
            return
        unit = self._free_unit("extinguish", task.id)
        if unit is None:
            log.info("fuego sin camión disponible: no se llama al retén")
            return
        station = next((p for p in self._state.pois.values() if p.kind == "base"), None)
        if station is None:
            return
        self._crew_called = True
        donde = self._where(task)
        req_crew = calls.fire_crew_call(
            task,
            station,
            settings.fire_crew_phone,
            self.scenario.hazard.kind,
            unit.id,
            donde,
            state=self._state,
            graph=self.graph,
            aliases=self.scenario.road_aliases,
            plan=plan,
            roads=self._state.roads,
        )
        await self._emit(EventType.CALL_REQUESTED, req_crew, cause)
        # Se retienen TODOS los camiones que la petición nombra, no solo el que
        # cogió `_free_unit`: la llamada dice «les pedimos dos camiones», así que
        # dejar salir al segundo mientras se pregunta por los dos es la misma
        # incoherencia que había antes, más pequeña.
        pedidas = {a.unit_id for a in plan.assignments if a.task_id == task.id}
        pedidas.update(
            a.unit_id
            for a in plan.assignments
            if (t := self._state.tasks.get(a.task_id)) is not None
            and t.kind == "extinguish"
        )
        pedidas.add(req_crew.facts.get("unit_id", ""))
        for unit_id in sorted(pedidas):
            self._hold_unit(unit_id, task.id)

    def _where(self, task: Task) -> str:
        """Dónde arde, dicho como se dice por teléfono: el pueblo más cercano al
        frente, que es lo que ubica a quien conduce."""
        cell = self._state.cells.get(task.target_cell or "")
        if cell is None:
            return "el valle"
        cx, cz = self.graph.cell_center(cell)
        villages = [
            p for p in self._state.pois.values() if p.kind in ("village", "landmark")
        ]
        if not villages:
            return "el valle"
        cerca = min(villages, key=lambda p: math.hypot(p.x - cx, p.z - cz))
        metros = int(round(math.hypot(cerca.x - cx, cerca.z - cz) / 10.0) * 10)
        return f"{metros} metros de {cerca.name}"

    async def _emit_ambulance_call(self, plan: Plan, cause: Event) -> None:
        """A la ambulancia, solo si alguien la ha pedido.

        «Pedida» es una tarea de rescate abierta, y un rescate solo nace de un hecho
        `poi:<id>:immobile` que ha entrado por una llamada: nadie inventa un rescate
        desde el mapa.

        Si hay una libre, se la manda. Si no queda ninguna, se llama a la que antes
        vaya a terminar para preguntarle en cuántos minutos estará libre y —si el
        caso es crítico— decirle que en cuanto acabe va allí. Esa respuesta vuelve al
        que sigue esperando al teléfono (`voice.webhooks`)."""
        if not settings.ambulance_phone:
            return
        rescue = next(
            (
                t
                for t in sorted(self._state.tasks.values(), key=lambda t: t.id)
                if t.kind == "rescue"
                and not t.done
                and t.id not in self._ambulance_called
            ),
            None,
        )
        if rescue is None:
            return
        poi = self._state.pois.get(rescue.target_poi or "")
        if poi is None:
            return
        unit = self._free_unit("transport", rescue.id)
        queued = unit is None
        if queued:
            # Ninguna libre: se llama igualmente, pero a preguntar CUÁNDO. Quien
            # pidió la ambulancia sigue al teléfono, y «ahora mismo no hay» no es
            # una respuesta: lo que se le puede dar son minutos.
            unit = self._busiest_unit("transport")
            if unit is None:
                log.info("rescate %s sin ninguna ambulancia en servicio", rescue.id)
                return
        base = next((p for p in self._state.pois.values() if p.kind == "hospital"), poi)
        grupos = [g for g in self._state.civilians.values() if g.poi_id == poi.id]
        immobile = max((g.immobile for g in grupos), default=1)
        injuries = max((g.injuries for g in grupos), default=0)
        self._ambulance_called.add(rescue.id)
        req = calls.ambulance_call(
            rescue,
            poi,
            base,
            settings.ambulance_phone,
            self.scenario.hazard.kind,
            unit.id,
            immobile,
            state=self._state,
            injuries=injuries,
            graph=self.graph,
            aliases=self.scenario.road_aliases,
            queued=queued,
            priority=rescue.severity == "critical",
            waiting_call_id=self._who_asked(poi.id),
            plan=plan,
            roads=self._state.roads,
        )
        await self._emit(EventType.CALL_REQUESTED, req, cause)
        # Una ambulancia en cola está ocupada en otra cosa y la llamada solo pregunta
        # cuándo quedará libre: no se le retiene nada.
        if req.facts.get("role") in DISPATCH_ROLES:
            # Y se retienen TODAS las que el plan manda a ese rescate, no solo la que
            # cogió `_free_unit`: dejar salir a la segunda mientras se pregunta por la
            # primera es la incoherencia que el retén ya tenía resuelta (`_emit_crew_call`).
            pedidas = {a.unit_id for a in plan.assignments if a.task_id == rescue.id}
            pedidas.add(req.facts.get("unit_id", ""))
            for unit_id in sorted(pedidas):
                self._hold_unit(unit_id, rescue.id)

    def _busiest_unit(self, capability: str) -> Unit | None:
        """La unidad de ese tipo que está en servicio, aunque ocupada: a la que se
        telefonea cuando no queda ninguna libre. Se prefiere la que antes va a
        terminar (menor ETA de su tarea en el plan vigente), porque es la que puede
        dar una respuesta útil a quien espera."""
        candidatas = [
            u
            for u in self._state.units.values()
            if capability in u.capabilities and u.status != "unavailable"
        ]
        if not candidatas:
            return None
        etas = {
            a.unit_id: a.eta_s for a in (self._plan.assignments if self._plan else [])
        }
        return min(candidatas, key=lambda u: (etas.get(u.id, 1e9), u.id))

    def _who_asked(self, poi_id: str) -> str:
        """La llamada que pidió el rescate de ese POI, para poder devolverle la
        respuesta de la ambulancia mientras sigue al teléfono. Es el `call_id` del
        hecho `poi:<id>:immobile` más reciente: un rescate no nace de otra cosa."""
        for fact in reversed(self._state.facts):
            if fact.key == f"poi:{poi_id}:immobile":
                return fact.call_id or fact.source.removeprefix(CALL_SOURCE_PREFIX)
        return ""

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
        route = calls.route_name(a.route, self._state.roads, self.scenario.road_aliases)
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
