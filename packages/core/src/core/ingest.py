"""Nivel 1 · ingesta: texto sucio a hechos tipados con procedencia y confianza.

`fenic` como capa de construcción de contexto: `semantic.extract` con esquema
Pydantic, `semantic.classify` sin datos de entrenamiento, `semantic.join` para
casar texto sucio contra datos limpios. No determinista, pero acotado por esquema.

Aquí vive lo que el core necesita clasificar por su cuenta. La extracción de
transcripciones es de P3 (`voice`): ningún paquete la duplica.
"""

from contracts.world import POI


def resolve_poi(location_hint: str, pois: list[POI]) -> str | None:
    """`semantic.join` de "el molino viejo" contra la tabla de POIs.

    Devuelve el `poi_id` o None. None no es un fallo: es un hecho sin ubicar, y
    se muestra igual con su procedencia."""
    raise NotImplementedError


def classify_severity(text: str) -> str:
    """`semantic.classify` sobre low | medium | critical."""
    raise NotImplementedError


def rank_signal(texts: list[str]) -> list[tuple[str, float]]:
    """Llegan cien mensajes y solo tres cambian algo. Esto ordena el lote de
    llamadas sintéticas por cuánto mueven el plan."""
    raise NotImplementedError
