"""Leer `scenarios/*.yaml` mientras el loader de P2 no tenga cuerpo. P4.

El modelo es `contracts.scenario.Scenario` y el parseo de verdad es
`sim.scenario.load`. Esto solo decide cuál de los dos usar, y existe porque el
gateway necesita un `Scenario` para `Core.__init__(bus, scenario)` y para
`core.belief.initial_state`, los dos antes de que P2 llegue.
"""

from __future__ import annotations

import logging
from pathlib import Path

from contracts.scenario import Scenario

log = logging.getLogger("vela.gateway")

SCENARIOS_DIR = Path("scenarios")


def scenario_path(scenario_id: str) -> Path:
    return SCENARIOS_DIR / f"{scenario_id}.yaml"


def load_scenario(scenario_id: str) -> Scenario:
    """`sim.scenario.load` cuando exista; mientras no, el YAML por `contracts`."""
    path = scenario_path(scenario_id)
    try:
        from sim.scenario import load

        return load(path)
    except (ImportError, NotImplementedError):
        import yaml

        return Scenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def list_ids() -> list[str]:
    """`sim.scenario.list_scenarios` cuando exista; mientras no, los nombres de
    fichero, que hoy coinciden con el `id` de dentro."""
    try:
        from sim.scenario import list_scenarios

        return list_scenarios(SCENARIOS_DIR)
    except (ImportError, NotImplementedError):
        return sorted(p.stem for p in SCENARIOS_DIR.glob("*.yaml"))
