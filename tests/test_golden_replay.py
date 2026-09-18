"""Replaya `fixtures/run_golden.jsonl` entero por `apply` y comprueba que ningún
evento revienta la validación.

Se salta solo mientras el fixture no exista: lo graba P2 el sábado por la mañana,
en cuanto el sim mueve unidades. Es el artefacto más valioso del proyecto.
"""

from pathlib import Path

import pytest

from contracts.events import PAYLOAD_MODELS

GOLDEN = Path("fixtures/run_golden.jsonl")

pytestmark = pytest.mark.skipif(
    not GOLDEN.exists(), reason="fixtures/run_golden.jsonl aún no grabado"
)


def test_every_payload_validates() -> None:
    from journal.replay import read

    for ev in read(GOLDEN):
        model = PAYLOAD_MODELS.get(ev.type)
        assert model is not None, f"tipo fuera del catálogo: {ev.type}"
        model.model_validate(ev.payload)


def test_seq_is_monotonic() -> None:
    from journal.replay import read

    last = -1
    for ev in read(GOLDEN):
        assert ev.seq > last, f"hueco o salto en seq: {last} → {ev.seq}"
        last = ev.seq


def test_apply_never_raises() -> None:
    from core.belief import apply, initial_state
    from journal.replay import read
    from sim.scenario import load

    events = list(read(GOLDEN))
    state = initial_state(events[0].run_id, load(Path("scenarios/wildfire_ridge.yaml")))
    for ev in events:
        state = apply(state, ev)
    assert state.seq == events[-1].seq


def test_catalog_fully_exercised() -> None:
    """Un run de 6 minutos con todos los tipos del catálogo al menos una vez."""
    from journal.replay import read

    seen = {ev.type for ev in read(GOLDEN)}
    missing = sorted(set(PAYLOAD_MODELS) - seen)
    assert not missing, f"tipos que el golden nunca ejercita: {missing}"
