"""El RCON que no conecta. P4. **Plan B nivel 3.**

`--no-minecraft` no es *que falle el RCON y lo ignoramos*: es **no abrir el socket**.
La diferencia no es de estilo. `RconClient.connect` reintenta con backoff porque Paper
tarda en arrancar (`sim/rcon.py`), así que arrancar un run contra un puerto muerto son
varios segundos de espera justo en el momento en el que estoy diciendo la primera frase
del pitch. Con esto no hay espera porque no hay intento.

Lo que se conserva es la **superficie** de `RconClient` —`connect`, `close`, `send`,
`send_many`—: `Sim` recibe esto y no nota la diferencia, y `packages/sim/**` no se toca.
Es el mismo truco que `scenario_fallback` con la geometría: sustituir una pieza de otro
por una mía que se comporta igual y **que dice que es ella**.

Los comandos se cuentan y no se tiran en silencio: `GET /api/health` enseña cuántos
llevaría enviados el sim, que es la única forma de saber desde fuera si el sim está vivo
cuando el mundo no se ve.
"""

from __future__ import annotations

import logging

log = logging.getLogger("vela.rcon")

SAMPLE = 3
"""Cuántos comandos se registran enteros antes de callar. Los primeros dicen si el
worldgen de P2 arranca; los 200 siguientes son ruido en la consola de la demo."""


class NullRcon:
    """La superficie de `sim.RconClient` sin socket. Traga, cuenta y devuelve `""`."""

    def __init__(self) -> None:
        self.sent = 0

    async def connect(self) -> None:
        log.info("rcon apagado (--no-minecraft): no se abre ninguna conexión")

    async def close(self) -> None:
        log.info("rcon apagado · %s comandos tragados en todo el run", self.sent)

    async def send(self, command: str) -> str:
        self.sent += 1
        if self.sent <= SAMPLE:
            log.info("rcon apagado · comando %s: %s", self.sent, command)
        return ""

    async def send_many(self, commands: list[str]) -> list[str]:
        return [await self.send(c) for c in commands]
