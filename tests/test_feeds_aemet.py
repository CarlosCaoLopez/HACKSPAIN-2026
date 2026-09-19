"""AEMET: avisos Meteoalerta en CAP (SPEC-007, F2 · REQ-253…255). P4.

`fixtures/feeds/_test/aemet_cap_annex3_example.xml` es el **ejemplo oficial del anexo 3** del
Plan Meteoalerta (METEOALERTA_ANX3_CAP, epígrafe 2.4), con los polígonos recortados. No es
una captura de la API: se sustituye por una real (fichero nuevo) al tener la clave. Los casos
que no trae (rojo, verde, cancelación) se fabrican editando su XML aquí mismo.

Lo que se protege:

- **Un solo `<info>` por aviso.** El CAP trae es-ES y en-GB; leer los dos duplica cada aviso.
- **Que un aviso verde o una cancelación no lancen un replan.** Un `critical` de más
  replanifica sin motivo delante de la sala.
"""

import io
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gateway.feeds import FeedContext
from gateway.feeds.aemet import parse_cap, to_facts
from gateway.feeds.anchor import GeoAnchor

XML = Path("fixtures/feeds/_test/aemet_cap_annex3_example.xml").read_bytes()
IDENT = "2.49.0.0.724.0.ES.20171201093433.633303NENV01231512120873"


def anchor(zones=("633303",), events=("NE",)) -> GeoAnchor:
    return GeoAnchor(
        id="t",
        place="Sitio",
        lat0=40.0,
        lon0=-4.0,
        meters_per_block=25,
        aemet_zones=list(zones),
        aemet_events=list(events),
    )


def facts_of(xml: bytes = XML, a: GeoAnchor | None = None, ctx: FeedContext | None = None):
    return to_facts(parse_cap(xml).records, a or anchor(), ctx or FeedContext())


def by_key(obs) -> dict:
    return {o.fact.key: o.fact for o in obs}


def tar_gz(*docs: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for i, doc in enumerate(docs):
            info = tarfile.TarInfo(f"Z_CAP_C_LEMM_20171201093433_AFAZ{i}.xml")
            info.size = len(doc)
            tar.addfile(info, io.BytesIO(doc))
    return buf.getvalue()


# --- parseo ---------------------------------------------------------------------------


def test_el_ejemplo_oficial_da_un_solo_aviso_aunque_traiga_dos_idiomas():
    parsed = parse_cap(XML)
    assert parsed.malformed == 0 and len(parsed.records) == 1
    a = parsed.records[0]
    assert a.identifier == IDENT
    assert a.event == "Aviso de nevadas de nivel naranja"  # el es-ES, no el «Severe snow warning»
    assert (a.level, a.event_code, a.certainty) == ("naranja", "NE", "Likely")
    assert a.zones == ("633303",)


def test_un_tar_gz_con_varios_avisos_se_lee_y_un_aviso_roto_no_tumba_a_los_demas():
    parsed = parse_cap(tar_gz(XML, b"<alert>esto se corta"))
    assert len(parsed.records) == 1 and parsed.malformed == 1


def test_un_aviso_sin_nivel_se_cuenta_como_roto():
    no_level = XML.replace(b"AEMET-Meteoalerta nivel", b"AEMET-Meteoalerta otra-cosa")
    parsed = parse_cap(no_level)
    assert parsed.records == [] and parsed.malformed == 1


# --- hechos -----------------------------------------------------------------------------


def test_un_aviso_naranja_en_la_zona_da_nivel_y_evento_con_su_procedencia():
    facts = by_key(facts_of())
    level, event = facts["alert:aemet:level"], facts["alert:aemet:event"]
    assert level.value == "naranja" and level.severity == "medium"
    assert level.kind == "observed" and level.confidence == 0.8  # Likely
    assert level.source == f"api:aemet:{IDENT}"
    assert event.value == "Aviso de nevadas de nivel naranja"
    assert event.source == level.source  # el banner los une por la procedencia


def test_solo_el_nivel_lleva_la_gravedad_para_no_provocar_dos_replanes():
    red = XML.replace(b"<value>naranja</value>", b"<value>rojo</value>")
    facts = by_key(facts_of(red))
    assert facts["alert:aemet:level"].severity == "critical"
    assert facts["alert:aemet:event"].severity == "low"  # el core no agrupa críticos


def test_el_momento_real_es_el_de_efectividad_del_aviso():
    obs = facts_of()
    assert obs[0].t_real == datetime(2017, 12, 1, 10, 34, 33, tzinfo=timezone(timedelta(hours=1)))


def test_un_aviso_rojo_es_critico():
    red = XML.replace(b"<value>naranja</value>", b"<value>rojo</value>")
    assert by_key(facts_of(red))["alert:aemet:level"].severity == "critical"


def test_un_aviso_amarillo_es_de_gravedad_baja():
    yellow = XML.replace(b"<value>naranja</value>", b"<value>amarillo</value>")
    assert by_key(facts_of(yellow))["alert:aemet:level"].severity == "low"


def test_verde_es_sin_aviso_y_no_publica_nada():
    green = XML.replace(b"<value>naranja</value>", b"<value>verde</value>")
    assert facts_of(green) == []


def test_una_cancelacion_no_es_un_aviso():
    # El anexo 3: un aviso retirado llega con `expires` igual a `effective`.
    cancelled = XML.replace(
        b"<expires>2017-12-01T23:59:59+01:00</expires>",
        b"<expires>2017-12-01T10:34:33+01:00</expires>",
    )
    assert facts_of(cancelled) == []


def test_otra_zona_no_publica_nada():
    assert facts_of(a=anchor(zones=("999999",))) == []


def test_el_evento_casa_por_codigo_o_por_texto_y_sin_lista_no_casa_nada():
    assert facts_of(a=anchor(events=("NE",)))  # código de fenómeno
    assert facts_of(a=anchor(events=("nevadas",)))  # texto del evento, sin mayúsculas
    assert facts_of(a=anchor(events=("VI",))) == []  # vientos: no es esto
    assert facts_of(a=anchor(events=())) == []  # lista vacía: nada declarado, nada casa


def test_el_mismo_aviso_en_dos_sondeos_no_se_republica():
    ctx = FeedContext()
    assert facts_of(ctx=ctx)
    assert facts_of(ctx=ctx) == []
