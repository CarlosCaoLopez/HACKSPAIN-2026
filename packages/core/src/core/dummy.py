"""El core tonto. Lo escribe P1 el viernes en 20 minutos.

Al recibir `world.fire.detected` manda el camión más cercano y nada más. Sin LLM,
sin solver, sin verificadores. P2 y P3 lo usan todo el sábado para no esperar a
nadie: `make dev-sim` lo levanta.
"""


class DummyCore:
    """Misma superficie que `Core` en lo que a otros les importa: consume el bus y
    publica `action.requested`."""

    def __init__(self, bus) -> None:
        raise NotImplementedError

    async def run(self) -> None:
        """Bucle: `world.fire.detected` → `goto` del camión más cercano."""
        raise NotImplementedError
