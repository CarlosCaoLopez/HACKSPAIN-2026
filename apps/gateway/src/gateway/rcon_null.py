"""El RCON que no conecta. P4. **Plan B nivel 3.**

`--no-minecraft` no es *que falle el RCON y lo ignoramos*: es **no abrir el socket**.
La diferencia no es de estilo. `RconClient.connect` reintenta con backoff porque Paper
tarda en arrancar (`sim/rcon.py`), así que arrancar un run contra un puerto muerto son
varios segundos de espera justo en el momento en el que estoy diciendo la primera frase
del pitch. Con esto no hay espera porque no hay intento.

Lo que se conserva es la **superficie** del Protocol `sim.rcon.Rcon` —`connect`,
`close`, `send(cmd, priority, timeout)`, `send_many(cmds, priority, timeout)`—: `Sim`
recibe esto y no nota la diferencia, y `packages/sim/**` no se toca. Es el mismo truco
que `score_fallback` con la puntuación: sustituir una pieza de otro por una mía que se
comporta igual y **que dice que es ella**.

La firma importa tanto como los nombres: `sim/runner.py` llama `send(cmd, LOW)` y
`send_many(cmds, LOW)` con el carril en posicional, y un `send(self, command)` a secas
reventaba con `TypeError` en el primer `/fill` del worldgen — dentro de la task del
sim, o sea, sin que `--no-minecraft` avisara de nada. `tests/test_demo_args.py` corre un
`Sim` de verdad contra esto para que no vuelva a pasar.

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
    """La superficie de `sim.rcon.Rcon` sin socket. Traga, cuenta y devuelve `""`.

    `priority` y `timeout` se aceptan con la misma forma que el Protocol (`"high"` |
    `"low"`, segundos o `None`) y se ignoran: sin socket no hay cola ni espera. No se
    importa `sim.rcon` para tiparlos: este fichero tiene que importar aunque `sim` no.
    """

    def __init__(self) -> None:
        self.sent = 0
        self.by_priority: dict[str, int] = {}

    async def connect(self) -> None:
        log.info("rcon apagado (--no-minecraft): no se abre ninguna conexión")

    async def close(self) -> None:
        log.info(
            "rcon apagado · %s comandos tragados en todo el run · %s",
            self.sent,
            self.by_priority,
        )

    async def send(
        self, command: str, priority: str = "high", timeout: float | None = None
    ) -> str:
        self.sent += 1
        self.by_priority[priority] = self.by_priority.get(priority, 0) + 1
        if self.sent <= SAMPLE:
            log.info("rcon apagado · comando %s (%s): %s", self.sent, priority, command)
        return ""

    async def send_many(
        self, commands: list[str], priority: str = "high", timeout: float | None = None
    ) -> list[str]:
        return [await self.send(c, priority, timeout) for c in commands]
