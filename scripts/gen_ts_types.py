"""Pydantic → TypeScript. `apps/dashboard/src/types.ts` se genera, nunca se escribe.

`make types` antes de cada push del dashboard.

Implementa SPEC-001 (docs-nacho/specs/001-toolchain-y-tipos.md).

Se introspecciona `model_fields` en vez de `model_json_schema()`: el JSON Schema de
Pydantic aplana los `Literal` a `enum` anónimos dentro de `$defs` -- justo lo que
REQ-004 prohíbe -- y convierte un `dict` sin parametrizar en `{}`.
"""

from __future__ import annotations

import datetime
import enum
import sys
import types
import typing
from pathlib import Path

from pydantic import BaseModel

import contracts
from contracts import calls, events, world
from contracts.events import PAYLOAD_MODELS, Event, EventType

OUT = Path(__file__).resolve().parents[1] / "apps" / "dashboard" / "src" / "types.ts"

HEADER = "// GENERADO por scripts/gen_ts_types.py desde contracts. No editar a mano.\n"

NONE_TYPE = type(None)

PRIMITIVES: dict[type, str] = {
    str: "string",
    int: "number",
    float: "number",
    bool: "boolean",
    datetime.datetime: "string",  # ISO-8601 por el sobre del bus
    datetime.date: "string",
}

# Los alias `Literal` de contracts, EXPLÍCITOS Y ORDENADOS. Primera coincidencia gana.
#
# No se autodetectan escaneando los módulos, y no es por pereza: `typing` cachea los
# `Literal`, así que `calls.Urgency is calls.Severity` es True -- los dos son
# Literal["low","medium","critical"] y en runtime son EL MISMO OBJETO. Escanear haría
# que el nombre emitido dependiera del orden de importación, rompiendo el determinismo
# de REQ-007 en silencio. Los duplicados se emiten como alias del canónico (ver
# `emit_aliases`), de forma que ambos nombres existen en TS con una sola fuente.
ALIASES: list[tuple[str, object]] = [
    ("UnitStatus", world.UnitStatus),
    ("UnitKind", world.UnitKind),
    ("CellState", world.CellState),
    ("CivState", world.CivState),
    ("POIKind", world.POIKind),
    ("TaskKind", world.TaskKind),
    ("TaskSeverity", world.TaskSeverity),
    ("Verb", events.Verb),
    ("OverrideKind", events.OverrideKind),
    ("Audience", calls.Audience),
    ("CallIntent", calls.CallIntent),
    ("CallOutcome", calls.CallOutcome),
    ("Urgency", calls.Urgency),
    ("Severity", calls.Severity),  # idéntico a Urgency: sale como `= Urgency`
]


class UnsupportedType(Exception):
    """REQ-008: nunca se emite `any` en silencio."""

    def __init__(self, model: str, field: str, ann: object) -> None:
        super().__init__(
            f"{model}.{field}: no sé traducir {ann!r} a TypeScript.\n"
            f"Añade el caso a ts_type() en scripts/gen_ts_types.py."
        )


# --- Alias -----------------------------------------------------------------


def _alias_index() -> dict[int, str]:
    """`id()` del objeto Literal → primer nombre del catálogo. Los duplicados
    (Urgency/Severity) caen en la misma entrada, que es exactamente lo que queremos."""
    index: dict[int, str] = {}
    for name, alias in ALIASES:
        index.setdefault(id(alias), name)
    return index


ALIAS_BY_ID = _alias_index()


def alias_for(ann: object) -> str | None:
    return ALIAS_BY_ID.get(id(ann))


# --- Mapeo de tipos --------------------------------------------------------


def _literal_union(args: tuple[object, ...]) -> str:
    return " | ".join(f"'{a}'" for a in args)


