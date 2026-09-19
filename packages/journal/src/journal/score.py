"""Métricas de un run. El journal es log, base de datos y dataset a la vez.

Es lo que hace comparable el run 1 contra el run 12 en pantalla partida.
"""

from pathlib import Path

from pydantic import BaseModel

from contracts.events import EventType
from journal.replay import read

# Pesos del `total`. Salvar gente manda; quemar celdas y quemar llamadas al modelo
# restan. Simple y monótono a propósito: run 1 vs run 12 tiene que ser legible.
W_SAFE = 10.0
W_EXPOSED = -8.0
W_BURNT = -1.0
W_LLM = -0.5


class RunScore(BaseModel):
    run_id: str
    scenario_id: str
    civilians_safe: int = 0
    civilians_exposed_end: int = 0
    cells_burnt: int = 0
    replans: int = 0
    llm_calls: int = 0
    calls_placed: int = 0
    mean_hangup_to_turn_s: float | None = None  # colgar → giro, el número del pitch
    total: float = 0.0


def score(path: Path) -> RunScore:
    """Puntúa un journal cerrado. Puro: mismo fichero, misma puntuación."""
    run_id = ""
    scenario_id = ""
    replans = 0
    llm_calls = 0
    calls_placed = 0
    replan_score: float | None = None

    # Último estado conocido por grupo de civiles: cuenta y estado al final del run.
    civ_state: dict[str, tuple[int, str]] = {}
    # Última celda vista en cada estado: burnt al final = celda quemada.
    cell_state: dict[str, str] = {}

    # colgar → giro: t_sim de cada `call.ended` pendiente de emparejar con el
    # siguiente `action.requested`.
    pending_hangups: list[float] = []
    hangup_to_turn: list[float] = []

    for ev in read(path):
        if not run_id:
            run_id = ev.run_id

        if ev.type == EventType.RUN_STARTED:
            scenario_id = ev.payload.get("scenario_id", scenario_id)
        elif ev.type == EventType.RUN_ENDED:
            scenario_id = ev.payload.get("scenario_id", scenario_id)
            if ev.payload.get("score") is not None:
                replan_score = float(ev.payload["score"])
        elif ev.type == EventType.PLAN_REPLAN_STARTED:
            replans += 1
        elif ev.type == EventType.PLAN_POLICY_EMITTED:
            llm_calls += 1
        elif ev.type in (EventType.CALL_REQUESTED, EventType.CALL_STARTED):
            calls_placed += 1
        elif ev.type == EventType.WORLD_CIVILIANS_CHANGED:
            gid = ev.payload.get("group_id", "")
            civ_state[gid] = (
                int(ev.payload.get("count", 0)),
                str(ev.payload.get("state", "exposed")),
            )
        elif ev.type == EventType.WORLD_CELL_CHANGED:
            cell_state[ev.payload.get("cell_id", "")] = str(ev.payload.get("state", ""))
        elif ev.type == EventType.CALL_ENDED:
            pending_hangups.append(ev.t_sim)
        elif ev.type == EventType.ACTION_REQUESTED and pending_hangups:
            for t in pending_hangups:
                hangup_to_turn.append(ev.t_sim - t)
            pending_hangups.clear()

    civilians_safe = sum(c for c, s in civ_state.values() if s == "safe")
    civilians_exposed_end = sum(
        c for c, s in civ_state.values() if s in ("exposed", "trapped")
    )
    cells_burnt = sum(1 for s in cell_state.values() if s == "burnt")
    mean_htt = sum(hangup_to_turn) / len(hangup_to_turn) if hangup_to_turn else None

    total = (
        replan_score
        if replan_score is not None
        else (
            W_SAFE * civilians_safe
            + W_EXPOSED * civilians_exposed_end
            + W_BURNT * cells_burnt
            + W_LLM * llm_calls
        )
    )

    return RunScore(
        run_id=run_id,
        scenario_id=scenario_id,
        civilians_safe=civilians_safe,
        civilians_exposed_end=civilians_exposed_end,
        cells_burnt=cells_burnt,
        replans=replans,
        llm_calls=llm_calls,
        calls_placed=calls_placed,
        mean_hangup_to_turn_s=mean_htt,
        total=total,
    )


def compare(a: Path, b: Path) -> dict:
    """Run 1 vs run 12. Lo que pinta el panel de aprendizaje."""
    sa = score(a)
    sb = score(b)
    delta: dict[str, float] = {}
    for field, value in sb.model_dump().items():
        base = getattr(sa, field)
        if isinstance(value, (int, float)) and isinstance(base, (int, float)):
            delta[field] = value - base
    return {"a": sa.model_dump(), "b": sb.model_dump(), "delta": delta}
