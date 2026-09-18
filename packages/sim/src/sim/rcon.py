"""Cliente RCON: cola, reintentos y nada más.

El servidor Paper es un dispositivo de salida, como una pantalla. Solo `/tp`,
`/fill` y `/setblock`. Sin bots, sin pathfinding, sin cuentas de Minecraft.
"""


class RconClient:
    """Serializa los comandos en una cola: RCON no es concurrente."""

    def __init__(self, host: str, port: int, password: str) -> None:
        raise NotImplementedError

    async def connect(self) -> None:
        """Abre la conexión. Reintenta con backoff: Paper tarda en arrancar."""
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError

    async def send(self, command: str) -> str:
        """Encola un comando y devuelve la respuesta cruda del servidor."""
        raise NotImplementedError

    async def send_many(self, commands: list[str]) -> list[str]:
        """Lote en orden. Para worldgen, que son 200 comandos seguidos."""
        raise NotImplementedError
