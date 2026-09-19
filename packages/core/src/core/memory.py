"""El aprendizaje entre ejecuciones. El bonus. Bucle estilo Hermes Agent.

Cada regla es un fichero individual en `memory/rules/<slug>.md` con frontmatter,
no un blob que se inyecta siempre. El planner solo ve las reglas cuyo `trigger`
casa con el `WorldState` actual (progressive disclosure), resumidas a una frase:
prompt corto, latencia baja, y el jurado puede ver *qué* regla influyó en *qué*
decisión (lineage → dashboard).

Tres piezas:
- `harvest`  — batch entre runs: journals + `fenic` → reglas candidatas.
- `select`   — gating determinista por `trigger` contra el estado (nunca el LLM).
- `apply_patch` — mejora por patch: sube/baja `support`/`confidence`, no reescribe.

Run 1 contra run 12 en pantalla partida con la puntuación de cada uno.
"""

from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

from contracts.world import WorldState
from journal import replay
from journal.score import RunScore

MIN_SUPPORT = 2
"""Una regla vista en un solo run es ruido: no se persiste."""

RULES_DIR = Path("memory/rules")
"""Sustituye al antiguo `memory/policy_rules.md`. Una regla, un fichero."""

_CONF_STEP = 0.3
"""Cuánto se mueve `confidence` hacia el objetivo en cada patch (EWMA)."""

_OPS: dict[str, object] = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}
"""El mini-DSL de triggers: comparaciones simples. Nada de `eval`. Empezar simple
es explícito en el diseño; un DSL elaborado es tiempo que no tenemos el sábado."""


class Rule(BaseModel):
    """Una regla aprendida. Vive en `core` (no cruza el bus) y se serializa a
    `memory/rules/<slug>.md`: frontmatter con metadata + cuerpo con la frase que
    se inyecta al planner."""

    slug: str
    trigger: str  # "wind_shift_deg > 60", evaluable contra features(WorldState)
    support: int = 0  # nº de runs que la respaldan
    confidence: float = 0.0
    lineage: list[str] = []  # ["run_a7:seq_412", ...]
    body: str = ""  # la frase que ve el planner


class LearnedRule(BaseModel):
    """El esquema que consume `fenic.semantic.extract` sobre los journals pasados.
    Una fila por run: patrón candidato con su evidencia (`run:seq`)."""

    trigger: str  # condición evaluable, p.ej. "wind_shift_deg > 60"
    body: str  # qué hacer, en una frase
    evidence: str = ""  # "run_a7:seq_412"


# --- Trigger DSL: gating determinista --------------------------------------


def eval_trigger(trigger: str, feats: dict[str, float]) -> bool | None:
    """Evalúa `"<feature> <op> <number>"` contra el diccionario de features.

    `None` = no evaluable (feature ausente o sintaxis inválida): la regla se salta,
    espejo de `divergence.evaluate_assumption`. No infla falsos positivos ni tumba
    el proceso con un trigger malformado."""
    for op, fn in _OPS.items():  # dict ordenado: ">=" antes que ">"
        left, sep, right = trigger.partition(op)
        if not sep:
            continue
        feature = left.strip()
        if feature not in feats:
            return None
        try:
            threshold = float(right.strip())
        except ValueError:
            return None
        return bool(fn(feats[feature], threshold))  # type: ignore[operator]
    return None


def features(state: WorldState) -> dict[str, float]:
    """Escalares derivables del `WorldState` para evaluar triggers. Solo lee.

    `wind_shift_deg` no está aquí: necesita el rumbo de referencia del plan, que
    `select` no ve. El bucle lo inyecta vía `extra`."""
    return {
        "immobile_total": float(sum(c.immobile for c in state.civilians.values())),
        "exposed_total": float(
            sum(c.count for c in state.civilians.values() if c.state == "exposed")
        ),
        "trapped_total": float(
            sum(c.count for c in state.civilians.values() if c.state == "trapped")
        ),
        "cells_burning": float(
            sum(1 for c in state.cells.values() if c.state == "burning")
        ),
        "cells_at_risk": float(
            sum(1 for c in state.cells.values() if c.state == "at_risk")
        ),
        "wind_speed": float(state.wind.speed),
        "wind_bearing_deg": float(state.wind.bearing_deg),
        "open_tasks": float(sum(1 for t in state.tasks.values() if not t.done)),
        "units_idle": float(sum(1 for u in state.units.values() if u.status == "idle")),
    }


def select(
    state: WorldState,
    rules: list[Rule],
    extra: dict[str, float] | None = None,
) -> list[Rule]:
    """Gating por trigger. Devuelve solo las reglas cuyo `trigger` casa con el
    estado actual. Determinista: si esto dependiera del LLM, perdemos la
    explicabilidad.

    `extra` añade features que no salen del estado por sí solo (p.ej.
    `wind_shift_deg`, que el bucle calcula contra el plan vigente)."""
    feats = features(state)
    if extra:
        feats = {**feats, **extra}
    return [r for r in rules if eval_trigger(r.trigger, feats) is True]


def render(rules: list[Rule]) -> str:
    """Reglas seleccionadas → texto para el placeholder `<<RULES>>` del prompt.
    Una frase por línea; corto a propósito."""
    if not rules:
        return ""
    return "\n".join(f"- {r.body} (confianza {r.confidence:.0%})" for r in rules)


