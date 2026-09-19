"""Telefonía: lo que el core pide, lo que voice devuelve, lo que entra al estado.

El core pide una intención de llamada; voice decide plataforma, número y guion.
El core no sabe que existe HappyRobot y no debe saberlo.
"""

from typing import Literal

from pydantic import BaseModel, Field

Audience = Literal["resident", "responder", "official"]
Urgency = Literal["low", "medium", "critical"]
CallIntent = Literal[
    "evacuation_order",
    "resource_request",
    "status_check",
    "shelter_confirm",
]
CallOutcome = Literal["answered", "no_answer", "busy", "failed", "hung_up"]
Severity = Literal["low", "medium", "critical"]
FactKind = Literal["observed", "inferred", "assumed_default"]
"""Regla 4: un hecho asumido nunca se disfraza de observado. Solo `observed` puede
fundar una restricción dura o reabrir una arista cortada."""


class CallRequest(BaseModel):
    """Lo que el core publica en `call.requested`. Intención, no guion."""

    task_id: str
    poi_id: str
    to: str  # E.164, lo resuelve core desde POI.contact_phone
    audience: Audience
    intent: CallIntent
    urgency: Urgency
    facts: dict[str, str] = {}  # variables del guion: poi_name, route_name,
    #                             deadline_min, hazard_kind
    expect: list[str] = []  # qué queremos sacar: "confirmation",
    #                         "road_status", "headcount"


class CallFacts(BaseModel):
    """El esquema que consume fenic.semantic.extract.

    Cada descripción del Field es parte del prompt: escribidlas bien.
    """

    location_hint: str | None = Field(
        None, description="lugar mencionado, tal cual lo dice la persona"
    )
    resolved_poi_id: str | None = None  # lo rellena semantic.join después
    road_blocked: str | None = Field(None, description="tramo o carretera impracticable")
    people_immobile: int | None = Field(
        None, description="personas que no pueden moverse solas"
    )
    injuries: int | None = None
    confirmed_order: bool | None = Field(
        None, description="si acepta la instrucción dada"
    )
    contradicts_known: bool = False
    urgency: Urgency = "medium"
    confidence: float = Field(0.5, ge=0, le=1)


class CallResult(BaseModel):
    """Lo que voice publica en `call.ended`. `facts=None` si la extracción falló."""

    call_id: str
    task_id: str | None  # viene de metadata.custom
    direction: Literal["outbound", "inbound"]
    started_t: float
    ended_t: float
    outcome: CallOutcome
    transcript: str
    facts: CallFacts | None
    audio_url: str | None = None
    health_score: float | None = None  # Humalike `analyze`, 0..1, al colgar
    analysis: dict | None = None  # hallazgos de `analyze`, para el bonus de aprendizaje


class Fact(BaseModel):
    """Lo que entra al WorldState. Un CallFacts produce de 0 a N de estos.

    Las claves válidas viven en `contracts.factkeys`: P1 las escribe, P3 las usa.
    """

    key: str  # "road:wp_sur_03-wp_sur_04:cut"
    value: str | float | bool
    confidence: float = Field(ge=0, le=1)
    source: str  # "call:hl_8821"
    severity: Severity
    t_sim: float
    kind: FactKind = "observed"
    call_id: str | None = None
