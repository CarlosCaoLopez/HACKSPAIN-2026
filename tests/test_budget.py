"""El presupuesto de completitud, con reloj simulado: puro, sin red."""

from voice.budget import (
    ASSUMED_CONFIDENCE,
    COMPLETION_BUDGET_S,
    Completeness,
    FieldState,
    safe_default,
)

ROAD = "road:wp_sur_01-wp_sur_02"


def test_urgency_starts_the_clock_and_sets_the_budget() -> None:
    c = Completeness()
    assert c.update({"urgency": ("critical", 0.9)}, now=100.0).ask == "road_blocked"
    assert c.budget_s == COMPLETION_BUDGET_S["critical"] == 8.0
    assert c.elapsed_s(103.0) == 3.0


def test_no_urgency_no_clock_and_no_questions() -> None:
    c = Completeness()
    dec = c.update({"road_blocked": ("not_stated", 0.9)}, now=0.0)
    assert dec.ask is None and dec.fill == [] and c.budget_s is None


def test_hard_fact_needs_the_high_bar() -> None:
    """Cortar una arista cambia rutas de civiles: 0,70 no basta, 0,90 sí."""
    c = Completeness()
    c.update({"urgency": ("critical", 0.9)}, now=0.0)
    dec = c.update({"road_blocked": (ROAD, 0.70)}, now=1.0)
    assert dec.newly_observed == [] and c.fields["road_blocked"].status != "observed"
    assert c.fields["road_blocked"].value == ROAD  # candidato, no hecho
    dec = c.update({"road_blocked": (ROAD, 0.90)}, now=2.0)
    assert dec.newly_observed == ["road_blocked"]
    assert c.fields["road_blocked"].status == "observed"


def test_soft_fact_asserts_at_the_low_bar() -> None:
    c = Completeness()
    dec = c.update({"people_immobile": ("3", 0.60)}, now=0.0)
    assert dec.newly_observed == ["people_immobile"]


def test_asks_one_question_at_a_time_then_fills_when_budget_runs_out() -> None:
    c = Completeness()
    assert c.update({"urgency": ("critical", 0.9)}, now=0.0).ask == "road_blocked"
    # la pregunta sigue viva: no se acumulan
    assert c.update({}, now=2.0).ask is None
    assert c.fields["road_blocked"].status == "asked"
    dec = c.update({}, now=8.0)  # presupuesto agotado
    assert set(dec.fill) == {"road_blocked", "people_immobile", "location_hint"}


def test_assumed_default_is_never_observed_and_gets_falsified() -> None:
    c = Completeness()
    c.update({"urgency": ("critical", 0.9)}, now=0.0)
    c.update({}, now=9.0)
    c.assume("people_immobile", "1")
    st = c.fields["people_immobile"]
    assert st.status == "assumed_default" and st.confidence == ASSUMED_CONFIDENCE
    dec = c.update({"people_immobile": ("3", 0.9)}, now=12.0)
    assert dec.newly_observed == ["people_immobile"]
    assert c.fields["people_immobile"].status == "observed"


def test_a_weak_answer_does_not_overwrite_an_assumption() -> None:
    c = Completeness()
    c.assume("people_immobile", "1")
    c.update({"people_immobile": ("4", 0.3)}, now=0.0)
    assert c.fields["people_immobile"] == FieldState("assumed_default", "1", ASSUMED_CONFIDENCE)


def test_facts_carry_kind_and_call_id() -> None:
    c = Completeness()
    c.update(
        {
            "urgency": ("critical", 0.95),
            "road_blocked": (ROAD, 0.93),
            "location_hint": ("poi_pueblo_b", 0.9),
            "people_immobile": ("5plus", 0.8),
        },
        now=0.0,
    )
    facts = {f.key: f for f in c.facts("hl_1", t_sim=5.0)}
    cut = facts["road:wp_sur_01-wp_sur_02:cut"]
    assert cut.value is True and cut.kind == "observed" and cut.call_id == "hl_1"
    assert cut.severity == "critical" and cut.source == "call:hl_1"
    assert facts["poi:poi_pueblo_b:immobile"].value == 5


def test_immobile_without_a_place_is_not_a_fact() -> None:
    """Sin ubicar no entra al estado: se espera a que llegue `location_hint`."""
    c = Completeness()
    c.update({"people_immobile": ("3", 0.9)}, now=0.0)
    assert c.facts("hl_1", 0.0) == []
    c.update({"location_hint": ("poi_pueblo_a", 0.9)}, now=1.0)
    assert [f.key for f in c.facts("hl_1", 1.0)] == ["poi:poi_pueblo_a:immobile"]


def test_assumed_field_yields_an_assumed_fact() -> None:
    c = Completeness()
    c.update({"location_hint": ("poi_pueblo_b", 0.9)}, now=0.0)
    c.assume("people_immobile", "1")
    (fact,) = c.facts("hl_1", 0.0)
    assert fact.kind == "assumed_default" and fact.confidence == ASSUMED_CONFIDENCE


def test_safe_default_never_invents_a_road_or_a_place() -> None:
    assert safe_default("people_immobile", FieldState()) == "1"
    assert safe_default("road_blocked", FieldState()) is None
    assert safe_default("location_hint", FieldState()) is None
    assert safe_default("road_blocked", FieldState("open", ROAD, 0.7)) == ROAD