def ts_type(ann: object, model: str, field: str) -> str:
    """Un tipo Python a su equivalente TypeScript. Ocho casos y nada más (REQ-005)."""
    named = alias_for(ann)
    if named is not None:
        return named

    origin = typing.get_origin(ann)
    args = typing.get_args(ann)

    if origin is typing.Literal:
        return _literal_union(args)

    if origin in (typing.Union, types.UnionType):
        parts = [ts_type(a, model, field) for a in args if a is not NONE_TYPE]
        return " | ".join(dict.fromkeys(parts))  # dedup conservando orden

    if origin in (list, set, frozenset):
        inner = ts_type(args[0], model, field) if args else "unknown"
        return f"{inner}[]" if " | " not in inner else f"({inner})[]"

    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return f"{ts_type(args[0], model, field)}[]"
        return "[" + ", ".join(ts_type(a, model, field) for a in args) + "]"

    if origin is dict:
        value = ts_type(args[1], model, field) if len(args) == 2 else "unknown"
        return f"Record<string, {value}>"

    if ann is dict:
        return "Record<string, unknown>"
    if ann is list:
        return "unknown[]"

    if isinstance(ann, type):
        if issubclass(ann, BaseModel):
            return ann.__name__
        if issubclass(ann, enum.Enum):
            return ann.__name__
        if ann in PRIMITIVES:
            return PRIMITIVES[ann]

    raise UnsupportedType(model, field, ann)


def admits_none(ann: object) -> bool:
    """REQ-006: el `?` lo decide la anotación, NO `FieldInfo.is_required()`.

    `Unit.status` tiene default, así que `is_required()` es False, y aun así viaja
    siempre en el JSON: en TypeScript es obligatorio.
    """
    if typing.get_origin(ann) in (typing.Union, types.UnionType):
        return any(a is NONE_TYPE for a in typing.get_args(ann))
    return False


# --- Recolección -----------------------------------------------------------


def _referenced(model: type[BaseModel]) -> list[type[BaseModel]]:
    """Modelos que aparecen en las anotaciones de `model`, en orden de campo."""
    found: list[type[BaseModel]] = []
    for field in model.model_fields.values():
        stack: list[object] = [field.annotation]
        while stack:
            ann = stack.pop(0)
            args = typing.get_args(ann)
            if args:
                stack = list(args) + stack
                continue
            if isinstance(ann, type) and issubclass(ann, BaseModel) and ann not in found:
                found.append(ann)
    return found


def collect_models() -> list[type]:
    """Todos los BaseModel públicos de `contracts`, en orden de dependencia.

    Raíces (REQ-002): `PAYLOAD_MODELS` -- el registro autoritativo del catálogo, que
    ya existe en contracts y evita listas copiadas a mano -- más el `__all__` del
    paquete, más `Event`.
    """
    roots: list[type[BaseModel]] = [Event]
    for name in contracts.__all__:
        obj = getattr(contracts, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel):
            roots.append(obj)
    roots.extend(PAYLOAD_MODELS[t] for t in EventType if t in PAYLOAD_MODELS)

    # Orden topológico: un tipo se emite después de los que usa. Desempate
    # alfabético y recorrido DFS estable, para que REQ-007 se cumpla siempre.
    out: list[type[BaseModel]] = []
    done: set[type[BaseModel]] = set()
    visiting: set[type[BaseModel]] = set()

    def visit(model: type[BaseModel]) -> None:
        if model in done or model in visiting:
            return  # un ciclo no es problema en TS: las interfaces se refieren igual
        visiting.add(model)
        for dep in sorted(_referenced(model), key=lambda c: c.__name__):
            visit(dep)
        visiting.discard(model)
        done.add(model)
        out.append(model)

    seen_roots: set[type[BaseModel]] = set()
    for root in sorted(set(roots), key=lambda c: c.__name__):
        if root not in seen_roots:
            seen_roots.add(root)
            visit(root)
    return list(out)


# --- Emisión ---------------------------------------------------------------


