"""Task 6 · memoria entre runs. Corre offline: fenic y journal se mockean.

Lo que se prueba de verdad es la lógica determinista (trigger DSL, features, select,
apply_patch, I/O de ficheros) y el cableado del recall en el bucle. `harvest` se
prueba con `fenic` y `journal.replay` fakeados.
"""

import sys
import types
from datetime import UTC, datetime
from pathlib import Path

from contracts.events import Event, EventType, ReplanStarted
from contracts.plan import Policy
from contracts.scenario import HazardSpec, Scenario, Waypoint
from contracts.world import (
    POI,
    Cell,
    CivilianGroup,
    RoadEdge,
    Task,
    Unit,
    Wind,
    WorldState,
)
from core import belief, loop, memory, planner
from core.memory import Rule
from journal import replay
from journal.score import RunScore

# --- Estado de referencia ---------------------------------------------------


def _state(bearing: float = 0.0) -> WorldState:
    return WorldState(
        run_id="run_test",
        seq=1,
        t_sim=0.0,
        wind=Wind(bearing_deg=bearing, speed=2.0),
        units={
            "unit_truck": Unit(
                id="unit_truck", kind="fire_truck", x=0, z=0,
                capabilities=["extinguish"],
            )
        },
        cells={
            "cell_0_0": Cell(id="cell_0_0", cx=0, cz=0, state="burning"),
            "cell_0_1": Cell(id="cell_0_1", cx=0, cz=1, state="burning"),
            "cell_0_2": Cell(id="cell_0_2", cx=0, cz=2, state="at_risk"),
        },
        pois={
            "poi_x": POI(
                id="poi_x", name="Pueblo X", kind="village", x=10, z=0, waypoint_id="wp_b"
            )
        },
        civilians={
            "civ_a": CivilianGroup(
                id="civ_a", poi_id="poi_x", count=8, immobile=3, state="exposed"
            )
        },
        tasks={
            "task_ext": Task(
                id="task_ext", kind="extinguish", target_poi="poi_x",
                required_capability="extinguish", severity="high", created_t=0.0,
            )
        },
    )


# --- Trigger DSL ------------------------------------------------------------


def test_eval_trigger_matches_and_misses() -> None:
    feats = {"cells_burning": 5.0}
    assert memory.eval_trigger("cells_burning > 3", feats) is True
    assert memory.eval_trigger("cells_burning < 3", feats) is False
    assert memory.eval_trigger("cells_burning == 5", feats) is True


def test_eval_trigger_unevaluable() -> None:
    # Feature ausente y sintaxis inválida → None (se salta).
    assert memory.eval_trigger("wind_shift_deg > 60", {}) is None
    assert memory.eval_trigger("no hay operador aqui", {"x": 1.0}) is None
    assert memory.eval_trigger("cells_burning > abc", {"cells_burning": 5.0}) is None


def test_ge_before_gt() -> None:
    # ">=" no debe partirse como ">" dejando "=3" a la derecha.
    assert memory.eval_trigger("cells_burning >= 3", {"cells_burning": 3.0}) is True


# --- features ---------------------------------------------------------------


def test_features_from_state() -> None:
    f = memory.features(_state())
    assert f["immobile_total"] == 3.0
    assert f["exposed_total"] == 8.0
    assert f["cells_burning"] == 2.0
    assert f["cells_at_risk"] == 1.0
    assert f["wind_speed"] == 2.0
    assert f["open_tasks"] == 1.0
    assert f["units_idle"] == 1.0
    assert "wind_shift_deg" not in f  # necesita contexto del plan


# --- select -----------------------------------------------------------------


def test_select_gating() -> None:
    hit = Rule(slug="a", trigger="cells_burning > 1", body="contener")
    miss = Rule(slug="b", trigger="cells_burning > 9", body="no aplica")
    skip = Rule(slug="c", trigger="wind_shift_deg > 60", body="no evaluable sin extra")
    out = memory.select(_state(), [hit, miss, skip])
    assert [r.slug for r in out] == ["a"]


def test_select_extra_feature() -> None:
    skip = Rule(slug="c", trigger="wind_shift_deg > 60", body="giro fuerte")
    out = memory.select(_state(), [skip], extra={"wind_shift_deg": 90.0})
    assert [r.slug for r in out] == ["c"]


# --- render -----------------------------------------------------------------


def test_render_one_line_per_rule() -> None:
    rules = [Rule(slug="a", trigger="x > 1", body="evacuar", confidence=0.8)]
    assert memory.render(rules) == "- evacuar (confianza 80%)"
    assert memory.render([]) == ""


# --- apply_patch ------------------------------------------------------------


def _score(exposed_end: int) -> RunScore:
    return RunScore(
        run_id="run_x", scenario_id="sc", civilians_exposed_end=exposed_end
    )


def test_apply_patch_confirm_and_contradict() -> None:
    rule = Rule(slug="a", trigger="x > 1", body="cuerpo", support=1, confidence=0.5)

    good = memory.apply_patch(rule, _score(exposed_end=0))
    assert good.support == 2
    assert good.confidence == 0.65  # 0.5 + 0.3*(1-0.5)
    assert good.body == "cuerpo"  # intacto

    bad = memory.apply_patch(rule, _score(exposed_end=4))
    assert bad.support == 2
    assert bad.confidence == 0.35  # 0.5 + 0.3*(0-0.5)


