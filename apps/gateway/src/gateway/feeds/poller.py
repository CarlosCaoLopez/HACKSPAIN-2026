"""El bucle que trae las fuentes y las publica como hechos. SPEC-007 · REQ-233…237, 246, 256…261.

Dos bucles que no se conocen entre sí:

- **uno por fuente** (`_run_source`): pide, guarda la captura, traduce con el `to_facts` puro
  y mete las observaciones en una cola ordenada por `t_sim` (`clock.Schedule`);
- **uno de emisión** (`_emit_loop`): saca de la cola lo que ya toca según el `t_sim` del run
  y lo publica por `Runtime.publish`, que escribe al journal antes de repartir (invariante 3).

Separarlos es lo que permite reproducir un día real a velocidad de demo: traer y publicar
ocurren en momentos distintos.

**Degradación explícita, no un `try` que se calla.** Una fuente que falla pasa a `degraded`
con el error a la vista en `/api/feeds`, reintenta con backoff y no toca al run ni a las
demás. Un servicio caído se degrada y se anota; nunca un `except: pass`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

import httpx

from contracts.events import EventType
from contracts.scenario import Scenario
from gateway.feeds import FeedContext, Observation, capture, dgt, firms, open_meteo
from gateway.feeds.anchor import GeoAnchor, anchor_view, edges_on_route
from gateway.feeds.clock import Schedule

if TYPE_CHECKING:
    from gateway.runtime import Runtime

log = logging.getLogger("vela.feeds")

NAMES = ("open_meteo", "dgt", "firms")
# El nombre que lleva `source` (`api:open-meteo:…`) no es el de la fuente en Python.
SOURCE_TAG = {"open-meteo": "open_meteo", "dgt": "dgt", "firms": "firms"}

INTERVAL_S = {"open_meteo": 900.0, "dgt": 120.0, "firms": 600.0}
BACKOFF_FIRST_S = 30.0
BACKOFF_MAX_S = 600.0
EMIT_EVERY_S = 0.5
HTTP_TIMEOUT_S = 10.0
USER_AGENT = "vela-hackspain/0.1 (+https://github.com/CarlosCaoLopez/HACKSPAIN-2026)"

Status = Literal["off", "ok", "degraded"]


class Handled(NamedTuple):
    observations: list[Observation]
    malformed: int


@dataclass
class FeedState:
    """Lo que `/api/feeds` cuenta de una fuente (REQ-260)."""

    status: Status = "off"
    note: str = ""
    last_ok_t_wall: str | None = None
    last_error: str | None = None
    published: int = 0
    malformed: int = 0
    next_poll_at: float | None = None  # `time.monotonic()`; se enseña como segundos restantes
    backoff_s: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        remaining = (
            None if self.next_poll_at is None else max(0, round(self.next_poll_at - time.monotonic()))
        )
        return {
            "status": self.status,
            "note": self.note,
            "last_ok_t_wall": self.last_ok_t_wall,
            "last_error": self.last_error,
            "published": self.published,
            "malformed": self.malformed,
            "next_poll_s": remaining,
        }


@dataclass(frozen=True)
class SourceSpec:
    name: str
    interval_s: float | None  # `None` = una vez por run (el modo fechado trae un día entero)
    fetch: Callable[[], Awaitable[list[bytes]]]  # `[]` = sin cambios
    handle: Callable[[bytes], Handled]


def feeds_from_settings(rt: Runtime, anchor: GeoAnchor, scenario: Scenario) -> Feeds | None:
    """`Feeds` con lo que dice el entorno, o `None` si `VELA_FEEDS=off`."""
    from contracts.settings import settings

    if settings.vela_feeds == "off":
        return None
    return Feeds(
        rt=rt,
        anchor=anchor,
        scenario=scenario,
        mode=settings.vela_feeds,
        only=frozenset(n.strip() for n in settings.vela_feeds_only.split(",") if n.strip()),
        feeds_dir=Path(settings.vela_feeds_dir),
        firms_key=settings.firms_map_key,
        strict=settings.vela_mode == "dev",
    )


def off_status(mode: str, anchor: GeoAnchor | None) -> dict[str, Any]:
    """`/api/feeds` sin run (o con las fuentes apagadas): el modo, el ancla y todo `off`."""
    return {
        "mode": mode,
        "anchor": anchor_view(anchor) if anchor is not None else None,
        "sources": {n: FeedState().as_dict() for n in NAMES},
        "detections": [],
    }


@dataclass
class Feeds:
    """Las fuentes de un run. Se crea en `start_run` y se cancela en `stop_run`."""

    rt: Runtime
    anchor: GeoAnchor
    scenario: Scenario
    mode: Literal["live", "recorded"]
    only: frozenset[str] = frozenset()
    feeds_dir: Path = Path("fixtures/feeds")
    firms_key: str = ""
    strict: bool = False  # en desarrollo un registro roto lanza; en la demo solo se cuenta
    save_captures: bool = True  # `--probe` no guarda nada; `--capture` sí (REQ-259)
    client: httpx.AsyncClient | None = None  # inyectable: los tests no salen a la red

    states: dict[str, FeedState] = field(init=False)
    schedule: Schedule = field(init=False, default_factory=Schedule)
    ctx: FeedContext = field(init=False)
    _detections: dict[tuple, dict[str, Any]] = field(init=False, default_factory=dict)
    _dgt_etag: str | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.states = {n: FeedState() for n in NAMES}
        self.ctx = FeedContext(
            origin_cell=self.scenario.hazard.origin_cell, cell_size=self.scenario.hazard.cell_size
        )

    # --- qué fuentes arrancan ------------------------------------------------------------

    def _off(self, name: str, note: str) -> None:
        st = self.states[name]
        st.status, st.note = "off", note

    @property
    def _dated(self) -> bool:
        return self.anchor.reference_start is not None

    def plan_sources(self) -> list[SourceSpec]:
        """Decide qué fuentes arrancan y por qué las demás no (REQ-235). Cada una que queda
        `off` deja escrita la razón: «sin clave» no es un error, es una decisión visible."""
        if not self.anchor.fixed:
            for n in NAMES:
                self._off(n, "ancla sin fijar")
            return []

        specs: list[SourceSpec] = []
        live = self.mode == "live"
        key_of = {"firms": self.firms_key}  # las que piden clave
        for name in NAMES:
            if self.only and name not in self.only:
                self._off(name, "desactivada por VELA_FEEDS_ONLY")
            elif live and name in key_of and not key_of[name]:
                self._off(name, "sin clave")
            elif name == "dgt" and live and self._dated:
                self._off(name, "sin histórico")  # DGT solo cuenta lo que pasa ahora
            elif name == "dgt" and not self.anchor.edges:
                self._off(name, "sin aristas declaradas en el ancla")
            elif not live and not capture.recorded(self.feeds_dir, self.anchor.id, name):
                self._off(name, "sin capturas")
            else:
                specs.append(self._spec(name))
        return specs

    def _spec(self, name: str) -> SourceSpec:
        if self.mode == "recorded":
            return SourceSpec(name, None, self._read_recorded(name), self._handler(name))
        return SourceSpec(name, *self._live(name), self._handler(name))

    def _handler(self, name: str) -> Callable[[bytes], Handled]:
        return {
            "open_meteo": self._handle_open_meteo,
            "dgt": self._handle_dgt,
            "firms": self._handle_firms,
        }[name]

    # --- traer (E/S) ---------------------------------------------------------------------

    def _read_recorded(self, name: str) -> Callable[[], Awaitable[list[bytes]]]:
        async def read() -> list[bytes]:
            paths = capture.recorded(self.feeds_dir, self.anchor.id, name)
            return [await asyncio.to_thread(p.read_bytes) for p in paths]

        return read

    def _live(self, name: str) -> tuple[float | None, Callable[[], Awaitable[list[bytes]]]]:
        assert self.client is not None, "Feeds.run() crea el cliente antes de planificar"
        client, anchor = self.client, self.anchor
        start = anchor.reference_start
        interval: float | None = None if self._dated else INTERVAL_S[name]

        async def open_meteo_fetch() -> list[bytes]:
            if start is not None:
                return [await open_meteo.fetch_hourly(client, anchor, start)]
            return [await open_meteo.fetch_current(client, anchor)]

        async def dgt_fetch() -> list[bytes]:
            data, self._dgt_etag = await dgt.fetch(client, self._dgt_etag)
            return [] if data is None else [data]

        async def firms_fetch() -> list[bytes]:
            date = start.astimezone(UTC).date().isoformat() if start is not None else None
            parts: list[bytes] = []
            last_error: Exception | None = None
            for source in firms.SOURCES:
                # En un día viejo el NRT no da error: da un CSV vacío. Por eso, en modo
                # fechado se piden el NRT **y** el procesado estándar, y la deduplicación por
                # `source` une los focos que salgan en los dos.
                candidates = (source,) if date is None else (source, firms.SP_FALLBACK[source])
                for candidate in candidates:
                    try:
                        parts.append(
                            await firms.fetch(client, self.firms_key, anchor, candidate, 1, date)
                        )
                    except httpx.HTTPError as exc:
                        last_error = exc
                        log.warning("FIRMS %s falló: %s", candidate, self._censor(repr(exc)))
            if not parts and last_error is not None:
                raise last_error
            return parts

        return interval, {
            "open_meteo": open_meteo_fetch,
            "dgt": dgt_fetch,
            "firms": firms_fetch,
        }[name]

    # --- traducir (puro, con el contexto del run) ------------------------------------------

    def _refresh_ctx(self) -> None:
        """Lo que `to_facts` necesita saber del mundo, tal y como está ahora."""
        state = self.rt.world_state()
        self.ctx.cells = state.cells if state is not None else {}
        plan = self.rt.current_plan()
        routes = [a.route for a in plan.assignments] if plan is not None else []
        self.ctx.route_edges = frozenset(edges_on_route(routes, self.scenario.roads))

    def _handle_open_meteo(self, raw: bytes) -> Handled:
        data = json.loads(raw)
        parsed = open_meteo.parse_hourly(data) if "hourly" in data else open_meteo.parse_current(data)
        return Handled(open_meteo.to_facts(parsed.records, self.anchor, self.ctx), parsed.malformed)

    def _handle_dgt(self, raw: bytes) -> Handled:
        parsed = dgt.parse_situations(raw)
        return Handled(dgt.to_facts(parsed.records, self.anchor, self.ctx), parsed.malformed)

    def _handle_firms(self, raw: bytes) -> Handled:
        parsed = firms.parse_csv(raw.decode("utf-8", errors="replace"))
        for d in firms.detections(parsed.records, self.anchor):
            self._detections[(d["x"], d["z"], d["t_real"], d["satellite"])] = d
        return Handled(firms.to_facts(parsed.records, self.anchor, self.ctx), parsed.malformed)

    # --- los bucles ------------------------------------------------------------------------

    def _censor(self, text: str) -> str:
        return capture.censor(text, (self.firms_key,))

    def _ingest(self, spec: SourceSpec, parts: list[bytes]) -> None:
        st = self.states[spec.name]
        self._refresh_ctx()
        malformed = 0
        errors: list[Exception] = []
        for raw in parts:
            if self.mode == "live" and self.save_captures:
                capture.save_raw(
                    self.feeds_dir, self.anchor.id, spec.name, _ext(spec.name, raw), raw,
                    datetime.now(UTC),
                )
            try:
                handled = spec.handle(raw)
            except Exception as exc:  # noqa: BLE001 — una parte ilegible no tumba a las buenas
                errors.append(exc)
                log.warning("%s: parte ilegible, se salta: %s", spec.name, self._censor(repr(exc)))
                continue
            malformed += handled.malformed
            for obs in handled.observations:
                self.schedule.push(obs, self.anchor)
        if errors and len(errors) == len(parts):
            raise errors[-1]  # nada legible: la fuente está mal, no es un registro suelto
        st.malformed += malformed + len(errors)
        # Después de encolar, no antes: si lanzara antes, los hechos ya marcados como vistos
        # en `ctx.seen` se perderían para siempre y el siguiente ciclo no los repondría.
        if (malformed or errors) and self.strict:
            raise ValueError(f"{spec.name}: {malformed} registros rotos y {len(errors)} partes ilegibles")

    async def cycle(self, spec: SourceSpec) -> float | None:
        """Un ciclo de una fuente: traer, traducir y encolar. Devuelve cuánto esperar hasta
        el siguiente, o `None` si no hay siguiente (una fuente de «una vez» que fue bien).

        Es un método suelto, y no el cuerpo del bucle, porque `--probe` necesita **un** ciclo
        sin publicar ni esperar. Una fuente que falla nunca devuelve `None`: reintenta.
        """
        st = self.states[spec.name]
        try:
            parts = await spec.fetch()
            if parts:
                self._ingest(spec, parts)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — degradar es el objetivo (REQ-256)
            st.status = "degraded"
            st.last_error = self._censor(repr(exc))
            st.backoff_s = min(BACKOFF_MAX_S, st.backoff_s * 2 or BACKOFF_FIRST_S)
            log.warning(
                "fuente %s degradada, reintento en %.0f s: %s", spec.name, st.backoff_s, st.last_error
            )
            return st.backoff_s
        st.status, st.last_error, st.backoff_s = "ok", None, 0.0
        st.last_ok_t_wall = datetime.now(UTC).isoformat(timespec="seconds")
        return spec.interval_s

    async def _run_source(self, spec: SourceSpec) -> None:
        st = self.states[spec.name]
        while True:
            wait = await self.cycle(spec)
            if wait is None:
                st.next_poll_at = None
                return
            st.next_poll_at = time.monotonic() + wait
            await asyncio.sleep(wait)

    def t_sim(self) -> float:
        """El `t_sim` del run. Sale del estado del core y no de `hub.last_t_sim`, que es un
        máximo que no baja al empezar un run nuevo en el mismo proceso: con él, en el
        segundo run los datos fechados saldrían todos de golpe."""
        state = self.rt.world_state()
        return state.t_sim if state is not None else self.rt.hub.last_t_sim

    async def publish_due(self) -> int:
        n = 0
        for obs in self.schedule.pop_due(self.t_sim()):
            try:
                await self.rt.publish(EventType.WORLD_FACT_ASSERTED, obs.fact, "feeds")
            except Exception:
                log.exception("no se pudo publicar %s", obs.fact.key)
                continue
            n += 1
            tag = obs.fact.source.split(":")[1] if obs.fact.source.startswith("api:") else ""
            if tag in SOURCE_TAG:
                self.states[SOURCE_TAG[tag]].published += 1
        return n

    async def _emit_loop(self) -> None:
        while True:
            await self.publish_due()
            await asyncio.sleep(EMIT_EVERY_S)

    async def run(self) -> None:
        """Lo que `Runtime.spawn("feeds", …)` supervisa. Al cancelarlo se cancela todo y se
        cierra el cliente HTTP: un Ctrl-C no deja una petición colgada (REQ-261)."""
        async with contextlib.AsyncExitStack() as stack:
            if self.client is None and self.mode == "live":
                self.client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT_S
                    )
                )
            tasks = [asyncio.create_task(self._emit_loop(), name="vela.feeds.emit")]
            tasks += [
                asyncio.create_task(self._run_source(s), name=f"vela.feeds.{s.name}")
                for s in self.plan_sources()
            ]
            try:
                await asyncio.gather(*tasks)
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "anchor": anchor_view(self.anchor),
            "sources": {n: s.as_dict() for n, s in self.states.items()},
            "detections": list(self._detections.values()),
        }


def _ext(name: str, raw: bytes) -> str:
    """La extensión de una captura."""
    return {"open_meteo": ".json", "dgt": ".xml", "firms": ".csv"}[name]
