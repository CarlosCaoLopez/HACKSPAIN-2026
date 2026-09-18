"""Pydantic → TypeScript. `apps/dashboard/src/types.ts` se genera, nunca se escribe.

`make types` antes de cada push del dashboard.
"""

from pathlib import Path

OUT = Path("apps/dashboard/src/types.ts")

HEADER = "// GENERADO por scripts/gen_ts_types.py desde contracts. No editar a mano.\n"


def collect_models() -> list[type]:
    """Todos los BaseModel públicos de `contracts`, en orden de dependencia."""
    raise NotImplementedError


def emit(models: list[type]) -> str:
    """Interfaces TypeScript. Los `Literal` de Pydantic salen como uniones de
    strings; `dict[str, X]` como `Record<string, X>`."""
    raise NotImplementedError


def main() -> None:
    raise NotImplementedError


if __name__ == "__main__":
    main()
