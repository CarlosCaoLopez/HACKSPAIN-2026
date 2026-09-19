"""El catálogo de preguntas de percepción. GENERADO desde el escenario, no escrito a mano.

`CallFacts` deja de ser un esquema de extracción y pasa a ser un catálogo de
preguntas atómicas sobre un conjunto cerrado: las opciones de cada `Choice` son los
ids del YAML, así que el modelo no puede nombrar una carretera que no existe.
Si alguien escribe a mano la lista de POIs en un `criteria`, el segundo escenario
se rompe.

Aquí solo hay la *especificación* (`QuestionSpec`), sin dependencia del SDK de
TypeSafe: `voice/jev.py` la traduce a `Choice` / `Score` / `Noul`. Así `contracts`
sigue sin importar nada de terceros y el plan B (`--no-jev`, fenic con `Literal`)
consume el mismo catálogo.

Instrucciones y criterios van **en inglés** y la transcripción en español: la
lengua principal de Jev es el inglés y las demás tienen menor precisión. Los POIs
son nombres propios y la descripción incluye el nombre local.
"""

from dataclasses import dataclass
from typing import Literal

from contracts.scenario import Scenario
from contracts.world import POI, RoadEdge

NOT_STATED = "not_stated"
URGENCY_LEVELS: tuple[str, ...] = ("low", "medium", "critical")
"""El orden importa: es el de los `criteria` del `Score`, y el índice redondeado del
score es la posición en esta tupla."""
IMMOBILE_OPTIONS: tuple[str, ...] = ("0", "1", "2", "3", "4", "5plus")
FIELDS: tuple[str, ...] = (
    "location_hint",
    "road_blocked",
    "people_immobile",
    "urgency",
    "contradicts_known",
    "confirmed_order",
)
"""El catálogo entero. El presupuesto de completitud persigue solo los cuatro primeros;
`contradicts_known` y `confirmed_order` son señales (Noul), no huecos que preguntar."""

_CALLER_ONLY = (
    " Consider ONLY what the CALLER (the neighbour) states. Ignore the operator's "
    "questions: if the operator mentions a place or a road, that is not a fact."
)


@dataclass(frozen=True)
class QuestionSpec:
    kind: Literal["choice", "score", "noul"]
    instructions: str
    criteria: dict[str, str] | list[str] | None = None

    @property
    def options(self) -> list[str]:
        """Las salidas posibles: las claves de un choice, los niveles de un score."""
        if isinstance(self.criteria, dict):
            return list(self.criteria)
        if self.kind == "score":
            return list(URGENCY_LEVELS)
        return []


def call_questions(scn: Scenario) -> dict[str, QuestionSpec]:
    """El catálogo de una llamada, sacado del escenario."""
    return call_questions_for(scn.pois, scn.roads)


def call_questions_for(pois: list[POI], roads: list[RoadEdge]) -> dict[str, QuestionSpec]:
    """Igual, desde las listas: `voice` no tiene un `Scenario` entero, solo los POIs y
    aristas que le da el gateway (`voice.pois.set_scenario`). Una descripción que falte
    en el YAML se deriva del nombre o de los extremos de la arista, nunca se inventa."""
    pois_d = {p.id: p.description or p.name for p in pois}
    roads_d = {r.id: r.description or f"road between {r.a} and {r.b}" for r in roads}
    return {
        "location_hint": QuestionSpec(
            "choice",
            "Which place does the caller say they are speaking from or about?"
            + _CALLER_ONLY,
            {**pois_d, NOT_STATED: "The caller does not say"},
        ),
        "road_blocked": QuestionSpec(
            "choice",
            "Which stretch of road does the caller say is cut or impassable?"
            + _CALLER_ONLY,
            {**roads_d, NOT_STATED: "The caller does not say"},
        ),
        # Choice, no int: Jev no cuenta de forma fiable. Se enumeran los valores
        # pequeños y el resto va a `5plus`.
        "people_immobile": QuestionSpec(
            "choice",
            "How many people who cannot move on their own does the caller mention?"
            + _CALLER_ONLY,
            {
                "0": "None",
                "1": "One",
                "2": "Two",
                "3": "Three",
                "4": "Four",
                "5plus": "Five or more",
                NOT_STATED: "The caller does not say",
            },
        ),
        "urgency": QuestionSpec(
            "score",
            "How severe is what the caller describes?" + _CALLER_ONLY,
            [
                "Low: no one is in danger, only information",
                "Medium: a road or property is affected, nobody is hurt or trapped",
                "Critical: lives at immediate risk, people trapped or unable to move",
            ],
        ),
        "contradicts_known": QuestionSpec(
            "noul",
            "The caller states something that contradicts the known facts of the "
            "system given in the state.",
        ),
        "confirmed_order": QuestionSpec(
            "noul",
            "The caller explicitly accepts the instruction the operator gave them "
            "(for example, agrees to evacuate).",
        ),
    }
