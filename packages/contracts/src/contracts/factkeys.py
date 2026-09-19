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
    # El punto exacto que ha mandado un vecino por GPS, "x,z" del mundo. Un pin se
    # ancla al POI más cercano, pero un perdido no está EN el pueblo: está donde
    # dice el pin, y ahí es donde tiene que ir la ambulancia.
    "poi:<poi_id>:rescue_point": str,
    "poi:<poi_id>:evacuated": bool,
    "poi:<poi_id>:shelter_ready": bool,  # el pueblo vecino dice si puede acoger gente
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


def road_bare(edge_id: str) -> str:
    """El id de una arista sin el prefijo `road:`.

    Los ids del escenario YA lo llevan (`road:wp_sur_03-wp_sur_04`, porque el id es
    también su dirección) y las claves de hecho llevan el prefijo UNA vez
    (`road:wp_sur_03-wp_sur_04:cut`, que es lo que valida la plantilla de arriba). Con un
    id de escenario, `f"road:{edge_id}:cut"` da `road:road:…` —una clave que
    `validate_fact_key` rechaza en silencio— y por eso todo pasa por aquí. Idempotente:
    un id sin prefijo se devuelve tal cual."""
    return edge_id.removeprefix("road:")


def road_cut_key(edge_id: str) -> str:
    """`road:wp_sur_03-wp_sur_04:cut` — la clave de la demo. Acepta el id con o sin
    `road:`."""
    return f"road:{road_bare(edge_id)}:cut"


def road_open_key(edge_id: str) -> str:
    """`road:wp_sur_03-wp_sur_04:open` — la suposición de un plan sobre una arista."""
    return f"road:{road_bare(edge_id)}:open"


def road_cause_key(edge_id: str) -> str:
    """`road:wp_sur_03-wp_sur_04:cause` — el motivo del corte."""
    return f"road:{road_bare(edge_id)}:cause"
