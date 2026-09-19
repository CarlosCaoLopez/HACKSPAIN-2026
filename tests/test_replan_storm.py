# ruff: noqa: F811 — los fixtures importados se vuelven a nombrar como parámetros de test
"""Una violación dura que el planner no puede resolver no dispara un replan por
evento. El plan la trae de fábrica (residual) y el siguiente tick no es novedad."""

from test_core_tasks import (  # noqa: F401 - fixtures compartidos (rootdir tests/ en sys.path)
    _ev,
    _ignite,
    _of,
    _scenario,
    fixed_planner,
    journal,
)

from contracts import bus
from contracts.events import EventType
from contracts.plan import Violation
from core import loop


def _stuck_verify(state, plan, graph):
    return [
        Violation(
            verifier="coverage_maintained",
            severity="hard",
            message="cobertura de Pueblo B baja de 1 a 0",
        )
    ]


async def _ticks(core: loop.Core, n: int, start: float = 1.0) -> None:
    for i in range(n):
        ev = _ev(
            EventType.WORLD_TICK,
            {"t_sim": start + i, "wind": {"bearing_deg": 270, "speed": 1.0}},
            t_sim=start + i,
        )
        await bus.publish(ev)
        await core.on_event(ev)


async def test_residual_hard_violation_does_not_storm(
    journal, fixed_planner, monkeypatch
) -> None:
    monkeypatch.setattr(loop, "verify", _stuck_verify)
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    n_after_initial = len(_of(journal, EventType.PLAN_REPLAN_STARTED))
    assert n_after_initial == 1
    await _ticks(core, 20)
    replans = [
        e
        for e in _of(journal, EventType.PLAN_REPLAN_STARTED)
        if e.payload.get("trigger") == "hard_violation"
    ]
    assert replans == [], "la misma violación residual no vuelve a llamar al modelo"
    assert fixed_planner["n"] <= 2  # plan inicial + a lo sumo su crítica


async def test_new_hard_violation_still_replans_after_gap(
    journal, fixed_planner, monkeypatch
) -> None:
    messages = ["cobertura de Pueblo B baja de 1 a 0"]

    def _verify(state, plan, graph):
        return [
            Violation(verifier="coverage_maintained", severity="hard", message=m)
            for m in messages
        ]

    monkeypatch.setattr(loop, "verify", _verify)
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    await _ticks(core, 5)  # nada nuevo: sin replans
    messages.append("ruta de unit_truck1 cruza una celda en llamas")  # novedad
    await _ticks(core, 5, start=10.0)
    replans = [
        e
        for e in _of(journal, EventType.PLAN_REPLAN_STARTED)
        if e.payload.get("trigger") == "hard_violation"
    ]
    assert len(replans) == 1, "una violación nueva sí replanifica, y una sola vez"
