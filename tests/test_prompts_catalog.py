"""Todo peso y restricción que aparece en `core/prompts/` existe en el catálogo.

Parece una tontería y es el que os salva: si P1 añade un peso que el solver no
conoce, se ignora en silencio y nadie se entera hasta la demo.

Convención: en los prompts, los pesos y restricciones se escriben `entre_backticks`.
"""

import re
from pathlib import Path

from contracts.plan import CONSTRAINTS, WEIGHTS, is_known_constraint

PROMPTS = Path("packages/core/src/core/prompts")

# `nombre` o `nombre:arg` o `nombre:arg:arg`
TOKEN = re.compile(r"`([a-z_]+(?::[a-z_0-9]+)*)`")

# Identificadores del código que también van entre backticks y no son catálogo.
IGNORED = {"doFireTick", "randomTickSpeed"}


def _tokens() -> set[str]:
    text = "\n".join(p.read_text() for p in sorted(PROMPTS.glob("*.md")))
    return {t for t in TOKEN.findall(text) if t not in IGNORED}


def test_prompts_dir_exists() -> None:
    assert PROMPTS.is_dir(), f"{PROMPTS} no existe"


def test_every_weight_in_catalog() -> None:
    """Un token de una sola palabra o es un peso conocido, o no es un peso."""
    unknown = sorted(
        t for t in _tokens() if ":" not in t and t not in WEIGHTS and t not in CONSTRAINTS
    )
    suspicious = [t for t in unknown if "_" in t]
    assert not suspicious, (
        f"tokens con pinta de peso que no están en el catálogo: {suspicious}. "
        "Añadidlos a contracts.plan.WEIGHTS o quitadlos del prompt."
    )


def test_every_constraint_in_catalog() -> None:
    """Un token con `:` es una restricción con argumento: nombre y aridad válidos."""
    bad = sorted(t for t in _tokens() if ":" in t and not is_known_constraint(t))
    assert not bad, (
        f"restricciones desconocidas o con aridad mala: {bad}. "
        f"El catálogo tiene {sorted(CONSTRAINTS)}."
    )
