"""El mapa de claves de hecho. P1 lo escribe, P3 lo usa.

Sin este fichero, P3 inventa claves y P1 las ignora en silencio, que es el bug más
caro que podéis tener el domingo.

Una clave es una plantilla con segmentos separados por `:`. Los segmentos entre
`<>` son variables (ids con prefijo). Añadir una clave es libre; borrar, no.
"""

FACT_KEYS: dict[str, type] = {
    # carreteras
    "road:<edge_id>:cut": bool,
    "road:<edge_id>:cause": str,
    # POIs y su población
    "poi:<poi_id>:immobile": int,
    "poi:<poi_id>:injuries": int,
    "poi:<poi_id>:headcount": int,
    "poi:<poi_id>:confirmed": bool,
    "poi:<poi_id>:evacuated": bool,
    # celdas de peligro
    "cell:<cell_id>:state": str,
    # unidades
    "unit:<unit_id>:available": bool,
    # viento
    "wind:bearing_deg": float,
    "wind:speed": float,
}


def _matches(template: str, key: str) -> bool:
    tpl = template.split(":")
    seg = key.split(":")
    if len(tpl) != len(seg):
        return False
    return all(
        (t.startswith("<") and t.endswith(">")) or t == s for t, s in zip(tpl, seg)
    )


def validate_fact_key(key: str) -> type | None:
    """Devuelve el tipo esperado del valor, o None si la clave no está en el mapa.

    Una clave desconocida no se aplica al WorldState: se registra y se muestra.
    """
    for template, expected in FACT_KEYS.items():
        if _matches(template, key):
            return expected
    return None


def road_cut_key(edge_id: str) -> str:
    """`road:wp_sur_03-wp_sur_04:cut` — la clave de la demo, con su helper."""
    return f"road:{edge_id}:cut"
