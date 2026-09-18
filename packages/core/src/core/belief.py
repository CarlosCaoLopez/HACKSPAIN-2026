"""Eventos → WorldState. Puro y testeable sin nada montado.

`WorldState` es inmutable: `apply` devuelve uno nuevo, nadie muta en sitio. Es lo
que permite replayar `fixtures/run_golden.jsonl` entero y comprobar que ningún
evento revienta la validación.
"""

from contracts.calls import Fact
from contracts.events import Event
from contracts.scenario import Scenario
from contracts.world import WorldState


def apply(state: WorldState, ev: Event) -> WorldState:
    """El estado tras aplicar el evento. Un evento desconocido no lanza: devuelve
    el estado tal cual y se registra."""
    raise NotImplementedError


def apply_fact(state: WorldState, fact: Fact) -> WorldState:
    """Un hecho con procedencia entra al estado. Clave desconocida (no está en
    `contracts.factkeys`) se registra y no se aplica.

    Un `assert_fact` humano entra con `confidence=1.0` y gana a cualquier hecho de
    llamada sobre la misma clave."""
    raise NotImplementedError


def initial_state(run_id: str, scenario: Scenario) -> WorldState:
    """El estado del que parte todo, antes del primer tick."""
    raise NotImplementedError
