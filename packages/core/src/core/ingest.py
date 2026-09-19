"""Nivel 1 · ingesta, camino frío.

La percepción en llamada ya no vive aquí ni usa `fenic`: es TypeSafe `jev-1.13` y está
en `voice` (`voice.jev`, `voice.perception`), que elige entre opciones cerradas del
escenario. El `semantic.join` de POIs desapareció: ya es el `Choice`.

Lo que queda es lo que el core necesita clasificar por su cuenta y sin Jev: severidad
de un texto y orden de un lote. `fenic` (`semantic.classify`) como capa de contexto en
el camino frío; si no está, heurísticas deterministas para que la demo siga.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata

from contracts.settings import settings

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


FENIC_OPENAI_MODEL = "gpt-5.6-luna"
FENIC_ANTHROPIC_MODEL = "claude-haiku-4-5"
"""Camino frío (fin de llamada, sintéticas). OpenAI con gpt-5.6-luna si hay
OPENAI_API_KEY; si no, Anthropic. fenic lee la key del entorno; `settings` la carga
del .env."""


_fenic_failed: str | None = None
"""Si la sesión de fenic falló una vez (sin key, key inválida), no se reintenta en
cada llamada: se recuerda el motivo y se cae a las heurísticas en silencio."""


def _session():
    """Sesión de fenic con Anthropic como modelo por defecto, o (None, None) si no
    hay `fenic` o no hay API key. Nunca lanza."""
    os.environ.setdefault(
        "TQDM_DISABLE", "1"
    )  # antes de importar: fenic pinta barras en el log
    try:
        import fenic as fc
    except ImportError:
        return None, None
    global _fenic_failed
    if _fenic_failed is not None:
        return None, None
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
        model = fc.OpenAILanguageModel(
            model_name=FENIC_OPENAI_MODEL, rpm=100, tpm=100_000
        )
    elif settings.anthropic_api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
        model = fc.AnthropicLanguageModel(
            model_name=FENIC_ANTHROPIC_MODEL,
            rpm=100,
            input_tpm=100_000,
            output_tpm=20_000,
        )
    else:
        return None, None
    try:
        config = fc.SessionConfig(
            app_name="vela",
            semantic=fc.SemanticConfig(
                language_models={"llm": model}, default_language_model="llm"
            ),
        )
        return fc, fc.Session.get_or_create(config)
    except Exception as exc:  # noqa: BLE001
        _fenic_failed = str(exc)[:200]
        log.warning("fenic sin sesión (no se reintenta): %s", _fenic_failed)
        return None, None


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
