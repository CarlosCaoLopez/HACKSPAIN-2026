"""El tick loop y la clase `Sim`. P2.

Cada tick avanza 1 segundo simulado: propaga fuego, interpola posiciones, dispara
injects vencidos, y publica `world.*`.
"""

from pathlib import Path

from sim.rcon import RconClient

TICK_S = 1.0
"""Un segundo simulado por tick. La interpolación va a 5 Hz por dentro."""


class Sim:
    def __init__(self, scenario_path: Path, rcon: RconClient) -> None:
        raise NotImplementedError

    async def start(self) -> None:
        """Worldgen + tick loop. Publica `world.*` hasta que alguien pare."""
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError

    async def execute(self, action_id: str, verb: str, args: dict) -> None:
        """Ejecuta una acción del core y confirma con `action.completed`.

        `verb` acepta exactamente `goto`, `set_marker`, `announce`, `rescue`.
        Cualquier otro emite `action.failed` con `error="unknown_verb"`.
        """
        raise NotImplementedError

    async def inject(self, inject_type: str, payload: dict) -> None:
        """Dispara un inject, venga del YAML o de `POST /control/inject`."""
        raise NotImplementedError

    def snapshot(self) -> dict:
        """Solo para depurar. El estado de verdad lo construye el core."""
        raise NotImplementedError
