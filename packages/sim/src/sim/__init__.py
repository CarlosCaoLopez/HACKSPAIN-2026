"""`sim` · P2 · mundo + injects. Habla con Paper por RCON y con los demás por el bus."""

from typing import Any

from sim.rcon import HIGH, LOW, FakeRcon, PrintRcon, Rcon, RconClient, RconError

__all__ = [
    "HIGH",
    "LOW",
    "FakeRcon",
    "PrintRcon",
    "Rcon",
    "RconClient",
    "RconError",
    "Sim",
]


def __getattr__(name: str) -> Any:
    """`Sim` se importa perezoso: `runner` arrastra `worldgen`, y si el paquete los
    importara al cargarse, `python -m sim.worldgen` (`make world`) y `python -m
    sim.runner` (`make dev-sim`) ejecutarían el módulo dos veces con un
    RuntimeWarning. `from sim import Sim` sigue funcionando igual."""
    if name == "Sim":
        from sim.runner import Sim

        return Sim
    raise AttributeError(f"module 'sim' has no attribute {name!r}")
