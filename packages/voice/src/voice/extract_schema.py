# ruff: noqa: UP045
"""Espejo de `CallFacts` para `fenic.semantic.extract`.

fenic rechaza `str | None` (PEP 604) en el esquema y solo acepta `Optional[T]`, así
que este fichero NO usa `from __future__ import annotations` y ruff no toca los
`Optional`. Las descripciones son las mismas que en `contracts.calls.CallFacts`:
son parte del prompt. `to_call_facts` lo convierte al modelo del contrato.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from contracts.calls import CallFacts


class CallFactsExtract(BaseModel):
    location_hint: Optional[str] = Field(
        None, description="lugar mencionado, tal cual lo dice la persona"
    )
    road_blocked: Optional[str] = Field(
        None, description="tramo o carretera impracticable"
    )
    people_immobile: Optional[int] = Field(
        None, description="personas que no pueden moverse solas"
    )
    injuries: Optional[int] = Field(None, description="número de heridos")
    confirmed_order: Optional[bool] = Field(
        None, description="si acepta la instrucción dada"
    )
    contradicts_known: bool = Field(False, description="si contradice algo dicho antes")
    urgency: Literal["low", "medium", "critical"] = Field(
        "medium", description="urgencia de lo que cuenta"
    )
    confidence: float = Field(0.5, description="confianza de 0 a 1 en lo extraído")


def to_call_facts(raw: object) -> CallFacts:
    data = dict(raw) if isinstance(raw, dict) else dict(vars(raw))
    data["confidence"] = min(1.0, max(0.0, float(data.get("confidence") or 0.5)))
    return CallFacts.model_validate(data)
