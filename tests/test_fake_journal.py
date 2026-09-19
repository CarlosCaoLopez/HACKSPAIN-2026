"""El journal falso que sustituye al golden hasta el sábado a las 13:00.

Mismos criterios que `tests/test_golden_replay.py` le exige al de verdad —payloads
que validan, `seq` sin huecos, `t_sim` monótono, catálogo completo— más dos que son
propios de un fixture generado: que sea determinista y que la cadena de `causes`
llegue de la llamada a la orden. Sin esa cadena, el panel del H3 no tiene nada que
dibujar y eso se descubre el sábado a las once.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from contracts.events import PAYLOAD_MODELS, Event, EventType

# El fixture vigente es el v4 (v3 = sin la percepción con Jev). Los v1, v2 y v3 están congelados (`fixtures/**` solo se añade)
# con los ids de carretera de antes del renombrado a `road:wp_a-wp_b` y sin los tres
# eventos de voz de P3: ya no se pueden regenerar, así que no se prueban aquí.
FAKE = Path("fixtures/run_fake_v4.jsonl")
GEN = Path("scripts/fake_journal.py")

pytestmark = pytest.mark.skipif(
    not FAKE.exists(),
    reason="falta fixtures/run_fake_v4.jsonl · uv run python scripts/fake_journal.py",
)


def _events(path: Path = FAKE) -> list[Event]:
    return [
        Event.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_payloads_validan() -> None:
    for ev in _events():
        model = PAYLOAD_MODELS.get(ev.type)
        assert model is not None, f"tipo fuera del catálogo: {ev.type}"
        model.model_validate(ev.payload)


def test_seq_sin_huecos_y_t_sim_monotono() -> None:
    last_seq, last_t = 0, -1.0
    for ev in _events():
        assert ev.seq == last_seq + 1, f"hueco en seq: {last_seq} → {ev.seq}"
        assert ev.t_sim >= last_t, f"t_sim retrocede en seq {ev.seq}"
        last_seq, last_t = ev.seq, ev.t_sim


def test_catalogo_completo() -> None:
    """Seis minutos con todos los tipos del catálogo al menos una vez."""
    seen = {ev.type for ev in _events()}
    missing = sorted(set(PAYLOAD_MODELS) - seen)
    assert not missing, f"tipos que el fixture nunca ejercita: {missing}"


def test_causas_anteriores_al_efecto() -> None:
    for ev in _events():
        assert all(c < ev.seq for c in ev.causes), f"causa futura en seq {ev.seq}"


def test_la_cadena_del_climax_esta_completa() -> None:
    """llamada → hecho → violación → replan → política → plan → orden.

    Se sigue por `causes` de verdad, eslabón a eslabón: que existan los siete tipos
    no sirve de nada si no están encadenados.
    """
    events = _events()
    by_seq = {ev.seq: ev for ev in events}

    def ancestors(ev: Event) -> set[EventType]:
        seen: set[EventType] = set()
        stack = list(ev.causes)
        while stack:
            parent = by_seq.get(stack.pop())
            if parent is None or parent.type in seen:
                continue
            seen.add(parent.type)
            stack.extend(parent.causes)
        return seen

    orders = [
        ev
        for ev in events
        if ev.type == EventType.ACTION_REQUESTED
        and EventType.CALL_STARTED in ancestors(ev)
    ]
    assert orders, "ninguna orden desciende de una llamada"

    chain = ancestors(orders[0])
    # El hecho llega DURANTE la llamada (el tool `report_fact` de P3), así que el primer
    # eslabón es `call.started` y no `call.ended`: al colgar solo llega la red de seguridad.
    for link in (
        EventType.CALL_STARTED,
        EventType.WORLD_FACT_ASSERTED,
        EventType.PLAN_VIOLATION,
        EventType.PLAN_REPLAN_STARTED,
        EventType.PLAN_POLICY_EMITTED,
        EventType.PLAN_EMITTED,
    ):
        assert link in chain, f"falta {link} en la cadena de causas"


def _generate(out: Path) -> None:
    subprocess.run(
        [sys.executable, str(GEN), "--out", str(out)],
        check=True,
        capture_output=True,
    )


def test_es_determinista(tmp_path: Path) -> None:
    """Dos generaciones, los mismos bytes. Si no, no se puede comparar por hash y
    cualquier diff del fixture se vuelve ilegible. Y es el candado del fixture: el día
    que alguien meta una escena en medio sin darse cuenta, o P2 renombre algo del YAML
    sin regenerarlo, salta aquí y no con el mapa sin dibujar un corte de carretera.
    """
    out = tmp_path / FAKE.name
    _generate(out)
    assert out.read_bytes() == FAKE.read_bytes()


def test_trae_la_llamada_sin_extraer() -> None:
    """El caso que el panel de llamadas no puede romper: `CallResult.facts is None`.

    Sin fixture que lo ejercite, el `SIN EXTRAER` del H4 se descubre con el golden, el
    domingo por la mañana.
    """
    events = _events()
    ended = [ev for ev in events if ev.type == EventType.CALL_ENDED]
    sin_extraer = [ev for ev in ended if ev.payload["facts"] is None]
    assert len(sin_extraer) == 1, "hay exactamente una llamada sin extraer"
    assert sin_extraer[0].payload["outcome"] == "no_answer"

    # Y el humano supliendo lo que el teléfono no dio, encadenado a esa llamada.
    overrides = [ev for ev in events if ev.type == EventType.HUMAN_OVERRIDE]
    assert any(
        ev.payload["kind"] == "assert_fact" and sin_extraer[0].seq in ev.causes
        for ev in overrides
    ), "el assert_fact no cuelga de la llamada sin respuesta"


def test_la_voz_en_vivo_esta_encadenada() -> None:
    """`call.affect` durante la llamada y la señal del core → voice → agente, en orden.

    Los tres tipos los añadió P3 después del v2. Que existan no basta: la señal tiene que
    colgar del plan que la provoca, y la llamada tiene que seguir abierta cuando se envía
    —una señal a una llamada ya colgada no puede ocurrir—.
    """
    events = _events()
    by_seq = {ev.seq: ev for ev in events}
    requested = next(ev for ev in events if ev.type == EventType.CALL_SIGNAL_REQUESTED)
    sent = next(ev for ev in events if ev.type == EventType.CALL_SIGNAL_SENT)
    assert requested.seq in sent.causes, "la señal enviada no cuelga de la pedida"
    assert any(by_seq[c].type == EventType.PLAN_EMITTED for c in requested.causes), (
        "la señal pedida no cuelga de un plan"
    )

    call_id = sent.payload["call_id"]
    ended = next(
        ev
        for ev in events
        if ev.type == EventType.CALL_ENDED and ev.payload["call_id"] == call_id
    )
    assert sent.t_sim < ended.t_sim, "la señal se envía a una llamada ya colgada"
    affect = [ev for ev in events if ev.type == EventType.CALL_AFFECT]
    assert affect and all(ev.t_sim < ended.t_sim for ev in affect)


def test_las_carreteras_usan_el_id_de_la_arista() -> None:
    """Los cortes y las claves de hecho llevan el id de hoy (`road:wp_a-wp_b`), una vez.

    Con el id viejo (`rd_*`) el mapa no dibuja el corte, y con el prefijo duplicado
    (`road:road:…`) el core no reconoce la clave. Los dos fallan en silencio.
    """
    keys = [
        ev.payload["key"]
        for ev in _events()
        if ev.type == EventType.WORLD_FACT_ASSERTED
        and ev.payload["key"].startswith("road")
    ]
    assert keys, "el fixture no trae ningún hecho de carretera"
    assert all(k.startswith("road:wp_") and "road:road" not in k for k in keys), keys
    # Con la comilla delante: `rd_` a secas casa con `ha`**`rd_`**`constraints`.
    assert not any('"rd_' in ev.model_dump_json() for ev in _events()), (
        "queda un id `rd_*`"
    )
