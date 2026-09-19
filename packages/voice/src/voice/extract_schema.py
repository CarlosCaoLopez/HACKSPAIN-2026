# ruff: noqa: UP045
"""Plan B (`--no-jev`): `CallFacts` para `fenic.semantic.extract`, con `Literal` sobre
las opciones del escenario. Se pierde la confianza calibrada de Jev y el bucle durante
la llamada; se conserva la garantía de espacio cerrado (`build_extract_model`).

fenic rechaza `str | None` (PEP 604) en el esquema y solo acepta `Optional[T]`, así
que este fichero NO usa `from __future__ import annotations` y ruff no toca los
`Optional`. Las descripciones son las mismas que en `contracts.calls.CallFacts`:
son parte del prompt. `to_call_facts` lo convierte al modelo del contrato.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, create_model

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
    headcount: Optional[int] = Field(
        None, description="cuántas personas hay en total en el lugar, si lo dice"
    )
    confirmed_order: Optional[bool] = Field(
        None, description="si acepta la instrucción dada"
    )
    contradicts_known: bool = Field(False, description="si contradice algo dicho antes")
    urgency: Literal["low", "medium", "critical"] = Field(
        "medium", description="urgencia de lo que cuenta"
    )
    confidence: float = Field(0.5, description="confianza de 0 a 1 en lo extraído")


def build_extract_model(poi_ids: list[str], road_ids: list[str]) -> type[BaseModel]:
    """`CallFactsExtract` con `location_hint` y `road_blocked` como `Literal` sobre los
    ids del escenario: el modelo no puede nombrar una carretera que no existe. Sin
    escenario cargado, el esquema de texto libre de siempre."""
    if not poi_ids or not road_ids:
        return CallFactsExtract
    return create_model(
        "CallFactsExtractClosed",
        __base__=CallFactsExtract,
        location_hint=(
            Optional[Literal[tuple(poi_ids)]],  # type: ignore[valid-type]  # Literal dinámico: los ids salen del YAML en runtime
            Field(
                None,
                description="the place the caller speaks from, one of the allowed ids",
            ),
        ),
        road_blocked=(
            Optional[Literal[tuple(road_ids)]],  # type: ignore[valid-type]  # idem
            Field(
                None,
                description="the road stretch the caller says is cut, one of the allowed ids",
            ),
        ),
    )


def to_call_facts(raw: object, poi_names: dict[str, str] | None = None) -> CallFacts:
    data = dict(raw) if isinstance(raw, dict) else dict(vars(raw))
    names = poi_names or {}
    if data.get("location_hint") in names:  # el Literal ya es el id del POI
        data["resolved_poi_id"] = data["location_hint"]
        data["location_hint"] = names[data["location_hint"]]
    data["confidence"] = min(1.0, max(0.0, float(data.get("confidence") or 0.5)))
    return CallFacts.model_validate(data)
