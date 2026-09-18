"""El reparto a los clientes del WS. P4.

Separado de `ws.py` a propósito: `ws.py` es el router de FastAPI y esto es la
mecánica del fan-out, que es lo único con trampa y lo único que se puede probar sin
levantar un servidor.

Dos reglas y de ellas sale todo lo demás:

1. **El bus nunca espera a un cliente.** `dispatch` es síncrono y usa `put_nowait`:
   un dashboard lento no puede frenar el tick del sim.
2. **Nunca se descarta un evento en silencio.** Si la cola de un cliente se llena,
   se le cierra el socket y que rehaga `GET /api/state`. Un hueco silencioso es un
   mapa que miente, y eso no se depura en directo delante de un jurado.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable

from contracts.events import Event

log = logging.getLogger("vela.hub")

QUEUE_MAX = 1000
"""Unos 15 minutos de eventos al ritmo del golden. Si un cliente se atrasa más que
eso, no está lento: está roto."""

CLOSE_BACKPRESSURE = 4002
CLOSE_RUN_CHANGED = 4003


class Client:
    """Una cola propia por cliente y el motivo por el que hay que cerrarlo."""

    def __init__(self, maxsize: int = QUEUE_MAX) -> None:
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.close_code: int | None = None
        self.close_reason: str = ""
        self.sent: int = 0
        # Se despierta al marcar el cierre: si no, un cliente atascado se queda
        # bloqueado en `queue.get()` y no se entera de que hay que echarlo.
        self.wake: asyncio.Event = asyncio.Event()

    def mark_close(self, code: int, reason: str) -> None:
        if self.close_code is None:
            self.close_code, self.close_reason = code, reason
        self.wake.set()

    @property
    def closing(self) -> bool:
        return self.close_code is not None


class Hub:
    """Una sola suscripción al bus y N colas de cliente."""

    def __init__(self, maxsize: int = QUEUE_MAX) -> None:
        self.clients: set[Client] = set()
        self.last_seq: int = 0
        # El reloj del dominio visto por el proceso. Lo necesita quien publica desde el
        # gateway (un override, una llamada simulada): sin esto sus eventos salen con
        # `t_sim=0` y los paneles, que ordenan por `t_sim`, los mandan al principio del
        # run — es decir, al sitio donde no pasó.
        self.last_t_sim: float = 0.0
        self.run_id: str | None = None
        self._maxsize = maxsize
        self._dropped_clients = 0

    # --- clientes ---------------------------------------------------------------

    def register(self) -> Client:
        """Se llama ANTES de capturar el snapshot. El orden es el requisito: si se
        registra después, los eventos de en medio se pierden y nadie se entera."""
        c = Client(self._maxsize)
        self.clients.add(c)
        return c

    def unregister(self, c: Client) -> None:
        self.clients.discard(c)

    # --- reparto ----------------------------------------------------------------

    def dispatch(self, ev: Event) -> None:
        """Reparte a todos. Síncrono y sin `await`: no puede frenar al bus."""
        self.last_seq = max(self.last_seq, ev.seq)
        self.last_t_sim = max(self.last_t_sim, ev.t_sim)
        self.run_id = ev.run_id
        for c in self.clients:
            if c.closing:
                continue
            try:
                c.queue.put_nowait(ev)
            except asyncio.QueueFull:
                # No se tira el evento: se echa al cliente y que rehaga el snapshot.
                self._dropped_clients += 1
                c.mark_close(CLOSE_BACKPRESSURE, "backpressure")
                log.warning("cliente cerrado por backpressure en seq=%s", ev.seq)
            else:
                c.wake.set()

    async def run_from_bus(self, subscribe: Callable[[], AsyncIterator[Event]]) -> None:
        """La única suscripción al bus de todo el proceso.

        `subscribe` se inyecta (normalmente `contracts.bus.subscribe`) para poder
        probar el hub sin bus y para que el gateway no se case con su firma.
        """
        async for ev in subscribe():
            self.dispatch(ev)

    # --- diagnóstico ------------------------------------------------------------

    def stats(self) -> dict:
        return {
            "clients": len(self.clients),
            "last_seq": self.last_seq,
            "max_queue": max((c.queue.qsize() for c in self.clients), default=0),
            "dropped_clients": self._dropped_clients,
        }
