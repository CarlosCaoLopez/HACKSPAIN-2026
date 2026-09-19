"""Capturas crudas de las fuentes. SPEC-007 · REQ-259.

Cada respuesta que llega en `live` se guarda tal cual en
`<VELA_FEEDS_DIR>/<ancla>/<fuente>/<t_wall>.<ext>`. Sirve para dos cosas:

- **auditar un `source`**: `api:dgt:5684393v1` se rastrea hasta el XML del que salió;
- **`recorded`**: reproducir un día real sin red, con el mismo `to_facts`.

Solo se **añade**, como todo `fixtures/**`: un fichero que ya existe no se pisa.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

log = logging.getLogger("vela.feeds")


def censor(text: str, secrets: Iterable[str]) -> str:
    """Quita las claves de un texto antes de loguearlo o guardarlo.

    Un `httpx.HTTPStatusError` lleva la URL entera en su mensaje, y la URL de FIRMS lleva
    la `MAP_KEY` dentro. Sin esto, un 500 pone la clave en el log.
    """
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def save_raw(base: Path, anchor_id: str, feed: str, ext: str, data: bytes, t_wall: datetime) -> Path | None:
    """Guarda una captura. `None` si ya existía (no se pisa) o si el disco falla.

    Un fallo al escribir no puede tumbar el ciclo: la fuente funcionó, solo se pierde la
    auditoría, y eso se anota.
    """
    folder = base / anchor_id / feed
    # `:` no es válido en un nombre de fichero de Windows: hora compacta, siempre en UTC.
    path = folder / f"{t_wall:%Y-%m-%dT%H%M%SZ}{ext}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as fh:
            fh.write(data)
    except FileExistsError:
        log.debug("captura ya existente, no se pisa: %s", path)
        return None
    except OSError as exc:
        log.warning("no se pudo guardar la captura %s: %r", path, exc)
        return None
    return path


def recorded(base: Path, anchor_id: str, feed: str) -> list[Path]:
    """Las capturas de una fuente, de la más antigua a la más reciente."""
    folder = base / anchor_id / feed
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file())
