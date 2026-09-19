"""Northstars: forma del payload. Sin red — la API se prueba a mano con el CLI."""

import pytest

from voice.northstars import ENTRANTE, SALIENTE, SOLAPES, Northstar, _rich


def test_payload_lleva_lo_obligatorio():
    """`name`, `description`, `category` y `version_id` son required en la API."""
    p = ENTRANTE[0].payload("v1")
    assert {"name", "description", "category", "version_id"} <= set(p)
    assert p["version_id"] == "v1"


def test_description_va_en_registros_con_forma_de_slate():
    """La API rechaza strings sueltos con 400 `expected record, received string`,
    y la forma que usa la plataforma para los suyos es Slate: un párrafo con
    `children`, no `content`. Acepta otras, pero entonces la UI no los pinta."""
    p = ENTRANTE[0].payload("v1")
    for campo in ("description", "positive_examples", "negative_examples"):
        for bloque in p[campo]:
            assert bloque["type"] == "paragraph"
            assert bloque["children"][0]["text"]


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


def test_el_saliente_tambien_vigila_su_herramienta():
    """Lo que el retén conteste solo entra en el `WorldState` si el agente llama a
    `reportar_situacion`. Sin esta regla, saltárselo no lo nota nadie."""
    regla = next(r for r in SALIENTE if "reportar_situacion" in r.name)
    assert regla.category == "tool"
    assert regla.priority == "high"


def test_las_sequential_llevan_etapas():
    """Una regla de orden sin `category_config` no ordena nada: la plataforma la
    acepta igual y el juez se queda sin nada contra lo que medirla."""
    for regla in (*ENTRANTE, *SALIENTE):
        if regla.category != "sequential":
            continue
        cfg = regla.payload("v1")["category_config"]
        assert cfg["current_stage"] and cfg["prerequisite_stage"]
        assert cfg["current_stage"] != cfg["prerequisite_stage"]


def test_una_sequential_sin_etapas_no_se_puede_escribir():
    with pytest.raises(ValueError, match="sequential"):
        Northstar(name="x", description=["y"], category="sequential")


def test_las_etapas_solo_valen_en_sequential():
    with pytest.raises(ValueError, match="sequential"):
        Northstar(
            name="x", description=["y"], category="notes", stage="a", after="b"
        )


def test_el_parche_no_lleva_version_y_reenciende():
    """El `PATCH` no acepta `version_id`, y si alguien apagó una de las nuestras
    desde la UI el fichero tiene que volver a encenderla."""
    p = ENTRANTE[0].parche()
    assert "version_id" not in p
    assert p["enabled"] is True


def test_difiere_ve_el_texto_y_no_la_forma_de_los_bloques():
    """El servidor normaliza los bloques ricos a su manera. Si `sync` comparase la
    estructura vería una diferencia en cada pasada y reescribiría para siempre."""
    regla = ENTRANTE[2]
    igual = {
        "description": [_rich(d) for d in regla.description],
        "category": regla.category,
        "priority": regla.priority,
        "positive_examples": [{"text": e} for e in regla.positive_examples],
        "negative_examples": [{"text": e} for e in regla.negative_examples],
        "category_config": {},
        "enabled": True,
    }
    assert regla.difiere(igual) == []
    assert regla.difiere({**igual, "priority": "low"}) == ["prioridad"]
    assert regla.difiere({**igual, "enabled": False}) == ["apagada"]


def test_los_solapes_no_apagan_nada_nuestro():
    """`dedupe` apaga por nombre. Si un nombre de `SOLAPES` fuese también el de una
    regla nuestra, `sync` la crearía y `dedupe` la apagaría en la misma tarde."""
    nuestras = {r.name for r in (*ENTRANTE, *SALIENTE)}
    assert not (nuestras & set(SOLAPES))
    assert all(motivo for motivo in SOLAPES.values())
