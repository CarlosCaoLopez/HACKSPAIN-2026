"""El poller de las fuentes reales (SPEC-007, F3 · REQ-233…237, 246, 256…261). P4.

Nada sale a la red: el cliente HTTP es un `httpx.MockTransport` y el `Runtime` es un doble
mínimo. Lo que se prueba es lo que tiene lógica de verdad:

- **La degradación**: una fuente que falla se anota y reintenta, sin tumbar el run ni a las
  demás, y **sin filtrar la clave** en el mensaje de error.
- **El reloj fechado**: un día real sale repartido en `t_sim`, no de golpe.
- **`recorded`**: reproducir capturas por el mismo camino que `live`, sin red.
- **La cancelación**: parar un run no deja tareas colgadas (REQ-261).
"""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from contracts.events import EventType
from gateway.feeds.anchor import EdgeRef, GeoAnchor
from gateway.feeds.poller import BACKOFF_FIRST_S, NAMES, Feeds, off_status
from gateway.scenarios import load_scenario

FIX = Path("fixtures/feeds/_test")
DGT = (FIX / "dgt_sample.xml").read_bytes()
METEO = (FIX / "open_meteo_hourly.json").read_bytes()
FIRMS = (FIX / "firms_viirs_synthetic.csv").read_bytes()
SCENARIO = load_scenario("wildfire_ridge")
EDGE = "road:wp_a-wp_b"


class FakeRt:
    """Lo que `Feeds` usa de `Runtime`: el estado, el plan, el reloj y `publish`."""

    def __init__(self, t_sim: float = 0.0, state=None, plan=None, fail_first: bool = False):
        self.hub = SimpleNamespace(last_t_sim=t_sim)
        self.state, self.plan = state, plan
        self.published: list = []
        self._fail_first = fail_first

    def world_state(self):
        return self.state

    def current_plan(self):
        return self.plan

    async def publish(self, type_, payload, source, causes=None):
        if self._fail_first:
            self._fail_first = False
            raise RuntimeError("bus caído")
        self.published.append((type_, payload, source))


def anchor(**over) -> GeoAnchor:
    base = {
        "id": "t",
        "place": "Sitio de prueba",
        "lat0": 40.0,
        "lon0": -4.0,
        "meters_per_block": 25,
        "edges": {EDGE: EdgeRef(road_name="A-8005", pk_from=1.0, pk_to=2.5)},
    }
    return GeoAnchor(**{**base, **over})


