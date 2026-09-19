"""La puntuación provisional y el número del pitch. P4.

`journal/score.py` es de P1 y no tiene cuerpo, así que esto prueba el contador de
reserva del gateway. Lo que tiene lógica de verdad y se cubre aquí:

- **Se cuenta el estado final, no los cambios.** Una celda que arde y se apaga no es una
  celda quemada, y un grupo de civiles que pasa por tres estados cuenta una vez.
- **`total` sale vacío.** La fórmula es de P1 y un número inventado justo donde el jurado
  mira el marcador no se pone.
- **El colgar → giro va por la cadena de `causes` y en `t_wall`.** Es el número del
  pitch: emparejar por cercanía temporal daría un número mejor que el real, y medirlo en
  `t_sim` daría uno que no significa nada.
- **Un journal a medias no revienta.** Cada Ctrl-C deja uno.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contracts.events import Event, EventType
from contracts.settings import settings
from gateway import main as gateway_main
from gateway import score_fallback
from gateway.scenarios import load_scenario

FIXTURE = Path("fixtures/run_fake_v2.jsonl")

pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="falta fixtures/run_fake_v2.jsonl · uv run python scripts/fake_journal.py",
)


@pytest.fixture(autouse=True)
def _sin_cache() -> None:
    """El contador cachea por `(mtime, size)`; los tests escriben ficheros nuevos en
    rutas temporales, pero más vale no arrastrar estado entre ellos."""
    score_fallback._CACHE.clear()


# --- lo que se cuenta ---------------------------------------------------------------


def test_los_numeros_del_fixture() -> None:
    counted = score_fallback.count(FIXTURE)
    score = counted.score
    assert (score.replans, score.llm_calls, score.calls_placed) == (2, 2, 3)
    assert score.run_id == "run_fake_0001"
    assert score.scenario_id == "wildfire_ridge"
    assert counted.incomplete is False


def test_solo_cuenta_el_estado_final_de_cada_celda() -> None:
    """25 `world.cell.changed` en el fixture y 8 celdas acaban quemadas: si esto contara
    eventos en vez de estados finales, el mapa y el marcador dirían cosas distintas."""
    assert score_fallback.count(FIXTURE).score.cells_burnt == 8


def test_civiles_por_grupo_y_no_por_evento() -> None:
    score = score_fallback.count(FIXTURE).score
    # El grupo de Pueblo A llega a `safe` una sola vez, con el `count` que declara el YAML:
    # contar eventos y no grupos daría más.
    pueblo_a = next(c for c in load_scenario("wildfire_ridge").civilians if c.id == "civ_pueblo_a")
    assert score.civilians_safe == pueblo_a.count
    assert score.civilians_exposed_end == 0


def test_total_sale_vacio() -> None:
    """La fórmula es de P1. Un hueco es información; un cero es una respuesta falsa."""
    assert score_fallback.count(FIXTURE).as_json()["total"] is None


# --- el número del pitch -------------------------------------------------------------


def _ev(seq: int, type_: EventType, t_wall: datetime, causes: list[int] | None = None) -> Event:
    payloads = {
        EventType.CALL_ENDED: {
            "call_id": "c1",
            "task_id": None,
            "direction": "inbound",
            "started_t": 0.0,
            "ended_t": 1.0,
            "outcome": "answered",
            "transcript": "…",
            "facts": None,
        },
        EventType.ACTION_REQUESTED: {"action_id": "a1", "verb": "goto", "args": {}},
        EventType.WORLD_FACT_ASSERTED: {
            "key": "road:x:cut",
            "value": True,
            "confidence": 1.0,
            "source": "call:c1",
            "severity": "critical",
        },
    }
    return Event(
        run_id="run_test",
        seq=seq,
        t_wall=t_wall,
        t_sim=float(seq),  # a propósito distinto del reloj de pared
        type=type_,
        source="core",
        payload=payloads[type_],
        causes=causes or [],
    )


def test_colgar_a_giro_se_mide_en_t_wall_por_la_cadena_de_causes() -> None:
    """La orden desciende del colgado a través de un hecho: dos saltos de `causes`."""
    base = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
    turns = score_fallback.HangupToTurn()
    turns.feed(_ev(1, EventType.CALL_ENDED, base))
    turns.feed(_ev(2, EventType.WORLD_FACT_ASSERTED, base + timedelta(seconds=0.8), [1]))
    delta = turns.feed(
        _ev(3, EventType.ACTION_REQUESTED, base + timedelta(seconds=1.7), [2])
    )
    assert delta == pytest.approx(1.7)  # t_wall, no t_sim (que daría 2.0)
    assert turns.unresolved == 0


def test_una_orden_sin_cadena_no_se_empareja() -> None:
    """Emparejar por cercanía temporal daría un número del pitch mejor que el real."""
    base = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
    turns = score_fallback.HangupToTurn()
    turns.feed(_ev(1, EventType.CALL_ENDED, base))
    assert turns.feed(_ev(2, EventType.ACTION_REQUESTED, base + timedelta(seconds=0.2))) is None
    assert turns.unresolved == 1
    assert turns.mean_s is None  # ninguna resuelta es None, nunca 0.0


def test_solo_la_primera_orden_cuenta_como_giro() -> None:
    """Las demás órdenes del plan nuevo no son el giro: son el resto del plan."""
    base = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
    turns = score_fallback.HangupToTurn()
    turns.feed(_ev(1, EventType.CALL_ENDED, base))
    turns.feed(_ev(2, EventType.ACTION_REQUESTED, base + timedelta(seconds=1.0), [1]))
    assert turns.feed(_ev(3, EventType.ACTION_REQUESTED, base + timedelta(seconds=9.0), [1])) is None
    assert len(turns.turns) == 1


def test_las_llamadas_sin_resolver_se_dicen() -> None:
    """En el fixture hay tres llamadas y solo una acaba en orden. Eso es un dato, no un
    fallo, y por eso viaja como nota hasta la pantalla."""
    counted = score_fallback.count(FIXTURE)
    assert any("sin orden que descienda" in n for n in counted.notes)
    assert counted.score.mean_hangup_to_turn_s is not None


# --- lo que no puede romper ----------------------------------------------------------


def test_un_journal_a_medias_no_revienta(tmp_path: Path) -> None:
    truncado = tmp_path / "run_cortado.jsonl"
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()[:200]
    lines.append('{"run_id": "run_cortado", "seq": 201, "t_')  # el corte de un Ctrl-C
    truncado.write_text("\n".join(lines), encoding="utf-8")

    counted = score_fallback.count(truncado)
    assert counted.incomplete is True
    assert any("no termina en run.ended" in n for n in counted.notes)
    assert any("ilegible" in n for n in counted.notes)


def test_la_cache_no_recuenta(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/api/runs` con doce journals se pide en mitad del pitch."""
    score_fallback.count(FIXTURE)
    llamadas = 0
    original = score_fallback._count_uncached

    def espia(path: Path):
        nonlocal llamadas
        llamadas += 1
        return original(path)

    monkeypatch.setattr(score_fallback, "_count_uncached", espia)
    score_fallback.count(FIXTURE)
    score_fallback.count(FIXTURE)
    assert llamadas == 0, "el contador ha vuelto a recorrer el fichero"


# --- el endpoint ---------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """`runs/` está vacío en el repo, así que el endpoint apunta a uno de mentira."""
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "run_fake_0001.jsonl").write_text(
        FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setattr(gateway_main, "RUNS_DIR", runs)
    monkeypatch.setattr(settings, "vela_mode", "dev")
    with TestClient(gateway_main.app) as c:
        yield c


def test_api_runs_marca_provisional_y_sintetico(client: TestClient) -> None:
    (run,) = client.get("/api/runs").json()
    assert run["provisional"] is True  # lo ha contado el gateway, no P1
    assert run["synthetic"] is True  # `run_fake*`: no puede colarse en el pitch
    assert run["incomplete"] is False
    assert run["score"]["total"] is None
    assert run["score"]["replans"] == 2
