"""Nivel 1 · ingesta: texto sucio a hechos tipados con procedencia y confianza.

`fenic` como capa de construcción de contexto: `semantic.extract` con esquema
Pydantic, `semantic.classify` sin datos de entrenamiento, `semantic.join` para
casar texto sucio contra datos limpios. No determinista, pero acotado por esquema.

Aquí vive lo que el core necesita clasificar por su cuenta. La extracción de
transcripciones es de P3 (`voice`): ningún paquete la duplica. Todo lo de aquí es
camino frío (fin de llamada, lote sintético); si `fenic` no está, cae a heurísticas
deterministas para que la demo siga.
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata

from contracts.world import POI

log = logging.getLogger("core.ingest")

CRITICAL_WORDS = (
    "atrapad",
    "no puede",
    "no pueden",
    "herid",
    "fuego en casa",
    "humo dentro",
    "niñ",
)
MEDIUM_WORDS = ("cortad", "árbol", "arbol", "carretera", "pista", "humo", "cerca")
SIGNAL_WORDS = (
    "cortad",
    "no pued",
    "herid",
    "atrapad",
    "molino",
    "pista",
    "carretera",
    "personas",
)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


def resolve_poi(location_hint: str, pois: list[POI]) -> str | None:
    """`semantic.join` de "el molino viejo" contra la tabla de POIs.

    Devuelve el `poi_id` o None. None no es un fallo: es un hecho sin ubicar, y
    se muestra igual con su procedencia."""
    if not location_hint or not pois:
        return None
    joined = _fenic_join(location_hint, pois)
    if joined is not None:
        return joined
    names = {_norm(p.name): p.id for p in pois}
    match = difflib.get_close_matches(_norm(location_hint), list(names), n=1, cutoff=0.6)
    if match:
        return names[match[0]]
    hint = set(_norm(location_hint).split())
    best = max(names.items(), key=lambda kv: len(hint & set(kv[0].split())), default=None)
    if best and len(hint & set(best[0].split())) > 0:
        return best[1]
    return None


def classify_severity(text: str) -> str:
    """`semantic.classify` sobre low | medium | critical."""
    label = _fenic_classify(text)
    if label is not None:
        return label
    t = _norm(text)
    if any(w in t for w in CRITICAL_WORDS):
        return "critical"
    if any(w in t for w in MEDIUM_WORDS):
        return "medium"
    return "low"


def rank_signal(texts: list[str]) -> list[tuple[str, float]]:
    """Llegan cien mensajes y solo tres cambian algo. Esto ordena el lote de
    llamadas sintéticas por cuánto mueven el plan."""
    scored: list[tuple[str, float]] = []
    for text in texts:
        t = _norm(text)
        hits = sum(1 for w in SIGNAL_WORDS if w in t)
        sev = {"critical": 1.0, "medium": 0.5, "low": 0.0}[classify_severity(text)]
        scored.append((text, round(min(1.0, 0.15 * hits + 0.5 * sev), 3)))
    return sorted(scored, key=lambda kv: kv[1], reverse=True)


# --- fenic, si está --------------------------------------------------------------


def _session():
    try:
        import fenic as fc  # type: ignore[import-not-found]
    except ImportError:
        return None, None
    try:
        return fc, fc.Session.get_or_create(fc.SessionConfig(app_name="vela"))
    except Exception as exc:  # noqa: BLE001
        log.warning("fenic sin sesión: %s", exc)
        return None, None


def _fenic_join(location_hint: str, pois: list[POI]) -> str | None:
    fc, session = _session()
    if fc is None:
        return None
    try:
        left = session.create_dataframe([{"hint": location_hint}])
        right = session.create_dataframe([{"poi_id": p.id, "name": p.name} for p in pois])
        rows = left.semantic.join(
            right,
            "El lugar {{left_on}} se refiere al punto de interés {{right_on}}",
            left_on=fc.col("hint"),
            right_on=fc.col("name"),
        ).to_pylist()
        return str(rows[0]["poi_id"]) if rows else None
    except Exception as exc:  # noqa: BLE001
        log.warning("semantic.join: %s", exc)
        return None


def _fenic_classify(text: str) -> str | None:
    fc, session = _session()
    if fc is None:
        return None
    try:
        df = session.create_dataframe([{"t": text}])
        rows = df.select(
            fc.semantic.classify(fc.col("t"), ["low", "medium", "critical"]).alias("s")
        ).to_pylist()
        label = rows[0]["s"] if rows else None
        return str(label) if label in ("low", "medium", "critical") else None
    except Exception as exc:  # noqa: BLE001
        log.warning("semantic.classify: %s", exc)
        return None