# --- Mejora por patch ------------------------------------------------------


def apply_patch(rule: Rule, outcome: RunScore) -> Rule:
    """Un run posterior confirma o contradice la regla: ajusta `support` y
    `confidence`. Edición dirigida, no reescritura — el `body` no se toca.

    Objetivo del EWMA: 1.0 si el run acabó sin civiles expuestos (la regla
    ayudó), 0.0 si no."""
    good = outcome.civilians_exposed_end == 0
    target = 1.0 if good else 0.0
    confidence = round(rule.confidence + _CONF_STEP * (target - rule.confidence), 3)
    return rule.model_copy(update={"support": rule.support + 1, "confidence": confidence})


# --- I/O de ficheros: frontmatter minimal ----------------------------------


def _serialize(rule: Rule) -> str:
    lineage = "[" + ", ".join(rule.lineage) + "]"
    return (
        "---\n"
        f"slug: {rule.slug}\n"
        f'trigger: "{rule.trigger}"\n'
        f"support: {rule.support}\n"
        f"confidence: {rule.confidence}\n"
        f"lineage: {lineage}\n"
        "---\n"
        f"{rule.body}\n"
    )


def _parse(text: str) -> Rule:
    """Parsea un `<slug>.md`. Formato controlado por nosotros (`_serialize`),
    así que el parser es minimal y no necesita PyYAML."""
    _, _, rest = text.partition("---\n")
    front, _, body = rest.partition("\n---\n")
    fields: dict[str, str] = {}
    for line in front.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()

    lineage_raw = fields.get("lineage", "[]").strip().lstrip("[").rstrip("]")
    lineage = [s.strip() for s in lineage_raw.split(",") if s.strip()]

    return Rule(
        slug=fields.get("slug", ""),
        trigger=fields.get("trigger", "").strip().strip('"'),
        support=int(fields.get("support", "0")),
        confidence=float(fields.get("confidence", "0")),
        lineage=lineage,
        body=body.strip(),
    )


def load_rules(rules_dir: Path = RULES_DIR) -> list[Rule]:
    """Todas las reglas del directorio, ordenadas por slug. Directorio ausente →
    lista vacía: el run 1 no tiene memoria y no pasa nada."""
    if not rules_dir.is_dir():
        return []
    return [_parse(p.read_text()) for p in sorted(rules_dir.glob("*.md"))]


def write_rule(rule: Rule, rules_dir: Path = RULES_DIR) -> Path:
    """Escribe (o sobrescribe) `memory/rules/<slug>.md`."""
    rules_dir.mkdir(parents=True, exist_ok=True)
    path = rules_dir / f"{rule.slug}.md"
    path.write_text(_serialize(rule))
    return path


# --- Cosecha: batch entre runs ---------------------------------------------


def _slug(text: str) -> str:
    """Kebab-case ASCII simple desde una frase."""
    out = [c if c.isalnum() else "-" for c in text.lower()]
    return "-".join("".join(out).split("-"))[:60].strip("-")


def harvest(runs_dir: Path) -> list[Rule]:
    """Carga `runs_dir/*.jsonl` en `fenic`, extrae patrones con `semantic.extract`
    sobre `LearnedRule`, y agrega por (trigger, body). Solo conserva reglas con
    soporte en `>= MIN_SUPPORT` runs distintos.

    Lento a propósito: corre entre runs, no en el bucle. Depende de
    `journal.replay` (task 7); `fenic` usa `ANTHROPIC_API_KEY`, mismo motor que la
    ingesta de P3."""
    import fenic

    session = fenic.Session.get_or_create(fenic.SessionConfig(app_name="vela-memory"))

    rows: list[dict] = []
    for path in sorted(runs_dir.glob("*.jsonl")):
        events = list(replay.read(path))
        if not events:
            continue
        run_id = events[0].run_id
        transcript = "\n".join(
            f"seq={e.seq} t_sim={e.t_sim:.0f} {e.type.value} {e.payload}" for e in events
        )
        rows.append({"run_id": run_id, "transcript": transcript})

    if not rows:
        return []

    df = session.create_dataframe(rows)
    extracted = df.select(
        fenic.col("run_id"),
        fenic.semantic.extract(fenic.col("transcript"), LearnedRule).alias("rule"),
    ).to_pylist()

    # Agregación: soporte = nº de runs distintos que respaldan (trigger, body).
    by_key: dict[tuple[str, str], set[str]] = defaultdict(set)
    lineage: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in extracted:
        rule = row.get("rule")
        if not rule or not rule.get("trigger") or not rule.get("body"):
            continue
        key = (rule["trigger"].strip(), rule["body"].strip())
        by_key[key].add(row["run_id"])
        evidence = rule.get("evidence") or row["run_id"]
        lineage[key].append(evidence)

    rules: list[Rule] = []
    for (trigger, body), runs in by_key.items():
        if len(runs) < MIN_SUPPORT:
            continue
        rules.append(
            Rule(
                slug=_slug(body),
                trigger=trigger,
                support=len(runs),
                confidence=min(1.0, len(runs) / (MIN_SUPPORT + 1)),
                lineage=lineage[(trigger, body)],
                body=body,
            )
        )
    return rules
