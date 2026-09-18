"""Un journal plausible sin esperar a nadie. P4.

`fixtures/run_golden.jsonl` lo graba P2 el sábado a las 13:00 y es el artefacto más
valioso del proyecto. Hasta entonces el dashboard no tiene con qué trabajar, así que
este script fabrica un run de seis minutos con los 26 tipos del catálogo y una cadena
de `causes` completa. Media hora de trabajo que quita una dependencia entera.

Reglas que cumple y por qué:

- **Los payloads se construyen con los modelos de `contracts`**, nunca con dicts a
  mano: si un modelo cambia, esto falla al generar —donde se ve— y no al replayar a
  mitad de demo.
- **Los tipos se recorren desde `PAYLOAD_MODELS`**, no desde una lista copiada. Añadir
  un tipo de evento es un cambio libre, y una lista copiada se desincroniza en silencio.
- **Determinista**: seed del escenario y `t_wall` derivado de `t_sim` sobre una base
  fija. Dos ejecuciones producen bytes idénticos.
- **`fixtures/**` solo se añade.** Esto genera `run_fake.jsonl` y no toca el golden.

    uv run python scripts/fake_journal.py             # genera
    uv run python scripts/fake_journal.py --validate  # revalida lo generado
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel

from contracts.calls import CallFacts, CallRequest, CallResult
from contracts.events import (
    PAYLOAD_MODELS,
    ActionCompleted,
    ActionFailed,
    ActionRequested,
    CallStarted,
    CellChanged,
    CiviliansChanged,
    DivergenceReport,
    Event,
    EventType,
    FactAsserted,
    FireDetected,
    HumanOverride,
    Inject,
    Malformed,
    ReplanStarted,
    RoadChanged,
    RunEnded,
    RunStarted,
    TranscriptPartial,
    UnitArrived,
    UnitPosition,
    UnitStatusChanged,
    WorldTick,
)
from contracts.plan import (
    Assignment,
    Assumption,
    NotifyIntent,
    Plan,
    PlanContext,
    Policy,
    Violation,
)
from contracts.scenario import Scenario
from contracts.world import Wind

OUT = Path("fixtures/run_fake.jsonl")
SCENARIO = Path("scenarios/wildfire_ridge.yaml")

RUN_ID = "run_fake_0001"
"""Fijo: el fixture es un artefacto reproducible, no un run de verdad."""

BASE_WALL = datetime(2026, 9, 19, 10, 0, 0, tzinfo=UTC)
"""`t_wall` = BASE_WALL + t_sim. Con un `datetime.now()` el fichero cambiaría en cada
ejecución y no se podría comparar por hash."""

DURATION_S = 360.0  # seis minutos, como la demo

# --- Entidades ------------------------------------------------------------------
#
# El YAML de P2 tiene `pois`, `units`, `waypoints`, `roads` y `civilians` a `[]`
# todavía (TODO suyo). De él se lee lo que SÍ está: seed, viento, celda de ignición y
# la línea temporal de injects. Lo demás son estas constantes, que siguen la
# convención de ids de CLAUDE.md y desaparecen solas: `_entities()` prefiere lo que
# venga del escenario en cuanto P2 lo rellene.

WAYPOINTS: dict[str, tuple[float, float]] = {
    "wp_base": (100.0, 20.0),
    "wp_sur_01": (140.0, 60.0),
    "wp_sur_02": (180.0, 80.0),
    "wp_sur_03": (220.0, 100.0),
    "wp_sur_04": (260.0, 120.0),
    "wp_norte_01": (140.0, -20.0),
    "wp_norte_02": (180.0, -40.0),
    "wp_norte_03": (220.0, -60.0),
}

TRUCK1 = "unit_truck1"
TRUCK2 = "unit_truck2"  # el que se avería en el inject de t=240 del YAML
AMBULANCE = "unit_ambulance1"
DRONE = "unit_drone1"

PUEBLO_A = "poi_pueblo_a"
PUEBLO_B = "poi_pueblo_b"

CIV_A = "civ_pueblo_a"
CIV_B = "civ_pueblo_b"

TASK_EVAC_A = "task_evac_a"
TASK_EXTINGUISH = "task_extinguish_ridge"
TASK_NOTIFY_B = "task_notify_b"

CALL_OUT = "hl_8821"  # saliente, HappyRobot
CALL_IN = "vh_1074"  # entrante, humalike · la que dispara el clímax

ROAD_NORTE = "wp_norte_02-wp_norte_03"


# --- El armazón -----------------------------------------------------------------


@dataclass
class Draft:
    """Un evento antes de tener `seq`.

    `causes` va por etiquetas y no por números: los `seq` se asignan al final, cuando
    todo está ordenado por `t_sim`, y una cadena escrita con números a mano se rompe
    en cuanto se añade un evento en medio.
    """

    t_sim: float
    type: EventType
    payload: BaseModel
    source: str
    label: str | None = None
    causes: tuple[str, ...] = ()


@dataclass
class Timeline:
    drafts: list[Draft] = field(default_factory=list)

    def add(
        self,
        t_sim: float,
        type_: EventType,
        payload: BaseModel,
        source: str,
        *,
        label: str | None = None,
        causes: tuple[str, ...] = (),
    ) -> None:
        self.drafts.append(Draft(t_sim, type_, payload, source, label, causes))

    def events(self) -> list[Event]:
        """Ordena por `t_sim` (estable: el orden de escritura decide los empates),
        sella `seq` desde 1 y resuelve las etiquetas de `causes`."""
        ordered = sorted(self.drafts, key=lambda d: d.t_sim)
        seq_of: dict[str, int] = {}
        for i, d in enumerate(ordered, start=1):
            if d.label:
                if d.label in seq_of:
                    raise SystemExit(f"etiqueta duplicada: {d.label}")
                seq_of[d.label] = i

        out: list[Event] = []
        for seq, d in enumerate(ordered, start=1):
            causes = []
            for lbl in d.causes:
                if lbl not in seq_of:
                    raise SystemExit(f"causa desconocida: {lbl}")
                if seq_of[lbl] >= seq:
                    raise SystemExit(
                        f"causa posterior al efecto: {lbl} (seq {seq_of[lbl]}) "
                        f"causa el seq {seq}"
                    )
                causes.append(seq_of[lbl])
            payload = d.payload.model_dump(mode="json")
            model = PAYLOAD_MODELS.get(d.type)
            if model is None:
                raise SystemExit(f"tipo fuera del catálogo: {d.type}")
            model.model_validate(payload)  # revienta aquí, no en la demo
            out.append(
                Event(
                    run_id=RUN_ID,
                    seq=seq,
                    t_wall=BASE_WALL + timedelta(seconds=d.t_sim),
                    t_sim=d.t_sim,
                    type=d.type,
                    source=d.source,
                    payload=payload,
                    causes=causes,
                )
            )
        return out


def _heading(frm: tuple[float, float], to: tuple[float, float]) -> float:
    """Grados, 0 = norte y en sentido horario. En Minecraft el norte es -z."""
    dx, dz = to[0] - frm[0], to[1] - frm[1]
    return round(math.degrees(math.atan2(dx, -dz)) % 360.0, 1)


def _leg(
    tl: Timeline,
    unit_id: str,
    route: list[str],
    t0: float,
    t1: float,
    rng: random.Random,
    step: float = 4.0,
) -> None:
    """`world.unit.position` interpolando la ruta entre `t0` y `t1`.

    El sim interpola a 5 Hz por dentro y publica cada pocos segundos; aquí con un
    punto cada 4 s el mapa del H3 ya se mueve suave y el fichero no se va a 5000
    líneas.
    """
    pts = [WAYPOINTS[w] for w in route]
    total = sum(
        math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)
    )  # metros del mundo
    t = t0
    while t <= t1:
        frac = (t - t0) / (t1 - t0)
        walked = frac * total
        # localizar el tramo
        acc = 0.0
        x, z = pts[-1]
        nxt = pts[-1]
        for i in range(len(pts) - 1):
            d = math.dist(pts[i], pts[i + 1])
            if acc + d >= walked or i == len(pts) - 2:
                u = 0.0 if d == 0 else (walked - acc) / d
                u = min(max(u, 0.0), 1.0)
                x = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * u
                z = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * u
                nxt = pts[i + 1]
                break
            acc += d
        jitter = rng.uniform(-0.4, 0.4)  # el sim no da coordenadas perfectas
        tl.add(
            t,
            EventType.WORLD_UNIT_POSITION,
            UnitPosition(
                unit_id=unit_id,
                x=round(x + jitter, 2),
                z=round(z + jitter, 2),
                heading=_heading((x, z), nxt),
                eta_s=round(max(t1 - t, 0.0), 1),
            ),
            "sim",
        )
        t += step


# --- El guion -------------------------------------------------------------------


def _entities(sc: Scenario) -> dict[str, list[str]]:
    """Lo que declare el escenario manda; mientras esté a `[]`, las constantes."""
    return {
        "units": [u.id for u in sc.units] or [TRUCK1, TRUCK2, AMBULANCE, DRONE],
        "pois": [p.id for p in sc.pois] or [PUEBLO_A, PUEBLO_B],
    }


def build(sc: Scenario) -> list[Event]:
    """Seis minutos de incendio, con la llamada entrante del 03:30 como clímax."""
    rng = random.Random(sc.seed)
    tl = Timeline()
    wind = sc.hazard.wind
    origin = sc.hazard.origin_cell
    cx, cz = (int(p) for p in origin.removeprefix("cell_").split("_"))

    tl.add(0.0, EventType.RUN_STARTED, RunStarted(scenario_id=sc.id), "sim", label="run")

    # --- 00:00 · la ignición ----------------------------------------------------
    tl.add(
        2.0,
        EventType.WORLD_FIRE_DETECTED,
        FireDetected(cell_id=origin, hazard=sc.hazard.kind),
        "sim",
        label="fire",
        causes=("run",),
    )
    tl.add(
        2.5,
        EventType.WORLD_CELL_CHANGED,
        CellChanged(cell_id=origin, state="burning", hazard=sc.hazard.kind),
        "sim",
        causes=("fire",),
    )

    # El viento sopla O→E hasta el inject de t=150: el fuego avanza en +x.
    for i in range(1, 9):
        t = 20.0 + i * 28.0
        if t > DURATION_S:
            break
        tl.add(
            t,
            EventType.WORLD_CELL_CHANGED,
            CellChanged(
                cell_id=f"cell_{cx + i}_{cz}", state="at_risk", hazard=sc.hazard.kind
            ),
            "sim",
        )
        tl.add(
            t + 8.0,
            EventType.WORLD_CELL_CHANGED,
            CellChanged(
                cell_id=f"cell_{cx + i}_{cz}", state="burning", hazard=sc.hazard.kind
            ),
            "sim",
        )
        tl.add(
            t + 20.0,
            EventType.WORLD_CELL_CHANGED,
            CellChanged(
                cell_id=f"cell_{cx + i - 1}_{cz}", state="burnt", hazard=sc.hazard.kind
            ),
            "sim",
        )

    # --- 00:05 · el primer plan -------------------------------------------------
    tl.add(
        5.0,
        EventType.PLAN_REPLAN_STARTED,
        ReplanStarted(reason="ignición detectada en la cresta", trigger="critical_fact"),
        "core",
        label="replan1",
        causes=("fire",),
    )
    policy1 = Policy(
        rationale="Pueblo A a sotavento con dos inmóviles: evacuar antes que contener.",
        weights={"life_safety": 0.6, "immobile_first": 0.25, "containment": 0.15},
        hard_constraints=["no_unit_into_burning_cell", "hospital_min_coverage:1"],
        horizon_s=600,
        notify=[
            NotifyIntent(
                poi_id=PUEBLO_A,
                audience="resident",
                message_intent="orden de evacuación por la pista sur",
                urgency="critical",
            )
        ],
    )
    tl.add(
        6.0,
        EventType.PLAN_POLICY_EMITTED,
        policy1,
        "core",
        label="policy1",
        causes=("replan1",),
    )
    plan1 = Plan(
        id="plan_0001",
        run_id=RUN_ID,
        created_t=6.5,
        policy=policy1,
        assignments=[
            Assignment(
                unit_id=TRUCK1,
                task_id=TASK_EVAC_A,
                route=["wp_base", "wp_sur_01", "wp_sur_02", "wp_sur_03", "wp_sur_04"],
                eta_s=94.0,
                cost=12.4,
            ),
            Assignment(
                unit_id=TRUCK2,
                task_id=TASK_EXTINGUISH,
                route=["wp_base", "wp_norte_01", "wp_norte_02"],
                eta_s=70.0,
                cost=18.1,
            ),
        ],
        unassigned_tasks=[TASK_NOTIFY_B],
        context=PlanContext(
            assumptions=[
                Assumption(key=f"road:{ROAD_NORTE}:open", expected=True, weight=1.0),
                Assumption(key=f"poi:{PUEBLO_A}:immobile", expected=2, weight=0.8),
                Assumption(key="wind:bearing_deg", expected=wind.bearing_deg, weight=0.6),
            ],
            world_seq=8,
        ),
    )
    tl.add(7.0, EventType.PLAN_EMITTED, plan1, "core", label="plan1", causes=("policy1",))

    tl.add(
        8.0,
        EventType.ACTION_REQUESTED,
        ActionRequested(
            action_id="act_0001", verb="goto", args={"unit_id": TRUCK1, "to": "wp_sur_04"}
        ),
        "core",
        label="act1",
        causes=("plan1",),
    )
    tl.add(
        8.5,
        EventType.ACTION_REQUESTED,
        ActionRequested(
            action_id="act_0002",
            verb="set_marker",
            args={"cell_id": origin, "kind": "hazard"},
        ),
        "core",
        causes=("plan1",),
    )
    tl.add(
        9.0,
        EventType.ACTION_COMPLETED,
        ActionCompleted(action_id="act_0002", result={"blocks": 16}),
        "sim",
        causes=("act1",),
    )
    tl.add(
        10.0,
        EventType.WORLD_UNIT_STATUS,
        UnitStatusChanged(unit_id=TRUCK1, status="moving", reason="goto wp_sur_04"),
        "sim",
        causes=("act1",),
    )

    # --- El camión va a Pueblo A y llega ---------------------------------------
    _leg(
        tl,
        TRUCK1,
        ["wp_base", "wp_sur_01", "wp_sur_02", "wp_sur_03", "wp_sur_04"],
        12.0,
        100.0,
        rng,
    )
    tl.add(
        101.0,
        EventType.WORLD_UNIT_ARRIVED,
        UnitArrived(unit_id=TRUCK1, waypoint_id="wp_sur_04"),
        "sim",
        label="arrived1",
    )
    tl.add(
        101.5,
        EventType.ACTION_COMPLETED,
        ActionCompleted(action_id="act_0001", result={"waypoint_id": "wp_sur_04"}),
        "sim",
        causes=("arrived1",),
    )
    tl.add(
        102.0,
        EventType.WORLD_UNIT_STATUS,
        UnitStatusChanged(unit_id=TRUCK1, status="working", reason="evacuación en curso"),
        "sim",
        causes=("arrived1",),
    )
    _leg(tl, TRUCK2, ["wp_base", "wp_norte_01", "wp_norte_02"], 12.0, 80.0, rng)
    _leg(tl, DRONE, ["wp_base", "wp_sur_01", "wp_sur_02"], 20.0, 120.0, rng, step=12.0)

    # --- 00:40 · la llamada saliente -------------------------------------------
    call_req = CallRequest(
        task_id=TASK_EVAC_A,
        poi_id=PUEBLO_A,
        to="+34600111222",
        audience="resident",
        intent="evacuation_order",
        urgency="critical",
        facts={"poi_name": "Pueblo A", "route_name": "pista sur", "deadline_min": "12"},
        expect=["confirmation", "headcount"],
    )
    tl.add(
        40.0,
        EventType.CALL_REQUESTED,
        call_req,
        "core",
        label="callreq",
        causes=("plan1",),
    )
    tl.add(
        42.0,
        EventType.CALL_STARTED,
        CallStarted(
            call_id=CALL_OUT, task_id=TASK_EVAC_A, to="+34600111222", direction="outbound"
        ),
        f"call:{CALL_OUT}",
        label="callstart",
        causes=("callreq",),
    )
    for i, (who, text) in enumerate(
        [
            ("agent", "Le llamo del centro de emergencias de la Cresta oeste."),
            ("agent", "Hay que evacuar Pueblo A por la pista sur en doce minutos."),
            ("resident", "Entendido, salimos. Somos dieciocho."),
            ("resident", "Dos no pueden andar, los llevamos en coche."),
        ]
    ):
        tl.add(
            46.0 + i * 6.0,
            EventType.CALL_TRANSCRIPT_PARTIAL,
            TranscriptPartial(call_id=CALL_OUT, speaker=who, text=text),
            f"call:{CALL_OUT}",
            causes=("callstart",),
        )
    tl.add(
        74.0,
        EventType.CALL_ENDED,
        CallResult(
            call_id=CALL_OUT,
            task_id=TASK_EVAC_A,
            direction="outbound",
            started_t=42.0,
            ended_t=74.0,
            outcome="answered",
            transcript=(
                "Agente: hay que evacuar Pueblo A por la pista sur en doce minutos. "
                "Vecino: entendido, salimos, somos dieciocho, dos no pueden andar."
            ),
            facts=CallFacts(
                location_hint="Pueblo A",
                resolved_poi_id=PUEBLO_A,
                people_immobile=2,
                confirmed_order=True,
                urgency="critical",
                confidence=0.86,
            ),
        ),
        f"call:{CALL_OUT}",
        label="callend",
        causes=("callstart",),
    )
    tl.add(
        76.0,
        EventType.WORLD_FACT_ASSERTED,
        FactAsserted(
            key=f"poi:{PUEBLO_A}:immobile",
            value=2,
            confidence=0.86,
            source=f"call:{CALL_OUT}",
            severity="medium",
        ),
        "voice",
        causes=("callend",),
    )
    tl.add(
        78.0,
        EventType.WORLD_CIVILIANS_CHANGED,
        CiviliansChanged(group_id=CIV_A, count=18, state="warned", poi_id=PUEBLO_A),
        "sim",
        causes=("callend",),
    )
    tl.add(
        110.0,
        EventType.WORLD_CIVILIANS_CHANGED,
        CiviliansChanged(group_id=CIV_A, count=18, state="evacuating", poi_id=PUEBLO_A),
        "sim",
        causes=("arrived1",),
    )

    # --- Divergencia subiendo ---------------------------------------------------
    for t, v, broken in [
        (30.0, 0.04, []),
        (60.0, 0.07, []),
        (120.0, 0.11, []),
        (160.0, 0.19, ["wind:bearing_deg"]),
        (200.0, 0.22, ["wind:bearing_deg"]),
        (218.0, 0.41, ["wind:bearing_deg", f"road:{ROAD_NORTE}:open"]),
        (260.0, 0.12, []),
        (320.0, 0.08, []),
    ]:
        tl.add(
            t,
            EventType.PLAN_DIVERGENCE,
            DivergenceReport(value=v, broken=list(broken)),
            "core",
            label=f"div_{int(t)}",
        )

    # --- 02:30 · los injects del YAML ------------------------------------------
    for spec in sc.injects:
        tl.add(
            spec.at,
            EventType.WORLD_INJECT,
            Inject(inject_type=spec.type, detail=dict(spec.payload)),
            "sim",
            label=f"inject_{int(spec.at)}",
        )
    tl.add(
        152.0,
        EventType.WORLD_TICK,
        WorldTick(t_sim=152.0, wind=Wind(bearing_deg=90.0, speed=1.4)),
        "sim",
        causes=("inject_150",),
    )
    tl.add(
        211.0,
        EventType.WORLD_ROAD_CHANGED,
        RoadChanged(edge_id=ROAD_NORTE, cut=True, cause="árbol caído"),
        "sim",
        label="roadcut",
        causes=("inject_210",),
    )
    tl.add(
        241.0,
        EventType.WORLD_UNIT_STATUS,
        UnitStatusChanged(
            unit_id=TRUCK2, status="unavailable", reason="avería de bomba"
        ),
        "sim",
        label="truck2down",
        causes=("inject_240",),
    )
    tl.add(
        243.0,
        EventType.ACTION_FAILED,
        ActionFailed(action_id="act_0003", error="unit_unavailable"),
        "sim",
        causes=("truck2down",),
    )

    # --- 03:30 · el clímax: llamada entrante → replan --------------------------
    #
    # Esta es la cadena que dibuja el WhatChangedPanel del H3 de punta a punta:
    # llamada → hecho → violación → replan → política → plan → orden.
    tl.add(
        212.0,
        EventType.CALL_STARTED,
        CallStarted(call_id=CALL_IN, task_id=None, to="+34999000111", direction="inbound"),
        f"call:{CALL_IN}",
        label="inbound",
    )
    for i, (who, text) in enumerate(
        [
            ("caller", "Soy el jefe de bomberos, estoy en el desvío norte."),
            ("caller", "La pista norte está cortada por un árbol, no pasa nadie."),
            ("agent", "Entendido, lo doy por cortado y reencaminamos."),
        ]
    ):
        tl.add(
            214.0 + i * 4.0,
            EventType.CALL_TRANSCRIPT_PARTIAL,
            TranscriptPartial(call_id=CALL_IN, speaker=who, text=text),
            f"call:{CALL_IN}",
            causes=("inbound",),
        )
    tl.add(
        228.0,
        EventType.CALL_ENDED,
        CallResult(
            call_id=CALL_IN,
            task_id=None,
            direction="inbound",
            started_t=212.0,
            ended_t=228.0,
            outcome="hung_up",
            transcript=(
                "Jefe de bomberos: la pista norte está cortada por un árbol, "
                "no pasa nadie."
            ),
            facts=CallFacts(
                location_hint="desvío norte",
                road_blocked=ROAD_NORTE,
                contradicts_known=True,
                urgency="critical",
                confidence=0.93,
            ),
        ),
        f"call:{CALL_IN}",
        label="inboundend",
        causes=("inbound",),
    )
    tl.add(
        229.0,
        EventType.WORLD_FACT_ASSERTED,
        FactAsserted(
            key=f"road:{ROAD_NORTE}:cut",
            value=True,
            confidence=0.93,
            source=f"call:{CALL_IN}",
            severity="critical",
        ),
        "voice",
        label="fact_road",
        causes=("inboundend",),
    )
    tl.add(
        229.5,
        EventType.PLAN_VIOLATION,
        Violation(
            verifier="route_feasible",
            severity="hard",
            message=f"la ruta de {AMBULANCE} cruza {ROAD_NORTE}, cortado",
            involved=[AMBULANCE, ROAD_NORTE, TASK_NOTIFY_B],
        ),
        "core",
        label="violation",
        causes=("fact_road",),
    )
    tl.add(
        230.0,
        EventType.PLAN_REPLAN_STARTED,
        ReplanStarted(
            reason="pista norte cortada, confirmado por llamada entrante",
            trigger="hard_constraint_violation",
        ),
        "core",
        label="replan2",
        causes=("violation", "div_218"),
    )
    policy2 = Policy(
        rationale="Norte cortado y viento girado: todo por el sur y proteger el refugio.",
        weights={
            "life_safety": 0.5,
            "response_time": 0.2,
            "structure_protection": 0.2,
            "containment": 0.1,
        },
        hard_constraints=[
            "no_unit_into_burning_cell",
            "no_civilian_route_through:wp_norte_02",
            "reserve_capability:transport:1",
        ],
        horizon_s=420,
        notify=[
            NotifyIntent(
                poi_id=PUEBLO_B,
                audience="official",
                message_intent="aviso de corte y desvío por el sur",
                urgency="medium",
            )
        ],
    )
    tl.add(
        231.0,
        EventType.PLAN_POLICY_EMITTED,
        policy2,
        "core",
        label="policy2",
        causes=("replan2",),
    )
    plan2 = Plan(
        id="plan_0002",
        run_id=RUN_ID,
        created_t=231.5,
        policy=policy2,
        assignments=[
            Assignment(
                unit_id=TRUCK1,
                task_id=TASK_EVAC_A,
                route=["wp_sur_04"],
                eta_s=0.0,
                cost=3.2,
            ),
            Assignment(
                unit_id=AMBULANCE,
                task_id=TASK_NOTIFY_B,
                route=["wp_base", "wp_sur_01", "wp_sur_02", "wp_sur_03"],
                eta_s=88.0,
                cost=21.7,
            ),
        ],
        unassigned_tasks=[TASK_EXTINGUISH],  # truck2 averiado: se enseña sin cubrir
        context=PlanContext(
            assumptions=[
                Assumption(key=f"road:{ROAD_NORTE}:open", expected=False, weight=1.0),
                Assumption(key="wind:bearing_deg", expected=90.0, weight=0.6),
            ],
            world_seq=420,
        ),
    )
    tl.add(
        232.0, EventType.PLAN_EMITTED, plan2, "core", label="plan2", causes=("policy2",)
    )
    tl.add(
        233.0,
        EventType.ACTION_REQUESTED,
        ActionRequested(
            action_id="act_0004",
            verb="goto",
            args={"unit_id": AMBULANCE, "to": "wp_sur_03"},
        ),
        "core",
        label="act4",
        causes=("plan2",),
    )
    tl.add(
        234.0,
        EventType.WORLD_UNIT_STATUS,
        UnitStatusChanged(unit_id=AMBULANCE, status="moving", reason="goto wp_sur_03"),
        "sim",
        causes=("act4",),
    )
    _leg(
        tl,
        AMBULANCE,
        ["wp_base", "wp_sur_01", "wp_sur_02", "wp_sur_03"],
        236.0,
        320.0,
        rng,
    )
    tl.add(
        321.0,
        EventType.WORLD_UNIT_ARRIVED,
        UnitArrived(unit_id=AMBULANCE, waypoint_id="wp_sur_03"),
        "sim",
        causes=("act4",),
    )
    tl.add(
        322.0,
        EventType.ACTION_COMPLETED,
        ActionCompleted(action_id="act_0004", result={"waypoint_id": "wp_sur_03"}),
        "sim",
        causes=("act4",),
    )

    # --- 04:30 · la intervención humana ----------------------------------------
    tl.add(
        270.0,
        EventType.HUMAN_OVERRIDE,
        HumanOverride(
            kind="veto_assignment",
            target=f"{AMBULANCE}:{TASK_NOTIFY_B}",
            value=None,
            note="el jefe de bomberos quiere la ambulancia en el refugio",
        ),
        "human",
        label="override",
    )
    tl.add(
        271.0,
        EventType.ACTION_REQUESTED,
        ActionRequested(
            action_id="act_0005",
            verb="announce",
            args={"poi_id": PUEBLO_B, "text": "desvío por la pista sur"},
        ),
        "core",
        label="act5",
        causes=("override",),
    )
    tl.add(
        272.0,
        EventType.ACTION_COMPLETED,
        ActionCompleted(action_id="act_0005", result={"audience": 6}),
        "sim",
        causes=("act5",),
    )
    tl.add(
        280.0,
        EventType.ACTION_REQUESTED,
        ActionRequested(
            action_id="act_0006", verb="rescue", args={"group_id": CIV_A, "count": 2}
        ),
        "core",
        label="act6",
        causes=("override",),
    )
    tl.add(
        290.0,
        EventType.ACTION_COMPLETED,
        ActionCompleted(action_id="act_0006", result={"rescued": 2}),
        "sim",
        causes=("act6",),
    )

    # --- Un payload malformado, que pasa y no puede tumbar nada ----------------
    tl.add(
        300.0,
        EventType.EVENT_MALFORMED,
        Malformed(
            type="world.cell.changed",
            error="1 validation error for CellChanged: state · unexpected value 'smoking'",
            raw={"cell_id": f"cell_{cx + 3}_{cz}", "state": "smoking"},
        ),
        "sim",
    )

    # --- Cierre -----------------------------------------------------------------
    tl.add(
        330.0,
        EventType.WORLD_CIVILIANS_CHANGED,
        CiviliansChanged(group_id=CIV_A, count=18, state="safe", poi_id=PUEBLO_A),
        "sim",
    )
    tl.add(
        332.0,
        EventType.WORLD_CIVILIANS_CHANGED,
        CiviliansChanged(group_id=CIV_B, count=6, state="warned", poi_id=PUEBLO_B),
        "sim",
    )
    tl.add(
        340.0,
        EventType.WORLD_UNIT_STATUS,
        UnitStatusChanged(unit_id=TRUCK1, status="idle", reason="evacuación completada"),
        "sim",
    )

    # El tick es el latido: uno por segundo simulado, con el viento vigente.
    for i in range(int(DURATION_S) + 1):
        t = float(i)
        w = wind if t < 152.0 else Wind(bearing_deg=90.0, speed=1.4)
        tl.add(t, EventType.WORLD_TICK, WorldTick(t_sim=t, wind=w), "sim")

    tl.add(
        DURATION_S,
        EventType.RUN_ENDED,
        RunEnded(scenario_id=sc.id, score=0.78),
        "core",
    )
    return tl.events()


# --- Salida y validación --------------------------------------------------------


def write(events: list[Event], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    # Una línea JSON por evento, \n siempre (no \r\n): el fichero se compara por hash
    # y Windows no puede cambiarlo.
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for ev in events:
            fh.write(ev.model_dump_json() + "\n")


def validate(path: Path) -> list[str]:
    """Los mismos tres criterios que `make check` le exige al golden: payloads que
    validan, `seq` sin huecos y `t_sim` monótono. Más el catálogo completo."""
    errors: list[str] = []
    seen: set[EventType] = set()
    last_seq = 0
    last_t = -1.0
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            ev = Event.model_validate_json(line)
        except Exception as exc:  # noqa: BLE001 — el mensaje es el valor aquí
            errors.append(f"línea {n}: sobre inválido · {exc}")
            continue
        model = PAYLOAD_MODELS.get(ev.type)
        if model is None:
            errors.append(f"seq {ev.seq}: tipo fuera del catálogo · {ev.type}")
            continue
        try:
            model.model_validate(ev.payload)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"seq {ev.seq} ({ev.type}): payload inválido · {exc}")
        if ev.seq != last_seq + 1:
            errors.append(f"hueco en seq: {last_seq} → {ev.seq}")
        if ev.t_sim < last_t:
            errors.append(f"seq {ev.seq}: t_sim retrocede · {last_t} → {ev.t_sim}")
        if any(c >= ev.seq for c in ev.causes):
            errors.append(f"seq {ev.seq}: causa posterior al efecto · {ev.causes}")
        last_seq, last_t = ev.seq, ev.t_sim
        seen.add(ev.type)
    missing = sorted(set(PAYLOAD_MODELS) - seen)
    if missing:
        errors.append(f"tipos que el fixture nunca ejercita: {missing}")
    return errors


def main() -> None:
    # La consola de Windows viene en cp1252 y un "✓" la tumba con UnicodeEncodeError.
    # El fichero ya se escribe en utf-8 explícito; esto es solo para los mensajes.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--scenario", type=Path, default=SCENARIO)
    ap.add_argument(
        "--validate",
        action="store_true",
        help="no genera: revalida el fichero que ya existe",
    )
    args = ap.parse_args()

    if args.validate:
        if not args.out.exists():
            raise SystemExit(f"no existe {args.out}: genéralo primero")
        errors = validate(args.out)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        if errors:
            raise SystemExit(f"{len(errors)} errores en {args.out}")
        lines = sum(1 for _ in args.out.open(encoding="utf-8"))
        print(f"✓ {args.out} · {lines} eventos · {len(PAYLOAD_MODELS)} tipos del catálogo")
        return

    sc = Scenario.model_validate(yaml.safe_load(args.scenario.read_text(encoding="utf-8")))
    ents = _entities(sc)
    events = build(sc)
    write(events, args.out)
    errors = validate(args.out)
    for e in errors:
        print(f"  ✗ {e}", file=sys.stderr)
    if errors:
        raise SystemExit(f"{len(errors)} errores: el fixture no sirve")
    types = len({ev.type for ev in events})
    print(
        json.dumps(
            {
                "out": str(args.out),
                "events": len(events),
                "types": types,
                "t_sim": events[-1].t_sim,
                "scenario": sc.id,
                "units": ents["units"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
