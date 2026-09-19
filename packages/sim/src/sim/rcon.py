"""Cliente RCON: cola, reintentos y nada más.

El servidor Paper es un dispositivo de salida, como una pantalla. Solo `/tp`,
`/fill` y `/setblock`. Sin bots, sin pathfinding, sin cuentas de Minecraft.

Dos decisiones viven aquí:

- **D1**: `Rcon` es un Protocol y `FakeRcon` lo cumple. El sim corre entero sin
  Minecraft: tests sin servidor, y plan B nivel 3 si Paper se cae en escenario.
- **D7**: la cola tiene dos carriles. `/tp` y las acciones del core adelantan al
  render del hazard. El fuego puede avanzar un tick tarde; el giro de los
  camiones en el clímax, no.
"""

import asyncio
import struct
from typing import Literal, Protocol

Priority = Literal["high", "low"]

HIGH: Priority = "high"
"""Movimiento y acciones del core. Va en la ruta crítica de latencia."""
LOW: Priority = "low"
"""Render del hazard: `/fill`, `/setblock`. Diferible sin que se note."""

_AUTH = 3
_AUTH_RESPONSE = 2
_COMMAND = 2
_RESPONSE = 0

CONNECT_RETRIES = 30
"""Paper tarda ~35 s en el primer arranque. Se reintenta con backoff."""
TIMEOUT_S: dict[str, float] = {"high": 5.0, "low": 60.0}
"""Timeout por carril. `high` va en la ruta crítica: si un `/tp` no vuelve en 5 s,
la demo ya se ha roto y conviene saberlo. `low` incluye worldgen y `/fill` de
áreas grandes, que generan terreno y tardan de verdad: un `forceload` de 171
chunks se pasa de 5 s sin que nada vaya mal."""


class RconError(RuntimeError):
    pass


class Rcon(Protocol):
    """La interfaz que ve el resto de `sim`. Nadie depende de la implementación."""

    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def send(
        self, command: str, priority: Priority = HIGH, timeout: float | None = None
    ) -> str: ...
    async def send_many(
        self,
        commands: list[str],
        priority: Priority = HIGH,
        timeout: float | None = None,
    ) -> list[str]: ...


class RconClient:
    """Serializa los comandos en una cola: RCON no es concurrente.

    Un único worker es dueño del socket y drena primero el carril `high`. Los
    comandos se encolan desde cualquier corrutina y esperan su propio future.
    """

    def __init__(self, host: str, port: int, password: str) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._queues: dict[Priority, asyncio.Queue] = {}
        self._worker: asyncio.Task | None = None
        self._req_id = 0

    async def connect(self) -> None:
        """Abre la conexión. Reintenta con backoff: Paper tarda en arrancar.

        Idempotente: quien ya está conectado vuelve enseguida. `Sim.start` lo llama
        siempre, y quien construye el cliente puede haberlo conectado ya; abrir un
        segundo socket dejaría un worker huérfano quedándose respuestas.
        """
        if self._worker is not None and not self._worker.done():
            return
        delay = 0.5
        for attempt in range(1, CONNECT_RETRIES + 1):
            try:
                self._reader, self._writer = await asyncio.open_connection(
                    self._host, self._port
                )
                break
            except OSError:
                if attempt == CONNECT_RETRIES:
                    raise RconError(
                        f"no hay RCON en {self._host}:{self._port} tras "
                        f"{CONNECT_RETRIES} intentos. ¿`make server` levantado?"
                    ) from None
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 5.0)

        await self._write(_AUTH, self._password)
        req_id, _ = await self._read()
        if req_id == -1:
            raise RconError(
                "RCON rechaza la contraseña: `rcon.password` de "
                "infra/server/server.properties no coincide con RCON_PASSWORD del .env"
            )

        self._queues = {HIGH: asyncio.Queue(), LOW: asyncio.Queue()}
        self._worker = asyncio.create_task(self._run(), name="rcon-worker")

    async def close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
        self._reader = None

    async def send(
        self, command: str, priority: Priority = HIGH, timeout: float | None = None
    ) -> str:
        """Encola un comando y devuelve la respuesta cruda del servidor."""
        if self._worker is None:
            raise RconError("RconClient sin conectar: falta `await connect()`")
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        limit = TIMEOUT_S[priority] if timeout is None else timeout
        self._queues[priority].put_nowait((command, fut, limit))
        return await fut

    async def send_many(
        self,
        commands: list[str],
        priority: Priority = HIGH,
        timeout: float | None = None,
    ) -> list[str]:
        """Lote en orden. Para worldgen, que son 200 comandos seguidos."""
        return [await self.send(c, priority, timeout) for c in commands]

    # --- el worker: único dueño del socket ---

    async def _run(self) -> None:
        while True:
            command, fut, limit = await self._next()
            try:
                await self._write(_COMMAND, command)
                _, body = await asyncio.wait_for(self._read(), limit)
                if not fut.done():
                    fut.set_result(body)
            except (asyncio.CancelledError, GeneratorExit):
                if not fut.done():
                    fut.cancel()
                raise
            except Exception as exc:  # noqa: BLE001 — deliberado: un comando malo
                # no tumba el sim. El fallo viaja al future de quien lo pidió.
                if not fut.done():
                    # `TimeoutError` tiene str() vacío: sin el nombre del tipo,
                    # el fallo más probable es también el más opaco.
                    fut.set_exception(
                        RconError(f"{command!r}: {type(exc).__name__}: {exc}")
                    )

    async def _next(self) -> tuple[str, asyncio.Future, float]:
        """`high` primero, siempre. Si está vacío, lo que haya en `low`."""
        high, low = self._queues[HIGH], self._queues[LOW]
        if not high.empty():
            return high.get_nowait()
        if not low.empty():
            return low.get_nowait()
        get_high = asyncio.ensure_future(high.get())
        get_low = asyncio.ensure_future(low.get())
        done, pending = await asyncio.wait(
            {get_high, get_low}, return_when=asyncio.FIRST_COMPLETED
        )
        # Si despiertan los dos a la vez, gana high y el de low se devuelve a su cola.
        winner = get_high if get_high in done else get_low
        for task in pending:
            task.cancel()
        for task in done:
            if task is not winner:
                low.put_nowait(task.result())
        return winner.result()

    # --- el protocolo, que son doce líneas ---

    async def _write(self, kind: int, body: str) -> None:
        assert self._writer is not None
        self._req_id += 1
        payload = struct.pack("<ii", self._req_id, kind) + body.encode() + b"\x00\x00"
        self._writer.write(struct.pack("<i", len(payload)) + payload)
        await self._writer.drain()

    async def _read(self) -> tuple[int, str]:
        assert self._reader is not None
        size = struct.unpack("<i", await self._reader.readexactly(4))[0]
        data = await self._reader.readexactly(size)
        req_id, _kind = struct.unpack("<ii", data[:8])
        return req_id, data[8:-2].decode("utf-8", errors="replace")


