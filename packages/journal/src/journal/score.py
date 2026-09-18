"""Métricas de un run. El journal es log, base de datos y dataset a la vez.

Es lo que hace comparable el run 1 contra el run 12 en pantalla partida.
"""

from pathlib import Path

from pydantic import BaseModel


class RunScore(BaseModel):
    run_id: str
    scenario_id: str
    civilians_safe: int = 0
    civilians_exposed_end: int = 0
    cells_burnt: int = 0
    replans: int = 0
    llm_calls: int = 0
    calls_placed: int = 0
    mean_hangup_to_turn_s: float | None = None  # colgar → giro, el número del pitch
    total: float = 0.0


def score(path: Path) -> RunScore:
    """Puntúa un journal cerrado. Puro: mismo fichero, misma puntuación."""
    raise NotImplementedError


def compare(a: Path, b: Path) -> dict:
    """Run 1 vs run 12. Lo que pinta el panel de aprendizaje."""
    raise NotImplementedError
