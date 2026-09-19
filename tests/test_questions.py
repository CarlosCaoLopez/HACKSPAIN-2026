"""El catálogo de preguntas de Jev sale del escenario: nada escrito a mano."""

from pathlib import Path

import pytest

from contracts.calls import Fact
from contracts.events import PAYLOAD_MODELS, EventType
from contracts.questions import IMMOBILE_OPTIONS, NOT_STATED, call_questions
from sim.scenario import load

SCENARIOS = sorted(Path("scenarios").glob("*.yaml"))


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_choices_offer_only_ids_of_the_scenario(path: Path) -> None:
    """El espacio de salida del modelo es el conjunto de objetos del YAML."""
    scn = load(path)
    q = call_questions(scn)
    assert set(q["location_hint"].options) == {p.id for p in scn.pois} | {NOT_STATED}
    assert set(q["road_blocked"].options) == {r.id for r in scn.roads} | {NOT_STATED}
    assert set(q["people_immobile"].options) == {*IMMOBILE_OPTIONS, NOT_STATED}


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_every_criterion_has_a_description(path: Path) -> None:
    for name, spec in call_questions(load(path)).items():
        if isinstance(spec.criteria, dict):
            assert all(spec.criteria.values()), name


def test_every_question_says_caller_only() -> None:
    """La lectura literal de Jev: si el operador nombra la pista, está en el texto."""
    q = call_questions(load(SCENARIOS[0]))
    for key in ("location_hint", "road_blocked", "people_immobile", "urgency"):
        assert "CALLER" in q[key].instructions


def test_fact_kind_defaults_keep_old_journals_valid() -> None:
    f = Fact(key="poi:poi_a:immobile", value=3, confidence=0.9, source="call:x",
             severity="critical", t_sim=1.0)
    assert f.kind == "observed" and f.call_id is None
    old = {"key": "k", "value": 1, "confidence": 1.0, "source": "s", "severity": "low"}
    PAYLOAD_MODELS[EventType.WORLD_FACT_ASSERTED].model_validate(old)


def test_completeness_payload_is_registered() -> None:
    PAYLOAD_MODELS[EventType.CALL_COMPLETENESS].model_validate(
        {"call_id": "hl_1", "fields": [{"key": "road_blocked", "status": "open"}]}
    )
