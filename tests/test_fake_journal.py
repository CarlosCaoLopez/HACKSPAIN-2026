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
GEN = Path("scripts/fake_journal.py")

pytestmark = pytest.mark.skipif(
    not FAKE.exists(),
    reason="falta fixtures/run_fake.jsonl · uv run python scripts/fake_journal.py",
)


def _events() -> list[Event]:
    return [
        Event.model_validate_json(line)
        for line in FAKE.read_text(encoding="utf-8").splitlines()
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


def test_es_determinista(tmp_path: Path) -> None:
    """Dos generaciones, los mismos bytes. Si no, no se puede comparar por hash y
    cualquier diff del fixture se vuelve ilegible."""
    out = tmp_path / "run_fake.jsonl"
    subprocess.run(
        [sys.executable, str(GEN), "--out", str(out)],
        check=True,
        capture_output=True,
    )
    assert out.read_bytes() == FAKE.read_bytes()
