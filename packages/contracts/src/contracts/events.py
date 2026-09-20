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

from contracts.calls import CallRequest, CallResult, FactKind, Severity, Urgency
from contracts.plan import Plan, Policy, Violation
from contracts.world import CellState, CivState, Task, UnitStatus, Wind

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

    TASK_CHANGED = "task.changed"  # core: una tarea nace, cambia de severidad o se cierra

    CITIZEN_LOCATION = (
        "citizen.location"  # Telegram: pin GPS del vecino, el «dónde» exacto
    )
    CALL_REQUESTED = "call.requested"
    CALL_STARTED = "call.started"
    CALL_TRANSCRIPT_PARTIAL = "call.transcript.partial"
    CALL_AFFECT = "call.affect"  # Humalike: emoción del interlocutor durante la llamada
    CALL_COMPLETENESS = "call.completeness"  # Jev: qué campos van resueltos en la llamada
    CALL_ENDED = "call.ended"
    CALL_SIGNAL_REQUESTED = (
        "call.signal.requested"  # core → voice: algo que decir en vivo
    )
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
    # Por qué cambió (opcional con default: libre). `extinguished` es un `burnt` que
    # ha puesto un camión, no el fuego: el sim lo pinta distinto y el dashboard también.
    cause: Literal["spread", "burnout", "extinguished", "inject", "at_risk"] | None = None


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
    kind: FactKind = "observed"
    call_id: str | None = None


# --- Payloads: task.* ------------------------------------------------------


class TaskChanged(BaseModel):
    """El core mantiene `WorldState.tasks` y, como el estado es inmutable y el
    journal append-only, cada alta, cambio de severidad o cierre (`done=True`) se
    publica entero: `belief.apply` lo pliega y el dashboard lo pinta."""

    task: Task


# --- Payloads: call.* ------------------------------------------------------


class CallStarted(BaseModel):
    call_id: str
    task_id: str | None = None
    to: str
    direction: Literal["outbound", "inbound"]
    channel: Literal["voice", "telegram"] = "voice"  # opcional con default: libre
    # La entrante viene del móvil que el visitante declaró como «vecino» en la /demo
    # (`PHONE_NEIGHBOR`): el dashboard la pinta como «tu llamada». Opcional con default.
    known_caller: bool = False


class CitizenLocation(BaseModel):
    """Un vecino comparte su ubicación por Telegram, ya colgada la llamada (o sin ella).
    `lat`/`lon` es lo que manda el móvil; `x`/`z` es su proyección al mundo del
    escenario (`Scenario.geo`), y `poi_id` el POI al que se ancla si cae a menos de
    `GeoAnchor.snap_m`. Sin anclaje, `poi_id=None`: el pin se pinta igual y el hueco
    queda visible, no adivinado."""

    call_id: str  # `tg_<chat_id>`: el canal Telegram es una «llamada» más para el journal
    channel: Literal["telegram"] = "telegram"
    chat_id: str
    lat: float
    lon: float
    x: float | None = None
    z: float | None = None
    poi_id: str | None = None
    poi_name: str | None = None
    text: str | None = None  # el texto que acompaña al pin, si lo hay
    live: bool = False  # ubicación en vivo (edited_message) o un pin suelto


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


FieldStatus = Literal["open", "observed", "asked", "assumed_default"]


class FieldCompleteness(BaseModel):
    key: str  # "location_hint" | "road_blocked" | "people_immobile" | "urgency"
    status: FieldStatus
    value: str | None = None
    confidence: float | None = None


class CallCompleteness(BaseModel):
    """El vector de completitud tras cada tick de Jev. Lo pinta el dashboard: huecos
    en gris que pasan a sólidos, o a gris cursiva si los rellenó el LLM."""

    call_id: str
    budget_s: float | None = None  # None hasta que `urgency` se resuelve
    elapsed_s: float = 0.0
    fields: list[FieldCompleteness] = []


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
    message: str | None = None  # lo que se le pidió decir al agente, para la tarjeta
    latency_ms: float | None = (
        None  # desde `call.signal.requested` hasta que HappyRobot aceptó
    )
    refined: bool | None = (
        None  # True si Humalike llegó a tiempo; False si fue el borrador
    )


# --- Payloads: plan.* ------------------------------------------------------


class DivergenceReport(BaseModel):
    value: float
    broken: list[str] = []


class ReplanStarted(BaseModel):
    reason: str
    trigger: str
    fired_rules: list[
        str
    ] = []  # slugs de reglas de memoria que casaron: lineage → dashboard


# --- Payloads: action.* ----------------------------------------------------


class ActionRequested(BaseModel):
    action_id: str
    verb: Verb
    args: dict = {}
    dispatch_confirmed: bool | None = None
    """Si la unidad salió con el «vamos» del medio al otro lado del teléfono.

    `None`: la acción no estaba condicionada a ninguna llamada (el caso normal).
    `True`: el retén o la dotación contestaron y la unidad salió al colgar.
    `False`: se agotó el plazo o la llamada no se pudo cerrar, y la unidad salió
    igual porque una demo congelada no es una degradación aceptable. Se anota, que
    es la regla: nunca un `except: pass`."""


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
    EventType.TASK_CHANGED: TaskChanged,
    EventType.CITIZEN_LOCATION: CitizenLocation,
    EventType.CALL_REQUESTED: CallRequest,
    EventType.CALL_STARTED: CallStarted,
    EventType.CALL_TRANSCRIPT_PARTIAL: TranscriptPartial,
    EventType.CALL_AFFECT: CallAffect,
    EventType.CALL_COMPLETENESS: CallCompleteness,
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