def transport(routes: dict[str, bytes | int | Exception]) -> httpx.AsyncClient:
    """Un cliente que contesta según un trozo de la URL. `int` = ese status; una excepción
    se lanza tal cual."""

    def handler(request: httpx.Request) -> httpx.Response:
        for needle, answer in routes.items():
            if needle in str(request.url):
                if isinstance(answer, Exception):
                    raise answer
                if isinstance(answer, int):
                    return httpx.Response(answer, request=request)
                return httpx.Response(200, content=answer, request=request)
        return httpx.Response(404, request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


_CAPTURES = {"dir": Path()}


@pytest.fixture(autouse=True)
def _captures_in_tmp(tmp_path: Path):
    """En `live` el poller guarda cada respuesta cruda. Sin esto, cada test escribiría sus
    capturas en `fixtures/feeds/` —dentro del repo— y `fixtures/**` solo se añade a mano."""
    _CAPTURES["dir"] = tmp_path / "capturas"
    yield


def make_feeds(rt=None, client=None, mode="live", **over) -> Feeds:
    kwargs = {
        "rt": rt or FakeRt(),
        "anchor": anchor(),
        "scenario": SCENARIO,
        "mode": mode,
        "feeds_dir": _CAPTURES["dir"],
    }
    return Feeds(client=client, **{**kwargs, **over})


async def run_source_once(feeds: Feeds, name: str) -> None:
    """Un ciclo de una fuente: espera a que deje de estar `off` y la cancela. Sin sleeps
    a ciegas: el ciclo se comprueba por su estado."""
    spec = next(s for s in feeds.plan_sources() if s.name == name)
    task = asyncio.create_task(feeds._run_source(spec))
    for _ in range(300):
        await asyncio.sleep(0.01)
        if task.done() or feeds.states[name].status != "off":
            break
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def facts(rt: FakeRt) -> list:
    return [payload for _, payload, _ in rt.published]


# --- qué arranca y por qué no ---------------------------------------------------------------


def test_un_ancla_sin_fijar_apaga_todo_y_lo_dice():
    feeds = make_feeds(anchor=anchor(fixed=False))
    assert feeds.plan_sources() == []
    assert {s.note for s in feeds.states.values()} == {"ancla sin fijar"}
    assert {s.status for s in feeds.states.values()} == {"off"}


def test_sin_clave_no_es_un_error_es_off_con_nota():
    feeds = make_feeds(client=transport({}))
    started = {s.name for s in feeds.plan_sources()}
    assert "firms" not in started and "aemet" not in started
    assert feeds.states["firms"].note == "sin clave" and feeds.states["firms"].status == "off"
    assert feeds.states["aemet"].note == "sin clave"


def test_solo_las_fuentes_pedidas_arrancan():
    feeds = make_feeds(client=transport({}), only=frozenset({"open_meteo"}))
    assert [s.name for s in feeds.plan_sources()] == ["open_meteo"]
    assert feeds.states["dgt"].note == "desactivada por VELA_FEEDS_ONLY"


def test_dgt_fechado_no_tiene_historico_y_sin_aristas_no_tiene_a_que_casar():
    dated = make_feeds(
        client=transport({}), anchor=anchor(reference_start=datetime(2025, 8, 14, 12, tzinfo=UTC))
    )
    dated.plan_sources()
    assert dated.states["dgt"].note == "sin histórico"

    bare = make_feeds(client=transport({}), anchor=anchor(edges={}))
    bare.plan_sources()
    assert bare.states["dgt"].note == "sin aristas declaradas en el ancla"


def test_recorded_sin_capturas_deja_cada_fuente_off():
    feeds = make_feeds(mode="recorded", feeds_dir=Path("no/existe"))
    assert feeds.plan_sources() == []
    assert {s.note for s in feeds.states.values()} == {"sin capturas"}


def test_off_status_tiene_la_forma_de_api_feeds():
    body = off_status("off", None)
    assert set(body) == {"mode", "anchor", "sources", "detections"}
    assert set(body["sources"]) == set(NAMES)
    assert body["anchor"] is None and body["detections"] == []


# --- un ciclo en vivo ---------------------------------------------------------------------


async def test_dgt_en_vivo_publica_el_corte_y_no_lo_republica():
    rt = FakeRt()
    feeds = make_feeds(rt, transport({"nap.dgt.es": DGT}), only=frozenset({"dgt"}))
    await run_source_once(feeds, "dgt")
    assert feeds.states["dgt"].status == "ok" and feeds.states["dgt"].last_ok_t_wall

    assert await feeds.publish_due() == 2  # el `cut` y la `cause`
    assert {p.key for p in facts(rt)} == {"road:wp_a-wp_b:cut", "road:wp_a-wp_b:cause"}
    assert all(t == EventType.WORLD_FACT_ASSERTED for t, _, _ in rt.published)
    assert all(src == "feeds" for _, _, src in rt.published)  # la envoltura (REQ-236)
    assert feeds.states["dgt"].published == 2

    await run_source_once(feeds, "dgt")  # el siguiente sondeo trae el mismo feed
    assert await feeds.publish_due() == 0


async def test_las_capturas_crudas_se_guardan_tal_cual(tmp_path: Path):
    feeds = make_feeds(client=transport({"nap.dgt.es": DGT}), feeds_dir=tmp_path, only=frozenset({"dgt"}))
    await run_source_once(feeds, "dgt")
    saved = list((tmp_path / "t" / "dgt").glob("*.xml"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == DGT  # auditable: es exactamente lo que devolvió DGT


async def test_una_fuente_caida_se_degrada_con_backoff_y_el_resto_sigue():
    client = transport({"nap.dgt.es": httpx.ConnectError("sin red"), "open-meteo": METEO})
    feeds = make_feeds(client=client, only=frozenset({"dgt", "open_meteo"}))
    await run_source_once(feeds, "dgt")
    st = feeds.states["dgt"]
    assert st.status == "degraded" and "sin red" in st.last_error
    assert st.backoff_s == BACKOFF_FIRST_S

    await run_source_once(feeds, "open_meteo")  # una fuente caída no arrastra a las demás
    assert feeds.states["open_meteo"].status == "ok"


async def test_la_clave_nunca_aparece_en_el_error_de_una_fuente():
    feeds = make_feeds(
        client=transport({"firms.modaps": 500}),
        anchor=anchor(edges={}),
        firms_key="CLAVE-SECRETA-123",
        only=frozenset({"firms"}),
    )
    await run_source_once(feeds, "firms")
    st = feeds.states["firms"]
    assert st.status == "degraded"
    assert "CLAVE-SECRETA-123" not in (st.last_error or "")


async def _waits_of(feeds: Feeds, name: str, monkeypatch: pytest.MonkeyPatch, cycles: int) -> list[float]:
    """Las esperas que pide una fuente entre ciclos, sin esperar de verdad."""
    from gateway.feeds import poller

    spec = next(s for s in feeds.plan_sources() if s.name == name)
    waits: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) >= cycles:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(poller.asyncio, "sleep", fake_sleep)
    with contextlib.suppress(asyncio.CancelledError):
        await feeds._run_source(spec)
    return waits


async def test_el_backoff_se_duplica_y_topa_en_600_s(monkeypatch: pytest.MonkeyPatch):
    feeds = make_feeds(client=transport({"nap.dgt.es": 500}), only=frozenset({"dgt"}))
    waits = await _waits_of(feeds, "dgt", monkeypatch, cycles=7)
    assert waits == [30.0, 60.0, 120.0, 240.0, 480.0, 600.0, 600.0]
    assert feeds.states["dgt"].status == "degraded"


async def test_un_exito_resetea_el_backoff(monkeypatch: pytest.MonkeyPatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # Falla dos veces y luego contesta bien.
        return httpx.Response(500 if calls["n"] <= 2 else 200, content=DGT, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    feeds = make_feeds(client=client, only=frozenset({"dgt"}))
    waits = await _waits_of(feeds, "dgt", monkeypatch, cycles=4)
    # Dos fallos (30, 60), un éxito (espera el intervalo normal) y otro ciclo bueno.
    assert waits[:2] == [30.0, 60.0] and waits[2] == waits[3] == 120.0
    assert feeds.states["dgt"].status == "ok" and feeds.states["dgt"].backoff_s == 0.0


async def test_un_run_fechado_sale_repartido_en_t_sim_y_no_de_golpe():
    a = anchor(reference_start=datetime(2025, 8, 14, 12, 0, tzinfo=timezone(timedelta(hours=2))))
    rt = FakeRt(t_sim=0.0)
    feeds = make_feeds(rt, transport({"open-meteo": METEO}), anchor=a, only=frozenset({"open_meteo"}))
    await run_source_once(feeds, "open_meteo")
    total = len(feeds.schedule)
    assert total > 2

    early = await feeds.publish_due()  # t_sim = 0: solo lo anterior al arranque del run
    assert 0 < early < total

    rt.hub.last_t_sim = 1e9
    assert early + await feeds.publish_due() == total
    assert feeds.states["open_meteo"].published == total


async def test_el_t_sim_sale_del_estado_del_core_y_no_del_maximo_del_hub():
    """`hub.last_t_sim` es un máximo que no baja al empezar otro run: con él, en el segundo
    run del proceso los datos fechados saldrían todos de golpe."""
    state = SimpleNamespace(t_sim=5.0, cells={})
    feeds = make_feeds(FakeRt(t_sim=999.0, state=state))
    assert feeds.t_sim() == 5.0
    assert make_feeds(FakeRt(t_sim=999.0)).t_sim() == 999.0  # sin core, no hay otra cosa


async def test_los_focos_de_firms_salen_como_hechos_y_como_detecciones_del_mapa():
    rt = FakeRt()
    feeds = make_feeds(
        rt,
        transport({"firms.modaps": FIRMS}),
        anchor=anchor(edges={}),
        firms_key="k",
        only=frozenset({"firms"}),
    )
    await run_source_once(feeds, "firms")
    # Tres satélites devuelven las mismas seis filas: la deduplicación deja 3 hechos.
    assert await feeds.publish_due() == 3
    assert len(feeds.status()["detections"]) == 4  # el mapa enseña también el foco de z < 0
    assert feeds.states["firms"].malformed >= 1  # la fila de confianza `x`


async def test_una_parte_ilegible_no_tumba_a_las_buenas_pero_todas_ilegibles_si():
    good, bad = FIRMS, b"Invalid MAP_KEY."
    routes = {"VIIRS_NOAA21_NRT": good, "VIIRS_NOAA20_NRT": bad, "VIIRS_SNPP_NRT": good}
    feeds = make_feeds(
        client=transport(routes), anchor=anchor(edges={}), firms_key="k", only=frozenset({"firms"})
    )
    await run_source_once(feeds, "firms")
    assert feeds.states["firms"].status == "ok"
    assert len(feeds.schedule) == 3

    all_bad = make_feeds(
        client=transport({"firms.modaps": bad}), anchor=anchor(edges={}), firms_key="k",
        only=frozenset({"firms"}),
    )
    await run_source_once(all_bad, "firms")
    assert all_bad.states["firms"].status == "degraded"


async def test_en_desarrollo_un_registro_roto_lanza_pero_no_pierde_los_buenos():
    strict = make_feeds(
        client=transport({"firms.modaps": FIRMS}), anchor=anchor(edges={}), firms_key="k",
        only=frozenset({"firms"}), strict=True,
    )
    await run_source_once(strict, "firms")
    assert strict.states["firms"].status == "degraded"  # REQ-258: lanza en desarrollo
    assert len(strict.schedule) == 3  # y los tres focos buenos están en la cola

    lenient = make_feeds(
        client=transport({"firms.modaps": FIRMS}), anchor=anchor(edges={}), firms_key="k",
        only=frozenset({"firms"}), strict=False,
    )
    await run_source_once(lenient, "firms")
    assert lenient.states["firms"].status == "ok"  # en la demo se cuenta y se sigue


# --- el contexto del mundo ------------------------------------------------------------------


async def test_un_corte_sobre_una_arista_del_plan_es_critico():
    road = SCENARIO.roads[0]
    plan = SimpleNamespace(assignments=[SimpleNamespace(route=[road.a, road.b])])
    rt = FakeRt(plan=plan)
    a = anchor(edges={road.id: EdgeRef(road_name="A-8005", pk_from=1.0, pk_to=2.5)})
    feeds = make_feeds(rt, transport({"nap.dgt.es": DGT}), anchor=a, only=frozenset({"dgt"}))
    await run_source_once(feeds, "dgt")
    await feeds.publish_due()
    assert {p.severity for p in facts(rt)} == {"critical"}


# --- publicar y parar -------------------------------------------------------------------------


async def test_un_hecho_que_no_se_publica_no_mata_la_emision():
    rt = FakeRt(fail_first=True)
    feeds = make_feeds(rt, transport({"nap.dgt.es": DGT}), only=frozenset({"dgt"}))
    await run_source_once(feeds, "dgt")
    assert await feeds.publish_due() == 1  # el primero falló, el segundo salió
    assert len(rt.published) == 1


async def test_recorded_reproduce_las_capturas_sin_red(tmp_path: Path):
    folder = tmp_path / "t" / "dgt"
    folder.mkdir(parents=True)
    (folder / "2025-08-14T100000Z.xml").write_bytes(DGT)
    rt = FakeRt()
    feeds = make_feeds(rt, mode="recorded", feeds_dir=tmp_path, only=frozenset({"dgt"}))
    await run_source_once(feeds, "dgt")

    assert feeds.states["dgt"].status == "ok"
    assert feeds.states["dgt"].as_dict()["next_poll_s"] is None  # una vez, no sondea
    assert await feeds.publish_due() == 2
    assert len(list(folder.iterdir())) == 1  # reproducir no guarda capturas nuevas


async def test_cancelar_run_no_deja_tareas_colgadas():
    feeds = make_feeds(client=transport({"open-meteo": METEO}), only=frozenset({"open_meteo"}))
    task = asyncio.create_task(feeds.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    leftovers = [t for t in asyncio.all_tasks() if t.get_name().startswith("vela.feeds") and not t.done()]
    assert leftovers == []