class FakeRcon:
    """D1. Cumple `Rcon` y solo apunta los comandos en una lista.

    Es lo que hace testeables H4, H5 y H6 sin Docker y sin servidor, y lo que
    sostiene el plan B nivel 3: si Paper se cae, el sim sigue publicando
    `world.*` y el dashboard sigue contando la historia.
    """

    def __init__(self) -> None:
        self.commands: list[tuple[Priority, str]] = []
        self.connected = False
        """Lo mira el test que comprueba que `Sim.start` conecta. El doble no
        se queja de que lo usen sin conectar —demasiados tests llaman a `tick`
        sin pasar por `start`— así que el descuido solo se ve si se asierta."""

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def send(
        self, command: str, priority: Priority = HIGH, timeout: float | None = None
    ) -> str:
        self.commands.append((priority, command))
        return ""

    async def send_many(
        self,
        commands: list[str],
        priority: Priority = HIGH,
        timeout: float | None = None,
    ) -> list[str]:
        return [await self.send(c, priority, timeout) for c in commands]

    def sent(self, prefix: str = "") -> list[str]:
        """Los comandos, para aserciones: `assert fake.sent("tp") == [...]`."""
        return [c for _, c in self.commands if c.startswith(prefix)]


class PrintRcon:
    """Cumple `Rcon` y escribe los comandos por stdout en vez de enviarlos.

    Es el `--dry-run` de `make world` y el `--no-minecraft` de `make dev-sim`:
    ver qué mandaría el sim sin abrir un socket ni esperar los reintentos de
    `connect`. `limit` acota lo que se imprime: los primeros comandos dicen si el
    worldgen arranca; los cinco `/tp` por segundo de cada camión son ruido. Con
    `None` se imprime todo.
    """

    def __init__(self, limit: int | None = None) -> None:
        self.limit = limit
        self.sent = 0

    async def connect(self) -> None:
        print("rcon: modo impresión, no se abre ninguna conexión")

    async def close(self) -> None:
        if self.limit is not None and self.sent > self.limit:
            print(
                f"rcon: {self.sent} comandos en total ({self.sent - self.limit} sin imprimir)"
            )

    async def send(
        self, command: str, priority: Priority = HIGH, timeout: float | None = None
    ) -> str:
        self.sent += 1
        if self.limit is None or self.sent <= self.limit:
            print(f"[{priority}] {command}")
        return ""

    async def send_many(
        self,
        commands: list[str],
        priority: Priority = HIGH,
        timeout: float | None = None,
    ) -> list[str]:
        return [await self.send(c, priority, timeout) for c in commands]
