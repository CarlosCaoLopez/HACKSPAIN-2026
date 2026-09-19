"""La puntuación provisional de un run. P4. **Se apaga sola.**

`journal/score.py` es de P1 y hoy no tiene cuerpo, así que `GET /api/runs` devuelve
`score: null` y el run 1 vs run 12 —criterio explícito de puntos extra— no se puede ni
construir. Esto cuenta lo que se puede contar de un journal mientras tanto.

Se apaga sola, con dos reglas:

- **Solo se rellena lo que falta.** En cuanto `journal.score` responda, esto no se llama.
- **Lo que se ha contado aquí se dice.** `provisional: true` viaja hasta la pantalla y
  `total` sale vacío: la fórmula de la puntuación es de P1 y un número inventado justo
  donde el jurado mira el marcador es lo que no se hace.

`RunScore.total` está tipado `float` y no admite `None`, así que el vacío se pone al
serializar (`as_json`) y no en el modelo: hacerlo obligatorio-anulable sería cambiar un
tipo de `contracts`, que necesita a los cuatro.

La otra mitad del fichero es `HangupToTurn`, que resuelve **el número del pitch**: de
colgar a girar. Vive aquí porque es la métrica, y la usan dos: este contador sobre un
journal cerrado y `scripts/demo.py` sobre el chorro en vivo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from contracts.events import Event, EventType

if TYPE_CHECKING:  # `RunScore` es de P1 y se importa tarde, nunca al cargar el módulo:
    from journal.score import RunScore  # un paquete que no importa no tumba el gateway.

log = logging.getLogger("vela.score")


# --- El número del pitch ---------------------------------------------------------


class HangupToTurn:
    """De `call.ended` al primer `action.requested` que descienda de ella.

    **En `t_wall`, no en `t_sim`.** Es el único número de todo el dashboard en tiempo de
    pared, y es a propósito: «colgar → giro por debajo de 3 s» es una promesa sobre el
    reloj de la sala, no sobre el tiempo del dominio. Medirlo en `t_sim` daría un número
    bonito que no significa nada.

    El emparejamiento va por la **cadena de `causes`**, nunca por cercanía temporal: dos
    llamadas seguidas y una acción en medio se emparejarían mal, y el número del pitch
    saldría mejor de lo que es. Si la cadena no llega, el hueco se queda sin resolver y
    se dice.

    Se le da de comer evento a evento, en orden de `seq`, y por eso sirve igual para un
    fichero que para el WS en directo.
    """

    def __init__(self) -> None:
        self.hung_up: dict[int, datetime] = {}  # seq de call.ended → su t_wall
        self.ancestors: dict[int, set[int]] = {}  # seq → seqs de colgado de los que nace
        self.turns: list[tuple[int, float]] = []  # (seq del colgado, segundos)
        self.resolved: set[int] = set()

    def feed(self, ev: Event) -> float | None:
        """Devuelve los segundos si este evento cierra un colgar → giro, y si no `None`."""
        # Los antepasados de un evento son los de sus causas, más las causas mismas. Con
        # esto la cadena llamada → hecho → violación → replan → orden se recorre entera
        # sin guardar el journal completo en memoria.
        mine: set[int] = set()
        for c in ev.causes:
            mine.add(c)
            mine |= self.ancestors.get(c, set())
        self.ancestors[ev.seq] = mine

        if ev.type == EventType.CALL_ENDED:
            self.hung_up[ev.seq] = ev.t_wall
            return None

        if ev.type != EventType.ACTION_REQUESTED:
            return None

        # La primera orden que desciende de ese colgado, y solo la primera: las demás son
        # el resto del plan, no el giro.
        for seq in sorted(mine & self.hung_up.keys()):
            if seq in self.resolved:
                continue
            self.resolved.add(seq)
            delta = (ev.t_wall - self.hung_up[seq]).total_seconds()
            self.turns.append((seq, delta))
            return delta
        return None

    @property
    def mean_s(self) -> float | None:
        """Ninguna llamada resuelta es `None`, no `0.0`: un cero se lee como instantáneo."""
        if not self.turns:
            return None
        return sum(d for _, d in self.turns) / len(self.turns)

    @property
    def unresolved(self) -> int:
        return len(self.hung_up) - len(self.resolved)


# --- El contador -----------------------------------------------------------------


@dataclass
class Counted:
    """Lo contado de un journal, con las dos advertencias pegadas al dato."""

    score: RunScore
    incomplete: bool = False  # no termina en `run.ended`: todo Ctrl-C deja uno así
    notes: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        data = self.score.model_dump(mode="json")
        # `total` vacío a propósito: la fórmula es de P1. Ver el docstring del módulo.
        data["total"] = None
        return data


def count(path: Path) -> Counted:
    """Cuenta un journal. Cacheado por `(mtime, size)`: `/api/runs` con doce journals se
    pide en mitad del pitch y no puede recorrerlos otra vez en cada petición."""
    try:
        stat = path.stat()
    except OSError as exc:
        return Counted(_empty(path), incomplete=True, notes=[f"no se puede leer: {exc}"])

    key = (stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.get(path)
    if cached is not None and cached[0] == key:
        return cached[1]

    counted = _count_uncached(path)
    _CACHE[path] = (key, counted)
    return counted


_CACHE: dict[Path, tuple[tuple[int, int], Counted]] = {}


def _empty(path: Path) -> RunScore:
    """El molde vacío. Si `journal` no es importable, esto lanza `ImportError` y quien
    llama lo anota: `/api/runs` ya sabe degradar a `score: null`."""
    from journal.score import RunScore

    return RunScore(run_id=path.stem, scenario_id="")


def _count_uncached(path: Path) -> Counted:
    score = _empty(path)
    notes: list[str] = []
    ended = False
    unreadable = 0

    cell_state: dict[str, str] = {}
    civ_count: dict[str, int] = {}
    civ_state: dict[str, str] = {}
    turns = HangupToTurn()

    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            ev = Event.model_validate_json(line)
        except Exception:  # noqa: BLE001 — un journal a medias es lo normal, no un fallo
            unreadable += 1
            log.debug("score: línea %s de %s ilegible", n, path)
            continue

        turns.feed(ev)
        score.run_id = ev.run_id

        match ev.type:
            case EventType.RUN_STARTED:
                score.scenario_id = str(ev.payload.get("scenario_id", ""))
            case EventType.RUN_ENDED:
                ended = True
                score.scenario_id = str(ev.payload.get("scenario_id", score.scenario_id))
            case EventType.PLAN_REPLAN_STARTED:
                score.replans += 1
            case EventType.PLAN_POLICY_EMITTED:
                # Una política es una llamada al modelo de razonamiento: la invariante 7
                # dice que hay exactamente una por replan que se levante.
                score.llm_calls += 1
            case EventType.CALL_STARTED:
                score.calls_placed += 1
            case EventType.WORLD_CELL_CHANGED:
                cell_state[str(ev.payload.get("cell_id"))] = str(ev.payload.get("state"))
            case EventType.WORLD_CIVILIANS_CHANGED:
                group = str(ev.payload.get("group_id"))
                civ_count[group] = int(ev.payload.get("count", 0))
                civ_state[group] = str(ev.payload.get("state"))
            case _:
                pass

    # El estado final de cada celda y cada grupo, no cuántas veces cambiaron.
    score.cells_burnt = sum(1 for state in cell_state.values() if state == "burnt")
    score.civilians_safe = sum(
        civ_count[g] for g, state in civ_state.items() if state == "safe"
    )
    score.civilians_exposed_end = sum(
        civ_count[g] for g, state in civ_state.items() if state in ("exposed", "trapped")
    )
    score.mean_hangup_to_turn_s = turns.mean_s

    if turns.unresolved:
        notes.append(
            f"{turns.unresolved} llamada(s) sin orden que descienda de ellas por `causes`"
        )
    if unreadable:
        notes.append(f"{unreadable} línea(s) ilegibles, saltadas")
    if not ended:
        notes.append("el journal no termina en run.ended")

    return Counted(score, incomplete=not ended, notes=notes)
