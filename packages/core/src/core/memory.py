"""El aprendizaje entre ejecuciones. El bonus.

Al terminar un run, un job batch carga todos los journals anteriores en `fenic` y
extrae, con `semantic.extract` sobre `LearnedRule`, patrones del tipo "cuando el
viento gira más de 60°, evacuar antes de reasignar extinción".

Las reglas con soporte en al menos dos runs se escriben en `memory/policy_rules.md`,
que se inyecta en el prompt del planner del run siguiente.

Run 1 contra run 12 en pantalla partida con la puntuación de cada uno.
"""

from pathlib import Path

from pydantic import BaseModel, Field

MIN_SUPPORT = 2
"""Una regla vista en un solo run es ruido."""

RULES_PATH = Path("memory/policy_rules.md")


class LearnedRule(BaseModel):
    """El esquema que consume `semantic.extract` sobre los journals pasados."""

    condition: str = Field(description="cuándo aplica, en una frase")
    action: str = Field(description="qué hacer antes o en vez de qué")
    support: int = Field(0, description="en cuántos runs se ha visto")
    evidence_runs: list[str] = []


def mine(journal_paths: list[Path]) -> list[LearnedRule]:
    """Batch sobre todos los journals. Lento a propósito: corre entre runs."""
    raise NotImplementedError


def write_rules(rules: list[LearnedRule], path: Path = RULES_PATH) -> None:
    """Solo las que tienen `support >= MIN_SUPPORT`."""
    raise NotImplementedError


def load_rules(path: Path = RULES_PATH) -> str:
    """El markdown tal cual, para inyectarlo en el prompt del planner. Fichero
    ausente devuelve cadena vacía: el run 1 no tiene memoria y no pasa nada."""
    raise NotImplementedError
