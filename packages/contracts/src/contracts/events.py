"""El sobre y el catálogo de eventos.

Un solo sobre para todo lo que cruza el bus. Ningún evento se borra ni se edita:
si algo cambia, se emite otro evento. El journal es append-only.

El payload de cada tipo tiene un modelo Pydantic aquí y el bus lo valida al
publicar (`PAYLOAD_MODELS`). Un payload que no valida lanza en desarrollo y se
registra como `event.malformed` en la demo, nunca tumba el proceso.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from contracts.calls import CallRequest, CallResult, Severity, Urgency
from contracts.plan import Plan, Policy, Violation
from contracts.world import CellState, CivState, UnitStatus, Wind

Verb = Literal["goto", "set_marker", "announce", "rescue"]
"""`sim.execute` acepta exactamente estos cuatro. Cualquier otro emite
`action.failed` con `error="unknown_verb"`. P2 no añade verbos sin avisar;
P1 no inventa verbos."""

UNKNOWN_VERB = "unknown_verb"

OverrideKind = Literal[
    "force_assignment",
    "veto_assignment",
    "assert_fact",
    "force_replan",
    "set_priority",
]


class EventType(StrEnum):
    """El catálogo completo. Añadir un tipo es libre; borrar está prohibido."""

    WORLD_TICK = "world.tick"
    WORLD_CELL_CHANGED = "world.cell.changed"
    WORLD_UNIT_POSITION = "world.unit.position"
    WORLD_UNIT_STATUS = "world.unit.status"
    WORLD_UNIT_ARRIVED = "world.unit.arrived"  # backbone.md: lo emite goto al llegar
    WORLD_ROAD_CHANGED = "world.road.changed"
    WORLD_CIVILIANS_CHANGED = "world.civilians.changed"
    WORLD_FIRE_DETECTED = "world.fire.detected"  # backbone.md: la ignición inicial
    WORLD_INJECT = "world.inject"
    WORLD_FACT_ASSERTED = "world.fact.asserted"

    CALL_REQUESTED = "call.requested"
    CALL_STARTED = "call.started"
    CALL_TRANSCRIPT_PARTIAL = "call.transcript.partial"
    CALL_AFFECT = "call.affect"  # Humalike: emoción del interlocutor durante la llamada
    CALL_ENDED = "call.ended"
    CALL_SIGNAL_REQUESTED = "call.signal.requested"  # core → voice: algo que decir en vivo
    CALL_SIGNAL_SENT = "call.signal.sent"  # voice → HappyRobot lo ha recibido

    PLAN_DIVERGENCE = "plan.divergence"
    PLAN_REPLAN_STARTED = "plan.replan.started"
    PLAN_POLICY_EMITTED = "plan.policy.emitted"
    PLAN_VIOLATION = "plan.violation"
    PLAN_EMITTED = "plan.emitted"

    ACTION_REQUESTED = "action.requested"
    ACTION_COMPLETED = "action.completed"
    ACTION_FAILED = "action.failed"

    HUMAN_OVERRIDE = "human.override"

    RUN_STARTED = "run.started"
    RUN_ENDED = "run.ended"

    EVENT_MALFORMED = "event.malformed"


class Event(BaseModel):
    """El sobre. `seq` lo pone el bus; `causes` es opcional de escribir pero vale
    oro: es lo que permite al dashboard dibujar la cadena llamada → hecho →
    violación → replan → orden cuando el jurado pregunta por qué."""

    run_id: str  # uuid del run
    seq: int  # monotónico, lo pone el bus
    t_wall: datetime  # reloj de pared, solo para depurar y medir latencia
    t_sim: float  # segundos simulados desde el inicio, el tiempo del dominio
    type: EventType
    source: str  # "sim" | "core" | "voice" | "human" | "call:<id>"
    payload: dict  # validado contra el modelo del tipo
    causes: list[int] = []  # seqs que provocaron este evento


# --- Payloads: world.* -----------------------------------------------------


class WorldTick(BaseModel):
    t_sim: float
    wind: Wind


class CellChanged(BaseModel):
    cell_id: str
    state: CellState
    hazard: str


class UnitPosition(BaseModel):
    unit_id: str
    x: float
    z: float
    heading: float
    eta_s: float | None = None


class UnitStatusChanged(BaseModel):
    unit_id: str
    status: UnitStatus
    reason: str


class UnitArrived(BaseModel):
    unit_id: str
    waypoint_id: str


class RoadChanged(BaseModel):
    edge_id: str
    cut: bool
    cause: str | None = None


class CiviliansChanged(BaseModel):
    group_id: str
    count: int
    state: CivState
    poi_id: str


class FireDetected(BaseModel):
    cell_id: str
    hazard: str


class Inject(BaseModel):
    inject_type: str  # "wind_shift" | "road_cut" | "unit_failure" | ...
    detail: dict = {}


class FactAsserted(BaseModel):
    key: str
    value: str | float | bool
    confidence: float
    source: str
    severity: Severity


# --- Payloads: call.* ------------------------------------------------------


class CallStarted(BaseModel):
    call_id: str
    task_id: str | None = None
    to: str
    direction: Literal["outbound", "inbound"]


class TranscriptPartial(BaseModel):
    call_id: str
    speaker: str
    text: str


class Emotion(BaseModel):
    type: str  # "fear", "frustration", "relief"...
    intensity: float  # 0..1


class CallAffect(BaseModel):
    """Lo que Humalike lee del interlocutor en mitad de la llamada. Solo dashboard."""

    call_id: str
    emotions: list[Emotion] = []
    risk: float | None = None  # riesgo de que la siguiente frase aterrice mal


class SignalRequested(BaseModel):
    """El core pide que el agente diga algo en vivo. `payload` lleva `kind`
    (`unit_dispatched`, `coach`...) y sus campos; voice lo redacta y lo manda."""

    call_id: str
    key: str  # "unit_dispatched" | "coach"
    payload: dict = {}


class SignalSent(BaseModel):
    call_id: str
    key: str
    signal_id: str


# --- Payloads: plan.* ------------------------------------------------------


class DivergenceReport(BaseModel):
    value: float
    broken: list[str] = []


class ReplanStarted(BaseModel):
    reason: str
    trigger: str


# --- Payloads: action.* ----------------------------------------------------


class ActionRequested(BaseModel):
    action_id: str
    verb: Verb
    args: dict = {}


class ActionCompleted(BaseModel):
    action_id: str
    result: dict = {}


class ActionFailed(BaseModel):
    action_id: str
    error: str


# --- Payloads: human y run -------------------------------------------------


class HumanOverride(BaseModel):
    """Prioridad máxima en el core: un `assert_fact` humano entra con
    confidence=1.0, un `veto_assignment` pone coste infinito a ese par
    unidad-tarea durante 5 minutos, y un `force_replan` salta el umbral."""

    kind: OverrideKind
    target: str  # "unit_truck1" | "task_evac_a" | "road:wp_sur_03-wp_sur_04"
    value: str | float | bool | None = None
    note: str = ""


class RunStarted(BaseModel):
    scenario_id: str


class RunEnded(BaseModel):
    scenario_id: str
    score: float | None = None


class Malformed(BaseModel):
    type: str
    error: str
    raw: dict = {}


PAYLOAD_MODELS: dict[EventType, type[BaseModel]] = {
    EventType.WORLD_TICK: WorldTick,
    EventType.WORLD_CELL_CHANGED: CellChanged,
    EventType.WORLD_UNIT_POSITION: UnitPosition,
    EventType.WORLD_UNIT_STATUS: UnitStatusChanged,
    EventType.WORLD_UNIT_ARRIVED: UnitArrived,
    EventType.WORLD_ROAD_CHANGED: RoadChanged,
    EventType.WORLD_CIVILIANS_CHANGED: CiviliansChanged,
    EventType.WORLD_FIRE_DETECTED: FireDetected,
    EventType.WORLD_INJECT: Inject,
    EventType.WORLD_FACT_ASSERTED: FactAsserted,
    EventType.CALL_REQUESTED: CallRequest,
    EventType.CALL_STARTED: CallStarted,
    EventType.CALL_TRANSCRIPT_PARTIAL: TranscriptPartial,
    EventType.CALL_AFFECT: CallAffect,
    EventType.CALL_ENDED: CallResult,
    EventType.CALL_SIGNAL_REQUESTED: SignalRequested,
    EventType.CALL_SIGNAL_SENT: SignalSent,
    EventType.PLAN_DIVERGENCE: DivergenceReport,
    EventType.PLAN_REPLAN_STARTED: ReplanStarted,
    EventType.PLAN_POLICY_EMITTED: Policy,
    EventType.PLAN_VIOLATION: Violation,
    EventType.PLAN_EMITTED: Plan,
    EventType.ACTION_REQUESTED: ActionRequested,
    EventType.ACTION_COMPLETED: ActionCompleted,
    EventType.ACTION_FAILED: ActionFailed,
    EventType.HUMAN_OVERRIDE: HumanOverride,
    EventType.RUN_STARTED: RunStarted,
    EventType.RUN_ENDED: RunEnded,
    EventType.EVENT_MALFORMED: Malformed,
}

__all__ = [
    "PAYLOAD_MODELS",
    "UNKNOWN_VERB",
    "Event",
    "EventType",
    "OverrideKind",
    "Urgency",
    "Verb",
]
