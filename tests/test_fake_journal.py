"""El journal falso que sustituye al golden hasta el sábado a las 13:00.

Mismos criterios que `tests/test_golden_replay.py` le exige al de verdad —payloads
que validan, `seq` sin huecos, `t_sim` monótono, catálogo completo— más dos que son
propios de un fixture generado: que sea determinista y que la cadena de `causes`
llegue de la llamada a la orden. Sin esa cadena, el panel del H3 no tiene nada que
dibujar y eso se descubre el sábado a las once.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from contracts.events import PAYLOAD_MODELS, Event, EventType

FAKE = Path("fixtures/run_fake.jsonl")
FAKE_V2 = Path("fixtures/run_fake_v2.jsonl")
GEN = Path("scripts/fake_journal.py")

pytestmark = pytest.mark.skipif(
    not FAKE.exists(),
    reason="falta fixtures/run_fake.jsonl · uv run python scripts/fake_journal.py",
)


def _events(path: Path = FAKE) -> list[Event]:
    return [
        Event.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_payloads_validan() -> None:
    for ev in _events():
        model = PAYLOAD_MODELS.get(ev.type)
        assert model is not None, f"tipo fuera del catálogo: {ev.type}"
        model.model_validate(ev.payload)


def test_seq_sin_huecos_y_t_sim_monotono() -> None:
    last_seq, last_t = 0, -1.0
    for ev in _events():
        assert ev.seq == last_seq + 1, f"hueco en seq: {last_seq} → {ev.seq}"
        assert ev.t_sim >= last_t, f"t_sim retrocede en seq {ev.seq}"
        last_seq, last_t = ev.seq, ev.t_sim


def test_catalogo_completo() -> None:
    """Seis minutos con todos los tipos del catálogo al menos una vez."""
    seen = {ev.type for ev in _events()}
    missing = sorted(set(PAYLOAD_MODELS) - seen)
    assert not missing, f"tipos que el fixture nunca ejercita: {missing}"


def test_causas_anteriores_al_efecto() -> None:
    for ev in _events():
        assert all(c < ev.seq for c in ev.causes), f"causa futura en seq {ev.seq}"


def test_la_cadena_del_climax_esta_completa() -> None:
    """llamada → hecho → violación → replan → política → plan → orden.

    Se sigue por `causes` de verdad, eslabón a eslabón: que existan los siete tipos
    no sirve de nada si no están encadenados.
    """
    events = _events()
    by_seq = {ev.seq: ev for ev in events}

    def ancestors(ev: Event) -> set[EventType]:
        seen: set[EventType] = set()
        stack = list(ev.causes)
        while stack:
            parent = by_seq.get(stack.pop())
            if parent is None or parent.type in seen:
                continue
            seen.add(parent.type)
            stack.extend(parent.causes)
        return seen

    orders = [
        ev
        for ev in events
        if ev.type == EventType.ACTION_REQUESTED
        and EventType.CALL_ENDED in ancestors(ev)
    ]
    assert orders, "ninguna orden desciende de una llamada"

    chain = ancestors(orders[0])
    for link in (
        EventType.CALL_STARTED,
        EventType.CALL_ENDED,
        EventType.WORLD_FACT_ASSERTED,
        EventType.PLAN_VIOLATION,
        EventType.PLAN_REPLAN_STARTED,
        EventType.PLAN_POLICY_EMITTED,
        EventType.PLAN_EMITTED,
    ):
        assert link in chain, f"falta {link} en la cadena de causas"


def _generate(variant: str, out: Path) -> None:
    subprocess.run(
        [sys.executable, str(GEN), "--variant", variant, "--out", str(out)],
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize(("variant", "fixture"), [("v1", FAKE), ("v2", FAKE_V2)])
def test_es_determinista(variant: str, fixture: Path, tmp_path: Path) -> None:
    """Dos generaciones, los mismos bytes. Si no, no se puede comparar por hash y
    cualquier diff del fixture se vuelve ilegible.

    Y el v1 es además el candado del fixture del H2/H3: los criterios de aceptación de
    las dos specs anteriores van por sus `t_sim`, así que el día que alguien meta una
    escena en medio sin darse cuenta, salta aquí.
    """
    if not fixture.exists():
        pytest.skip(f"falta {fixture} · uv run python scripts/fake_journal.py")
    out = tmp_path / fixture.name
    _generate(variant, out)
    assert out.read_bytes() == fixture.read_bytes()


@pytest.mark.skipif(not FAKE_V2.exists(), reason="falta fixtures/run_fake_v2.jsonl")
def test_v2_trae_la_llamada_sin_extraer() -> None:
    """El caso que el panel de llamadas no puede romper: `CallResult.facts is None`.

    En el v1 no existía ninguna, así que el `SIN EXTRAER` del H4 se habría probado por
    primera vez con el golden, el domingo por la mañana.
    """
    ended = [ev for ev in _events(FAKE_V2) if ev.type == EventType.CALL_ENDED]
    sin_extraer = [ev for ev in ended if ev.payload["facts"] is None]
    assert len(sin_extraer) == 1, "el v2 tiene exactamente una llamada sin extraer"
    assert sin_extraer[0].payload["outcome"] == "no_answer"

    # Y el humano supliendo lo que el teléfono no dio, encadenado a esa llamada.
    override = [ev for ev in _events(FAKE_V2) if ev.type == EventType.HUMAN_OVERRIDE]
    assert any(
        ev.payload["kind"] == "assert_fact" and sin_extraer[0].seq in ev.causes
        for ev in override
    ), "el assert_fact del v2 no cuelga de la llamada sin respuesta"


@pytest.mark.skipif(not FAKE_V2.exists(), reason="falta fixtures/run_fake_v2.jsonl")
def test_v2_cumple_lo_mismo_que_v1() -> None:
    """Las cuatro reglas del fixture, también en el variant nuevo."""
    events = _events(FAKE_V2)
    last_seq, last_t = 0, -1.0
    for ev in events:
        assert PAYLOAD_MODELS[ev.type].model_validate(ev.payload) is not None
        assert ev.seq == last_seq + 1
        assert ev.t_sim >= last_t
        assert all(c < ev.seq for c in ev.causes)
        last_seq, last_t = ev.seq, ev.t_sim
    assert not set(PAYLOAD_MODELS) - {ev.type for ev in events}