def _doc(model: type[BaseModel]) -> str:
    """El docstring en una línea. Cortar por el primer punto no vale: hay
    docstrings con `fenic.semantic.extract` dentro."""
    text = " ".join((model.__doc__ or "").split())
    if not text:
        return ""
    if len(text) > 100:
        text = text[:100].rsplit(" ", 1)[0] + "…"
    return f"/** {text} */\n"


def emit_aliases() -> str:
    """Los alias `Literal` con nombre. Los duplicados apuntan al canónico."""
    lines = ["// --- Alias del catálogo de contracts ---", ""]
    emitted: dict[int, str] = {}
    for name, alias in ALIASES:
        args = typing.get_args(alias)
        canonical = emitted.get(id(alias))
        if canonical is not None:
            lines.append(f"export type {name} = {canonical} // idéntico a {canonical}")
        else:
            emitted[id(alias)] = name
            lines.append(f"export type {name} = {_literal_union(args)}")
    return "\n".join(lines) + "\n"


def emit_event_type() -> str:
    """REQ-003: el enum completo, no `string`."""
    lines = ["export type EventType ="]
    lines += [f"  | '{member.value}'" for member in EventType]
    return "\n".join(lines) + "\n"


def emit_interface(model: type[BaseModel], skip: tuple[str, ...] = ()) -> str:
    name = model.__name__
    lines = [f"{_doc(model)}export interface {name} {{"]
    unnamed: list[str] = []
    for field_name, field in model.model_fields.items():
        if field_name in skip:
            continue
        ann = field.annotation
        rendered = ts_type(ann, name, field_name)
        if typing.get_origin(ann) is typing.Literal and alias_for(ann) is None:
            unnamed.append(f"{name}.{field_name}")
        if admits_none(ann):
            lines.append(f"  {field_name}?: {rendered} | null")
        else:
            lines.append(f"  {field_name}: {rendered}")
    lines.append("}")
    for where in unnamed:
        print(f"  aviso: literal sin alias en {where}, emitido inline", file=sys.stderr)
    return "\n".join(lines) + "\n"


def emit_vela_event() -> str:
    """REQ-019: unión discriminada por `type`.

    Con esto, un `switch (ev.type)` estrecha el payload solo y TypeScript obliga a
    cubrir los 26 casos. Sin esto, cada panel acaba haciendo casts a mano.
    """
    body = emit_interface(Event, skip=("type", "payload"))
    body = body[body.index("export interface") :]  # sin el docstring de Event
    lines = ["/** El sobre sin `type` ni `payload`: la base de la unión. */"]
    lines.append(body.replace("interface Event {", "interface EventBase {").rstrip("\n"))
    lines.append("")
    lines.append("/** El sobre con el payload ya estrechado por `type`. */")
    lines.append("export type VelaEvent =")
    for member in EventType:
        payload = PAYLOAD_MODELS.get(member)
        rendered = payload.__name__ if payload is not None else "Record<string, unknown>"
        lines.append(
            f"  | (EventBase & {{ type: '{member.value}'; payload: {rendered} }})"
        )
    return "\n".join(lines) + "\n"


def emit(models: list[type]) -> str:
    """Interfaces TypeScript. Los `Literal` de Pydantic salen como uniones de
    strings; `dict[str, X]` como `Record<string, X>`."""
    blocks = [HEADER.rstrip("\n"), "", emit_aliases(), emit_event_type()]
    blocks.append("// --- Modelos ---\n")
    for model in models:
        if not issubclass(model, BaseModel):
            continue
        blocks.append(emit_interface(model))
    blocks.append("// --- El sobre y su unión discriminada ---\n")
    blocks.append(emit_vela_event())
    return "\n".join(blocks)


def main() -> None:
    models = collect_models()
    source = emit(models)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(source, encoding="utf-8", newline="\n")
    print(
        f"{OUT.relative_to(Path(__file__).resolve().parents[1])}: {len(models)} modelos"
    )


if __name__ == "__main__":
    main()