# --- I/O de ficheros --------------------------------------------------------


def test_write_load_roundtrip(tmp_path: Path) -> None:
    d = tmp_path / "rules"
    rule = Rule(
        slug="viento-gira-evacuar-antes",
        trigger="wind_shift_deg > 60",
        support=3,
        confidence=0.8,
        lineage=["run_a7:seq_412", "run_b2:seq_88"],
        body="Cuando el viento gira más de 60°, evacuar a sotavento antes de reasignar.",
    )
    memory.write_rule(rule, d)
    loaded = memory.load_rules(d)
    assert len(loaded) == 1
    assert loaded[0] == rule


def test_load_rules_missing_dir(tmp_path: Path) -> None:
    assert memory.load_rules(tmp_path / "no-existe") == []


# --- harvest: fenic + journal fakeados --------------------------------------


class _FakeType:
    value = "world.tick"


def _fake_read(path: Path):
    run_id = path.stem
    return [
        types.SimpleNamespace(
            run_id=run_id, seq=1, t_sim=0.0, type=_FakeType(), payload={}
        )
    ]


def _install_fake_fenic(monkeypatch, extracted: list[dict]) -> None:
    class _Col:
        def alias(self, name):
            return self

    class _DF:
        def select(self, *args):
            return self

        def to_pylist(self):
            return extracted

    class _Session:
        @staticmethod
        def get_or_create(config):
            return _Session()

        def create_dataframe(self, rows):
            return _DF()

    semantic = types.SimpleNamespace(extract=lambda col, schema: _Col())
    fake = types.SimpleNamespace(
        Session=_Session,
        SessionConfig=lambda **kw: None,
        col=lambda name: _Col(),
        semantic=semantic,
    )
    monkeypatch.setitem(sys.modules, "fenic", fake)


def test_harvest_keeps_rules_with_support(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "run_a.jsonl").write_text("{}\n")
    (tmp_path / "run_b.jsonl").write_text("{}\n")
    monkeypatch.setattr(replay, "read", _fake_read)

    # "cells_burning > 3" aparece en dos runs → soporte 2 (se conserva).
    # "immobile_total > 0" aparece en uno → soporte 1 (se descarta).
    extracted = [
        {"run_id": "run_a", "rule": {"trigger": "cells_burning > 3", "body": "contener", "evidence": "run_a:seq_10"}},
        {"run_id": "run_b", "rule": {"trigger": "cells_burning > 3", "body": "contener", "evidence": "run_b:seq_20"}},
        {"run_id": "run_a", "rule": {"trigger": "immobile_total > 0", "body": "evacuar", "evidence": "run_a:seq_11"}},
    ]
    _install_fake_fenic(monkeypatch, extracted)

    rules = memory.harvest(tmp_path)
    assert len(rules) == 1
    r = rules[0]
    assert r.trigger == "cells_burning > 3"
    assert r.support == 2
    assert set(r.lineage) == {"run_a:seq_10", "run_b:seq_20"}


def test_harvest_empty_dir(monkeypatch, tmp_path: Path) -> None:
    _install_fake_fenic(monkeypatch, [])
    assert memory.harvest(tmp_path) == []


# --- Cableado en el bucle ---------------------------------------------------


def _scenario() -> Scenario:
    return Scenario(
        id="sc_test",
        name="test",
        hazard=HazardSpec(
            kind="wildfire", origin_cell="cell_0_0", wind=Wind(bearing_deg=0, speed=1.0)
        ),
        waypoints=[Waypoint(id="wp_a", x=0, z=0), Waypoint(id="wp_b", x=10, z=0)],
        roads=[RoadEdge(id="e1", a="wp_a", b="wp_b", length_m=10.0)],
    )


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[Event] = []

    def current_run_id(self) -> str:
        return "run_test"

    async def publish(self, ev: Event) -> None:
        self.published.append(ev)


def _tick() -> Event:
    return Event(
        run_id="run_test", seq=5, t_wall=datetime.now(UTC), t_sim=0.0,
        type=EventType.WORLD_TICK, source="sim",
        payload={"t_sim": 0.0, "wind": {"bearing_deg": 0.0, "speed": 2.0}},
    )


async def test_replan_injects_matching_rules(monkeypatch) -> None:
    """Solo las reglas cuyo trigger casa llegan al planner, y sus slugs salen en
    ReplanStarted como lineage."""
    seen = {"rules": None}

    async def _plan(state, reason, rules=""):
        seen["rules"] = rules
        return Policy(rationale="test")

    hit = Rule(slug="regla-fuego", trigger="cells_burning > 1", body="contener el frente")
    miss = Rule(slug="regla-inerte", trigger="cells_burning > 99", body="no aplica")

    monkeypatch.setattr(planner, "plan", _plan)
    monkeypatch.setattr(memory, "load_rules", lambda *a, **k: [hit, miss])
    monkeypatch.setattr(belief, "initial_state", lambda run_id, sc: _state())
    monkeypatch.setattr(belief, "apply", lambda state, ev: _state())

    bus = _FakeBus()
    core = loop.Core(bus, _scenario())
    await core.on_event(_tick())

    assert seen["rules"] == "- contener el frente (confianza 0%)"
    started = next(
        ReplanStarted.model_validate(e.payload)
        for e in bus.published
        if e.type == EventType.PLAN_REPLAN_STARTED
    )
    assert started.fired_rules == ["regla-fuego"]
