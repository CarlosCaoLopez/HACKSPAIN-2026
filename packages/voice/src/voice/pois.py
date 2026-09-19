"""Resolución rápida de lo que dice el vecino contra el escenario. Sin red.

`voice` no importa de `core` ni de `sim` (invariante 4), así que la tabla de POIs
y carreteras se la da el gateway al arrancar con `set_scenario`, o se lee del
YAML si `pyyaml` está a mano. `fenic.semantic.join` (en `core.ingest`) es el
camino frío; esto es el camino caliente del tool, y tiene que tardar milisegundos.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from pathlib import Path

from contracts.world import POI, RoadEdge

_pois: dict[str, POI] = {}
_roads: dict[str, RoadEdge] = {}
_poi_aliases: dict[str, str] = {}  # texto normalizado → poi_id
_road_aliases: dict[str, str] = {}  # texto normalizado → edge_id

STOPWORDS = {"el", "la", "los", "las", "de", "del", "en", "al", "un", "una", "por"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split() if t not in STOPWORDS]


def set_scenario(
    pois: list[POI],
    roads: list[RoadEdge] | None = None,
    poi_aliases: dict[str, str] | None = None,
    road_aliases: dict[str, str] | None = None,
) -> None:
    """Lo llama el gateway al arrancar el run. Los alias son "molino viejo" →
    `poi_molino`, "pista sur" → `wp_sur_03-wp_sur_04`."""
    _pois.clear()
    _pois.update({p.id: p for p in pois})
    _roads.clear()
    _roads.update({r.id: r for r in roads or []})
    _poi_aliases.clear()
    _poi_aliases.update({normalize(k): v for k, v in (poi_aliases or {}).items()})
    _road_aliases.clear()
    _road_aliases.update({normalize(k): v for k, v in (road_aliases or {}).items()})


def load_scenario_yaml(path: Path | str) -> bool:
    """Carga POIs y carreteras del YAML si hay `pyyaml`. Devuelve False si no."""
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return False
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    pois = [POI.model_validate(p) for p in data.get("pois") or []]
    roads = [RoadEdge.model_validate(r) for r in data.get("roads") or []]
    set_scenario(
        pois,
        roads,
        poi_aliases=data.get("poi_aliases") or {},
        road_aliases=data.get("road_aliases") or {},
    )
    return True


def road_label(edge_id: str) -> str:
    """Cómo lo dice el agente: el primer alias en español de la arista, o genérico."""
    for alias, eid in _road_aliases.items():
        if eid == edge_id:
            return alias
    return "ese tramo"


def questions() -> dict:
    """El catálogo de preguntas de Jev sobre el escenario que el gateway ha cargado."""
    from contracts.questions import call_questions_for

    return call_questions_for(list(_pois.values()), list(_roads.values()))


def roads() -> dict[str, RoadEdge]:
    return dict(_roads)


def pois() -> dict[str, POI]:
    return dict(_pois)


def poi_name(poi_id: str | None) -> str | None:
    p = _pois.get(poi_id or "")
    return p.name if p else None


def resolve_poi_local(location_hint: str | None) -> str | None:
    """ "el molino viejo" → `poi_molino`. Alias exacto, luego nombre por fuzzy,
    luego solape de tokens. None no es fallo: es un hecho sin ubicar."""
    if not location_hint or not _pois:
        return None
    norm = normalize(location_hint)
    if norm in _poi_aliases:
        return _poi_aliases[norm]
    for alias, pid in _poi_aliases.items():
        if alias and alias in norm:
            return pid
    names = {normalize(p.name): p.id for p in _pois.values()}
    match = difflib.get_close_matches(norm, list(names), n=1, cutoff=0.6)
    if match:
        return names[match[0]]
    hint_tokens = set(_tokens(location_hint))
    best: tuple[int, str | None] = (0, None)
    for name, pid in names.items():
        overlap = len(hint_tokens & set(name.split()))
        if overlap > best[0]:
            best = (overlap, pid)
    return best[1]


def resolve_edge_local(road_hint: str | None) -> str | None:
    """ "la pista del sur" → `wp_sur_03-wp_sur_04`. Alias, luego tokens del hint
    que aparezcan en el id de la arista ("sur", "norte")."""
    if not road_hint or not _roads:
        return None
    if road_hint in _roads:  # ya es un id del escenario (fenic con Literal)
        return road_hint
    norm = normalize(road_hint)
    if norm in _road_aliases:
        return _road_aliases[norm]
    for alias, eid in _road_aliases.items():
        if alias and alias in norm:
            return eid
    hint_tokens = set(_tokens(road_hint))
    best: tuple[int, str | None] = (0, None)
    for eid in _roads:
        parts = set(re.split(r"[^a-z0-9]+", eid.lower()))
        overlap = len(hint_tokens & parts)
        if overlap > best[0]:
            best = (overlap, eid)
    return best[1]
