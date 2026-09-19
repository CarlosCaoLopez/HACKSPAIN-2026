"""El journal: writer, replay, score y el bus (escribe → reparte).

Corre sin fixtures, sin sim y sin red: eventos sintéticos y ficheros temporales.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from contracts import bus
from contracts.events import Event, EventType
from journal.replay import read, stream, validate
from journal.score import compare, score
from journal.writer import JournalWriter


def _ev(
    seq: int,
    etype: EventType,
    payload: dict,
    *,
    t_sim: float = 0.0,
    source: str = "sim",
    run_id: str = "run_test",
) -> Event:
    return Event(
        run_id=run_id,
        seq=seq,
        t_wall=datetime.now(UTC),
        t_sim=t_sim,
        type=etype,
        source=source,
        payload=payload,
    )


def _tick(seq: int, t_sim: float) -> Event:
    return _ev(
        seq,
        EventType.WORLD_TICK,
        {"t_sim": t_sim, "wind": {"bearing_deg": 270.0, "speed": 1.2}},
        t_sim=t_sim,
    )


# --- writer + replay -------------------------------------------------------


def test_write_read_roundtrip(tmp_path: Path) -> None:
    w = JournalWriter("run_rt", directory=tmp_path)
    events = [_tick(i, float(i)) for i in range(1, 6)]
    for ev in events:
        w.write(ev)
    w.close()

    back = list(read(w.path))
    assert back == events
    assert [e.seq for e in back] == [1, 2, 3, 4, 5]


def test_write_is_append_only(tmp_path: Path) -> None:
    JournalWriter("run_ap", directory=tmp_path).write(_tick(1, 0.0))
    w2 = JournalWriter("run_ap", directory=tmp_path)  # reabre el mismo run
    w2.write(_tick(2, 1.0))
    w2.close()
    assert [e.seq for e in read(tmp_path / "run_ap.jsonl")] == [1, 2]


def test_read_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "run_blank.jsonl"
    path.write_text(_tick(1, 0.0).model_dump_json() + "\n\n", encoding="utf-8")
    assert len(list(read(path))) == 1


# --- validate --------------------------------------------------------------


def test_validate_green_on_good_file(tmp_path: Path) -> None:
    w = JournalWriter("run_ok", directory=tmp_path)
    w.write(_tick(1, 0.0))
    w.write(_ev(2, EventType.RUN_STARTED, {"scenario_id": "wildfire_ridge"}))
    w.close()
    assert validate(w.path) == []


def test_validate_flags_broken_payload(tmp_path: Path) -> None:
    path = tmp_path / "run_bad.jsonl"
    good = _tick(1, 0.0)
    # world.tick sin `wind`: revienta el modelo.
    bad = _ev(2, EventType.WORLD_TICK, {"t_sim": 1.0})
    path.write_text(
        good.model_dump_json() + "\n" + bad.model_dump_json() + "\n",
        encoding="utf-8",
    )
    errors = validate(path)
    assert len(errors) == 1
    assert "seq=2" in errors[0]


# --- stream ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_speed_zero_yields_all_in_order(tmp_path: Path) -> None:
    w = JournalWriter("run_stream", directory=tmp_path)
    for i in range(1, 4):
        w.write(_tick(i, float(i) * 10))
    w.close()

    seqs = [ev.seq async for ev in stream(w.path, speed=0)]
    assert seqs == [1, 2, 3]


@pytest.mark.asyncio
async def test_stream_respects_deltas(tmp_path: Path, monkeypatch) -> None:
    w = JournalWriter("run_delta", directory=tmp_path)
    w.write(_tick(1, 0.0))
    w.write(_tick(2, 5.0))  # 5 s de delta
    w.close()

    slept: list[float] = []

    async def fake_sleep(d: float) -> None:
        slept.append(d)

    monkeypatch.setattr("journal.replay.asyncio.sleep", fake_sleep)
    seqs = [ev.seq async for ev in stream(w.path, speed=1.0)]
    assert seqs == [1, 2]
    assert slept == [5.0]  # solo el delta entre eventos, el primero sin espera


# --- score + compare -------------------------------------------------------


def _run_journal(tmp_path: Path, name: str) -> Path:
    w = JournalWriter(name, directory=tmp_path)
    w.write(_ev(1, EventType.RUN_STARTED, {"scenario_id": "wildfire_ridge"}))
    w.write(
        _ev(
            2,
            EventType.PLAN_REPLAN_STARTED,
            {"reason": "x", "trigger": "divergence", "fired_rules": []},
        )
    )
    w.write(
        _ev(
            3,
            EventType.PLAN_POLICY_EMITTED,
            {"weights": {}, "hard_constraints": [], "rationale": "y"},
        )
    )
    w.write(
        _ev(
            4,
            EventType.WORLD_CIVILIANS_CHANGED,
            {"group_id": "g1", "count": 8, "state": "safe", "poi_id": "poi_a"},
        )
    )
    w.write(
        _ev(
            5,
            EventType.WORLD_CIVILIANS_CHANGED,
            {"group_id": "g2", "count": 3, "state": "exposed", "poi_id": "poi_b"},
        )
    )
    w.write(
        _ev(6, EventType.WORLD_CELL_CHANGED, {"cell_id": "cell_1_1", "state": "burnt", "hazard": "fire"})
    )
    w.close()
    return w.path


def test_score_counts(tmp_path: Path) -> None:
    s = score(_run_journal(tmp_path, "run_score"))
    assert s.scenario_id == "wildfire_ridge"
    assert s.replans == 1
    assert s.llm_calls == 1
    assert s.civilians_safe == 8
    assert s.civilians_exposed_end == 3
    assert s.cells_burnt == 1


def test_compare_delta_signs(tmp_path: Path) -> None:
    a = _run_journal(tmp_path, "run_a")
    # run b: un civil más a salvo → total sube, delta positivo.
    w = JournalWriter("run_b", directory=tmp_path)
    w.write(_ev(1, EventType.RUN_STARTED, {"scenario_id": "wildfire_ridge"}))
    w.write(
        _ev(
            2,
            EventType.WORLD_CIVILIANS_CHANGED,
            {"group_id": "g1", "count": 11, "state": "safe", "poi_id": "poi_a"},
        )
    )
    w.close()
    result = compare(a, w.path)
    assert result["delta"]["civilians_safe"] == 3
    assert result["delta"]["total"] > 0


# --- journal ↔ bus: el writer inyectado escribe ANTES de repartir ----------
#
# El bus lo escribió Hugo (`contracts/bus.py`) y su `writer` es un
# `Callable[[Event], None]`: `JournalWriter.write` es exactamente esa forma. Aquí
# se verifica el invariante que es propio del journal (07-journal.md, done H2):
# cuando el suscriptor ve el evento, la línea ya está en disco. Los internos del
# bus (seq, filtro, malformed) los cubre test_voice_fact.


@pytest.fixture(autouse=True)
def _fresh_bus():
    bus.reset()
    yield
    bus.reset()


@pytest.mark.asyncio
async def test_journal_writer_wired_to_bus_writes_before_dispatch(
    tmp_path: Path,
) -> None:
    w = JournalWriter("run_bus", directory=tmp_path)
    bus.configure(run_id="run_bus", writer=w.write)
    sub = bus.subscribe(EventType.WORLD_TICK)

    await bus.publish(
        bus.make_event(
            EventType.WORLD_TICK,
            {"t_sim": 0.0, "wind": {"bearing_deg": 270.0, "speed": 1.2}},
            source="sim",
        )
    )
    # Cuando el suscriptor lo recibe, el journal YA tiene la línea en disco y es
    # legible por `read`: sin esto no hay replay.
    ev = await sub.__anext__()
    assert ev.seq == 1
    back = list(read(w.path))
    assert len(back) == 1 and back[0].seq == 1
    w.close()
