"""El contrato, verificado. Corre sin fixtures, sin sim y sin red."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from contracts.events import PAYLOAD_MODELS, Event, EventType
from contracts.factkeys import validate_fact_key
from contracts.plan import CONSTRAINTS, WEIGHTS, Policy, is_known_constraint
from contracts.world import Wind, WorldState


def test_policy_never_assigns() -> None:
    """LA REGLA DE ORO. Si esto falla, el diseño entero se vino abajo."""
    assert "assignments" not in Policy.model_fields


def test_every_event_type_has_a_payload_model() -> None:
    missing = sorted(set(EventType) - set(PAYLOAD_MODELS))
    assert not missing, f"tipos sin modelo de payload: {missing}"


def test_envelope_roundtrips() -> None:
    ev = Event(
        run_id="run_test",
        seq=1,
        t_wall=datetime.now(UTC),
        t_sim=0.0,
        type=EventType.WORLD_TICK,
        source="sim",
        payload={"t_sim": 0.0, "wind": {"bearing_deg": 270.0, "speed": 1.2}},
    )
    again = Event.model_validate_json(ev.model_dump_json())
    assert again == ev
    PAYLOAD_MODELS[again.type].model_validate(again.payload)


def test_world_state_is_immutable() -> None:
    """`apply` devuelve un estado nuevo: nadie muta en sitio."""
    state = WorldState(
        run_id="run_test", seq=0, t_sim=0.0, wind=Wind(bearing_deg=0, speed=0)
    )
    with pytest.raises(ValidationError):
        state.seq = 1  # type: ignore[misc]


def test_constraint_arity() -> None:
    assert is_known_constraint("no_unit_into_burning_cell")
    assert is_known_constraint("hospital_min_coverage:1")
    assert not is_known_constraint("hospital_min_coverage")  # le falta el argumento
    assert not is_known_constraint("no_existe:1")
    assert set(CONSTRAINTS) and set(WEIGHTS)


def test_fact_keys() -> None:
    assert validate_fact_key("road:wp_sur_03-wp_sur_04:cut") is bool
    assert validate_fact_key("poi:poi_pueblo_a:immobile") is int
    assert validate_fact_key("inventada:del:todo") is None
