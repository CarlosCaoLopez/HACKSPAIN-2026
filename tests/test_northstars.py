"""Northstars: forma del payload. Sin red — la API se prueba a mano con el CLI."""

from voice.northstars import ENTRANTE, SALIENTE, Northstar


def test_payload_lleva_lo_obligatorio():
    """`name`, `description`, `category` y `version_id` son required en la API."""
    p = ENTRANTE[0].payload("v1")
    assert {"name", "description", "category", "version_id"} <= set(p)
    assert p["version_id"] == "v1"


def test_description_va_en_registros_no_en_strings():
    """La API rechaza strings sueltos con 400 `expected record, received string`."""
    p = ENTRANTE[0].payload("v1")
    for campo in ("description", "positive_examples", "negative_examples"):
        for bloque in p[campo]:
            assert isinstance(bloque, dict)
            assert bloque["text"]


def test_categorias_y_prioridades_son_las_de_la_api():
    for regla in (*ENTRANTE, *SALIENTE):
        assert regla.category in ("notes", "style", "tool", "sequential")
        assert regla.priority in ("low", "medium", "high")


def test_nombres_unicos():
    """`sync` deduplica por nombre: dos reglas con el mismo nombre se pisarían."""
    for reglas in (ENTRANTE, SALIENTE):
        nombres = [r.name for r in reglas]
        assert len(nombres) == len(set(nombres))


def test_la_regla_del_tool_existe_y_es_alta():
    """El clímax de la demo depende de que `report_fact` se invoque en llamada."""
    regla = next(r for r in ENTRANTE if "report_fact" in r.name)
    assert regla.category == "tool"
    assert regla.priority == "high"
    assert isinstance(regla, Northstar)
