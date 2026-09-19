"""Un journal plausible sin esperar a nadie. P4.

`fixtures/run_golden.jsonl` lo graba P2 el sábado a las 13:00 y es el artefacto más
valioso del proyecto. Hasta entonces el dashboard no tiene con qué trabajar, así que
este script fabrica un run de seis minutos con los 29 tipos del catálogo y una cadena
de `causes` completa. Media hora de trabajo que quita una dependencia entera.

Reglas que cumple y por qué:

- **Los payloads se construyen con los modelos de `contracts`**, nunca con dicts a
  mano: si un modelo cambia, esto falla al generar —donde se ve— y no al replayar a
  mitad de demo.
- **Los tipos se recorren desde `PAYLOAD_MODELS`**, no desde una lista copiada. Añadir
  un tipo de evento es un cambio libre, y una lista copiada se desincroniza en silencio.
- **Determinista**: seed del escenario y `t_wall` derivado de `t_sim` sobre una base
  fija. Dos ejecuciones producen bytes idénticos.
- **`fixtures/**` solo se añade.** Esto genera `run_fake_v3.jsonl` y no toca el golden ni
  los fixtures anteriores. `run_fake.jsonl` (v1) y `run_fake_v2.jsonl` se quedan como
  están: llevan los ids de carretera de antes de que P2 los renombrara a `road:wp_a-wp_b`
  y los tres eventos de voz que P3 añadió después, así que **ya no se pueden regenerar**
  (el escenario de hoy no produce esos bytes) y por eso ya no hay `--variant`.

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
    CallAffect,
    CallStarted,
    CellChanged,
    CiviliansChanged,
    DivergenceReport,
    Emotion,
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
    SignalRequested,
    SignalSent,
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
from gateway.scenarios import load_scenario

# v1 (`run_fake.jsonl`, H2/H3) y v2 (`run_fake_v2.jsonl`, H4) están CONGELADOS: los
# criterios de aceptación de SPEC-003 y SPEC-004 van por sus `t_sim`. `fixtures/**` solo
# se añade, así que v3 es otro fichero: v2 + los ids de carretera de hoy + la voz en vivo
# de P3 (`call.affect`, `call.signal.requested`, `call.signal.sent`).
OUT = Path("fixtures/run_fake_v3.jsonl")
SCENARIO = Path("scenarios/wildfire_ridge.yaml")

RUN_ID = "run_fake_0001"
"""Fijo: el fixture es un artefacto reproducible, no un run de verdad."""


@dataclass(frozen=True)
class Variation:
    """Un run del mismo guion, salido mejor o peor. Para el run 1 vs run 12 del H5.

    `runs/` está vacío y en replay no se llena (un run reproducido no genera otro run),
    así que sin esto la pantalla partida de la comparación no se puede construir hasta
    que Carlos corra los doce runs el domingo de madrugada.

    **Los valores por defecto son los de hoy, byte a byte**: `Variation()` produce
    exactamente `run_fake_v3.jsonl`, y `test_es_determinista` lo comprueba contra el
    fichero commiteado. Un fixture que se mueve sin querer corre los `t_sim` contra los
    que están escritos los criterios.

    Y lo que sale de aquí **se etiqueta sintético** en pantalla (`/api/runs`): un run
    fabricado por mí no puede colarse en una comparación del pitch.
    """

    run_id: str = RUN_ID
    quality: float = 0.0
    """0 = el run de hoy, tal cual está commiteado; 1 = el mejor. Mueve tres cosas: hasta
    dónde llega el fuego, si la llamada de la orden se completa, y la puntuación final.
    Las tres arrancan en el valor del fixture, que es lo que lo mantiene byte a byte."""

    @property
    def spread(self) -> int:
        """Celdas que alcanza el fuego. Menos fuego es un run mejor."""
        return 8 - round(self.quality * 4)

    @property
    def answered(self) -> bool:
        """En un run bueno, la llamada de la orden se completa: no hay `facts=None` que suplir."""
        return self.quality >= 0.5

    @property
    def final_score(self) -> float:
        """0,78 es el del fixture y el suelo de la escala: el run 1 no es un desastre,
        es el que todavía no ha aprendido nada."""
        return round(0.78 + 0.14 * self.quality, 3)


BASE_WALL = datetime(2026, 9, 19, 10, 0, 0, tzinfo=UTC)
"""`t_wall` = BASE_WALL + t_sim. Con un `datetime.now()` el fichero cambiaría en cada
ejecución y no se podría comparar por hash."""

DURATION_S = 360.0  # seis minutos, como la demo

# --- Entidades ------------------------------------------------------------------
#
# Los ids, las coordenadas, las carreteras y el corte de la maqueta NO se declaran aquí:
# salen de `scenarios/wildfire_ridge.yaml` (P2), que es lo mismo que sirve
# `GET /api/scenario` al mapa del dashboard y lo que levanta `make world`. Si P2 renombra
# un waypoint o quita una carretera, este script falla al **generar** —donde se ve— y no
# con el camión moviéndose hacia un waypoint que no existe en mitad de la demo.
#
# Lo que sí es de aquí es el guion: qué unidad va a dónde y las tareas. Las rutas están
# escritas a mano y `_check_route` las comprueba contra las carreteras del YAML.

_MAQUETA = load_scenario("wildfire_ridge")
WAYPOINTS: dict[str, tuple[float, float]] = {w.id: (w.x, w.z) for w in _MAQUETA.waypoints}
_ROADS = {frozenset((r.a, r.b)): r.id for r in _MAQUETA.roads}
_CIVILIANS = {c.id: c for c in _MAQUETA.civilians}


def _declared(kind: str, wanted: str, have: set[str]) -> str:
    if wanted not in have:
        raise SystemExit(f"el escenario ya no declara {kind} {wanted!r}: {sorted(have)}")
    return wanted


def _check_route(route: list[str]) -> list[str]:
    """La ruta solo cruza waypoints y carreteras que el YAML declara."""
    for a, b in zip(route, route[1:]):
        _declared("el waypoint", a, set(WAYPOINTS))
        if frozenset((a, b)) not in _ROADS:
            raise SystemExit(f"el escenario no tiene carretera entre {a} y {b}")
    _declared("el waypoint", route[-1], set(WAYPOINTS))
    return route


_UNITS = {u.id for u in _MAQUETA.units}
_POIS = {p.id for p in _MAQUETA.pois}
TRUCK1 = _declared("la unidad", "unit_truck1", _UNITS)
TRUCK2 = _declared("la unidad", "unit_truck2", _UNITS)  # se avería en el inject de t=240
AMBULANCE = _declared("la unidad", "unit_ambulance", _UNITS)
DRONE = _declared("la unidad", "unit_drone", _UNITS)
PUEBLO_A = _declared("el POI", "poi_pueblo_a", _POIS)
PUEBLO_B = _declared("el POI", "poi_pueblo_b", _POIS)
CIV_A = _declared("el grupo", "civ_pueblo_a", set(_CIVILIANS))
CIV_B = _declared("el grupo", "civ_pueblo_b", set(_CIVILIANS))

CIV_A_COUNT = _CIVILIANS[CIV_A].count
CIV_A_IMMOBILE = _CIVILIANS[CIV_A].immobile
CIV_B_COUNT = _CIVILIANS[CIV_B].count

# La carretera que corta el inject `road_cut` del YAML. El id es el de la propia
# carretera (`road:wp_a-wp_b`): es el `edge_id` que emite el sim, el que casa con
# `roads[].id` en el mapa y —tal cual, más `:cut`— la clave de hecho que documenta
# `interfaces.md` (`road:wp_sur_01-wp_sur_02:cut`). Por eso las claves se forman con
# `f"{ROAD_CUT}:cut"` y no con `"road:" + ROAD_CUT + ":cut"`, que duplicaría el prefijo.
ROAD_CUT = next(i.payload["edge"] for i in _MAQUETA.injects if i.type == "road_cut")
_declared("la carretera", ROAD_CUT, set(_ROADS.values()))

# El desvío sur (corto, expuesto) es el de la primera orden; el norte es el rodeo cuando
# se corta. A Pueblo B solo se llega por `wp_sur_02`, es decir, pasando por Pueblo A.
ROUTE_EVAC_A = _check_route(["wp_base", "wp_cruce", "wp_sur_01", "wp_sur_02", "wp_pueblo_a"])
ROUTE_EXTINGUISH = _check_route(["wp_base", "wp_cruce", "wp_sur_01"])
ROUTE_RECON = _check_route(["wp_base", "wp_cruce", "wp_sur_01", "wp_sur_02"])
ROUTE_NOTIFY_B = _check_route(
    [
        "wp_hospital",
        "wp_cruce",
        "wp_nor_01",
        "wp_nor_02",
        "wp_pueblo_a",
        "wp_sur_02",
        "wp_pueblo_b",
    ]
)

TASK_EVAC_A = "task_evac_a"
TASK_EXTINGUISH = "task_extinguish_ridge"
TASK_NOTIFY_B = "task_notify_b"

CALL_OUT = "hl_8821"  # la orden: llamamos al agente (HappyRobot) y nos la dicta
CALL_IN = "vh_1074"  # el vecino: info del terreno, humalike · dispara el clímax
CALL_NO_ANSWER = "hl_9002"  # la orden que no se completa · la orden a Pueblo B que no contesta


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

    def events(self, run_id: str = RUN_ID) -> list[Event]:
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
                    run_id=run_id,
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
    """Las unidades y POIs que declara el escenario."""
    return {
        "units": [u.id for u in sc.units],
        "pois": [p.id for p in sc.pois],
    }


BASELINE = Variation()
"""El run tal cual está commiteado. Es el valor por defecto de `build` y el candado de
`test_es_determinista`: construir sin variación tiene que dar los mismos bytes."""


def build(sc: Scenario, *, var: Variation = BASELINE) -> list[Event]:
    """Seis minutos de incendio, con la llamada entrante del 03:30 como clímax.

    `var` es el mismo guion salido mejor o peor, para los runs sintéticos del H5. Con
    `Variation()` —el valor por defecto— produce **byte a byte**
    `fixtures/run_fake_v3.jsonl`.
    """
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

    # El viento sopla O→E hasta el inject de t=150: el fuego avanza en +x. Hasta dónde
    # llega es lo que distingue un run bueno de uno malo (`Variation.spread`).
    for i in range(1, var.spread + 1):
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
        rationale=(
            f"Pueblo A a sotavento con {CIV_A_IMMOBILE} inmóviles: "
            "evacuar antes que contener."
        ),
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
        run_id=var.run_id,
        created_t=6.5,
        policy=policy1,
        assignments=[
            Assignment(
                unit_id=TRUCK1,
                task_id=TASK_EVAC_A,
                route=ROUTE_EVAC_A,
                eta_s=94.0,
                cost=12.4,
            ),
            Assignment(
                unit_id=TRUCK2,
                task_id=TASK_EXTINGUISH,
                route=ROUTE_EXTINGUISH,
                eta_s=70.0,
                cost=18.1,
            ),
        ],
        unassigned_tasks=[TASK_NOTIFY_B],
        context=PlanContext(
            assumptions=[
                Assumption(key=f"{ROAD_CUT}:open", expected=True, weight=1.0),
                Assumption(key=f"poi:{PUEBLO_A}:immobile", expected=CIV_A_IMMOBILE, weight=0.8),
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
            action_id="act_0001", verb="goto", args={"unit_id": TRUCK1, "to": ROUTE_EVAC_A[-1]}
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
        UnitStatusChanged(unit_id=TRUCK1, status="moving", reason=f"goto {ROUTE_EVAC_A[-1]}"),
        "sim",
        causes=("act1",),
    )

    # --- El camión va a Pueblo A y llega ---------------------------------------
    _leg(
        tl,
        TRUCK1,
        ROUTE_EVAC_A,
        12.0,
        100.0,
        rng,
    )
    tl.add(
        101.0,
        EventType.WORLD_UNIT_ARRIVED,
        UnitArrived(unit_id=TRUCK1, waypoint_id=ROUTE_EVAC_A[-1]),
        "sim",
        label="arrived1",
    )
    tl.add(
        101.5,
        EventType.ACTION_COMPLETED,
        ActionCompleted(action_id="act_0001", result={"waypoint_id": ROUTE_EVAC_A[-1]}),
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
    _leg(tl, TRUCK2, ROUTE_EXTINGUISH, 12.0, 80.0, rng)
    _leg(tl, DRONE, ROUTE_RECON, 20.0, 120.0, rng, step=12.0)

    # --- 00:40 · la llamada al agente: nos dicta la orden -------------------------------------------
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
                people_immobile=CIV_A_IMMOBILE,
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
            value=CIV_A_IMMOBILE,
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
        CiviliansChanged(group_id=CIV_A, count=CIV_A_COUNT, state="warned", poi_id=PUEBLO_A),
        "sim",
        causes=("callend",),
    )
    tl.add(
        110.0,
        EventType.WORLD_CIVILIANS_CHANGED,
        CiviliansChanged(group_id=CIV_A, count=CIV_A_COUNT, state="evacuating", poi_id=PUEBLO_A),
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
        (218.0, 0.41, ["wind:bearing_deg", f"{ROAD_CUT}:open"]),
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
        RoadChanged(edge_id=ROAD_CUT, cut=True, cause="árbol caído"),
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

    # --- 03:30 · el clímax: llamada del vecino → replan --------------------------
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
            ("caller", "Soy el jefe de bomberos, estoy en el desvío sur."),
            ("caller", "La pista sur está cortada por un árbol, no pasa nadie."),
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
    # Humalike lee al interlocutor mientras habla (`call.affect`): miedo alto cuando cuenta
    # lo del árbol, alivio cuando el agente le dice que ya hay unidad en camino.
    for t, emotions, risk in [
        (216.0, [Emotion(type="fear", intensity=0.8)], 0.45),
        (224.0, [Emotion(type="fear", intensity=0.7), Emotion(type="frustration", intensity=0.2)], 0.3),
        (235.0, [Emotion(type="relief", intensity=0.6)], 0.1),
    ]:
        tl.add(
            t,
            EventType.CALL_AFFECT,
            CallAffect(call_id=CALL_IN, emotions=emotions, risk=risk),
            "voice",
            causes=("inbound",),
        )
    # El hecho llega DURANTE la llamada (el tool `report_fact` del agente), no al colgar:
    # en el flujo de P3 el replan no espera al final de la conversación. La llamada
    # cuelga más abajo, cuando el agente ya ha dicho la señal.
    tl.add(
        229.0,
        EventType.WORLD_FACT_ASSERTED,
        FactAsserted(
            key=f"{ROAD_CUT}:cut",
            value=True,
            confidence=0.93,
            source=f"call:{CALL_IN}",
            severity="critical",
        ),
        "voice",
        label="fact_road",
        causes=("inbound",),
    )
    tl.add(
        229.5,
        EventType.PLAN_VIOLATION,
        Violation(
            verifier="route_feasible",
            severity="hard",
            message=f"la ruta de {AMBULANCE} cruza {ROAD_CUT}, cortado",
            involved=[AMBULANCE, ROAD_CUT, TASK_NOTIFY_B],
        ),
        "core",
        label="violation",
        causes=("fact_road",),
    )
    tl.add(
        230.0,
        EventType.PLAN_REPLAN_STARTED,
        ReplanStarted(
            reason="pista sur cortada, confirmado por la llamada del vecino",
            trigger="hard_constraint_violation",
        ),
        "core",
        label="replan2",
        causes=("violation", "div_218"),
    )
    policy2 = Policy(
        rationale="Sur cortado y viento girado: todo por el norte y proteger el refugio.",
        weights={
            "life_safety": 0.5,
            "response_time": 0.2,
            "structure_protection": 0.2,
            "containment": 0.1,
        },
        hard_constraints=[
            "no_unit_into_burning_cell",
            "no_civilian_route_through:wp_sur_01",
            "reserve_capability:transport:1",
        ],
        horizon_s=420,
        notify=[
            NotifyIntent(
                poi_id=PUEBLO_B,
                audience="official",
                message_intent="aviso de corte y desvío por el norte",
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
        run_id=var.run_id,
        created_t=231.5,
        policy=policy2,
        assignments=[
            Assignment(
                unit_id=TRUCK1,
                task_id=TASK_EVAC_A,
                route=[ROUTE_EVAC_A[-1]],
                eta_s=0.0,
                cost=3.2,
            ),
            Assignment(
                unit_id=AMBULANCE,
                task_id=TASK_NOTIFY_B,
                route=ROUTE_NOTIFY_B,
                eta_s=88.0,
                cost=21.7,
            ),
        ],
        unassigned_tasks=[TASK_EXTINGUISH],  # truck2 averiado: se enseña sin cubrir
        context=PlanContext(
            assumptions=[
                Assumption(key=f"{ROAD_CUT}:open", expected=False, weight=1.0),
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
            args={"unit_id": AMBULANCE, "to": ROUTE_NOTIFY_B[-1]},
        ),
        "core",
        label="act4",
        causes=("plan2",),
    )
    tl.add(
        234.0,
        EventType.WORLD_UNIT_STATUS,
        UnitStatusChanged(unit_id=AMBULANCE, status="moving", reason=f"goto {ROUTE_NOTIFY_B[-1]}"),
        "sim",
        causes=("act4",),
    )
    # El core pide una señal a la sesión abierta y `voice` la publica en HappyRobot: el
    # agente se lo dice al vecino sin colgar. El replan (230-232) no esperó a nada de esto.
    tl.add(
        232.5,
        EventType.CALL_SIGNAL_REQUESTED,
        SignalRequested(
            call_id=CALL_IN,
            key="unit_dispatched",
            payload={"unit": "ambulancia", "route": "desvío norte", "eta_s": 88},
        ),
        "core",
        label="sigreq",
        causes=("plan2",),
    )
    tl.add(
        233.4,
        EventType.CALL_SIGNAL_SENT,
        SignalSent(
            call_id=CALL_IN,
            key="unit_dispatched",
            signal_id="sig_0001",
            message="La ambulancia sale por el desvío norte, llega en minuto y medio.",
            latency_ms=900.0,
            refined=True,
        ),
        "voice",
        label="sigsent",
        causes=("sigreq",),
    )
    for i, (who, text) in enumerate(
        [
            ("agent", "La ambulancia sale por el desvío norte, llega en minuto y medio."),
            ("caller", "Perfecto, gracias. Aquí seguimos."),
        ]
    ):
        tl.add(
            234.0 + i * 2.5,
            EventType.CALL_TRANSCRIPT_PARTIAL,
            TranscriptPartial(call_id=CALL_IN, speaker=who, text=text),
            f"call:{CALL_IN}",
            causes=("sigsent",),
        )
    # Cuelga cuando ya se ha dicho todo. Al final de la llamada llega la transcripción
    # completa y `fenic` la extrae como red de seguridad: los hechos ya estaban dados.
    tl.add(
        238.0,
        EventType.CALL_ENDED,
        CallResult(
            call_id=CALL_IN,
            task_id=None,
            direction="inbound",
            started_t=212.0,
            ended_t=238.0,
            outcome="hung_up",
            transcript=(
                "Jefe de bomberos: la pista sur está cortada por un árbol, no pasa nadie. "
                "Agente: la ambulancia sale por el desvío norte, llega en minuto y medio. "
                "Jefe de bomberos: perfecto, gracias."
            ),
            facts=CallFacts(
                location_hint="desvío sur",
                road_blocked=ROAD_CUT,
                contradicts_known=True,
                urgency="critical",
                confidence=0.93,
            ),
        ),
        f"call:{CALL_IN}",
        label="inboundend",
        causes=("inbound", "sigsent"),
    )
    _leg(
        tl,
        AMBULANCE,
        ROUTE_NOTIFY_B,
        236.0,
        320.0,
        rng,
    )
    tl.add(
        321.0,
        EventType.WORLD_UNIT_ARRIVED,
        UnitArrived(unit_id=AMBULANCE, waypoint_id=ROUTE_NOTIFY_B[-1]),
        "sim",
        causes=("act4",),
    )
    tl.add(
        322.0,
        EventType.ACTION_COMPLETED,
        ActionCompleted(action_id="act_0004", result={"waypoint_id": ROUTE_NOTIFY_B[-1]}),
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
            args={"poi_id": PUEBLO_B, "text": "desvío por el norte"},
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
            action_id="act_0006", verb="rescue", args={"group_id": CIV_A, "count": CIV_A_IMMOBILE}
        ),
        "core",
        label="act6",
        causes=("override",),
    )
    tl.add(
        290.0,
        EventType.ACTION_COMPLETED,
        ActionCompleted(action_id="act_0006", result={"rescued": CIV_A_IMMOBILE}),
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
        CiviliansChanged(group_id=CIV_A, count=CIV_A_COUNT, state="safe", poi_id=PUEBLO_A),
        "sim",
    )
    tl.add(
        332.0,
        EventType.WORLD_CIVILIANS_CHANGED,
        CiviliansChanged(group_id=CIV_B, count=CIV_B_COUNT, state="warned", poi_id=PUEBLO_B),
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
        RunEnded(scenario_id=sc.id, score=var.final_score),
        "core",
    )

    if not var.answered:
        _llamada_sin_respuesta(tl)

    return tl.events(var.run_id)


def _llamada_sin_respuesta(tl: Timeline) -> None:
    """02:20 · la llamada de la orden que no se completa, y el humano que suple lo que no se supo.

    Es literalmente el guion de `docs/interfaces.md`: «llamada sin respuesta en
    45 s: `outcome="no_answer"`, el core reintenta una vez y después escala a
    `human.override`». Está aquí porque el fixture del H3 no tenía **ninguna** llamada
    con `facts=None`, que es justo el caso que el panel de llamadas no puede romper —y
    el que, sin fixture, se descubre el domingo por la mañana.

    Va en el hueco tranquilo entre los injects (t=150) y el clímax (t=212): no se pisa
    con nada y deja ver el `SIN EXTRAER` con la pantalla entera para él.
    """
    tl.add(
        140.0,
        EventType.CALL_REQUESTED,
        CallRequest(
            task_id=TASK_NOTIFY_B,
            poi_id=PUEBLO_B,
            to="+34600333444",
            audience="resident",
            intent="status_check",
            urgency="medium",
            facts={"poi_name": "Pueblo B"},
            expect=["confirmation", "headcount"],
        ),
        "core",
        label="callreq_b",
    )
    tl.add(
        142.0,
        EventType.CALL_STARTED,
        CallStarted(
            call_id=CALL_NO_ANSWER,
            task_id=TASK_NOTIFY_B,
            to="+34600333444",
            direction="outbound",
        ),
        f"call:{CALL_NO_ANSWER}",
        label="callstart_b",
        causes=("callreq_b",),
    )
    tl.add(
        187.0,  # 45 s de tono, como dice el contrato
        EventType.CALL_ENDED,
        CallResult(
            call_id=CALL_NO_ANSWER,
            task_id=TASK_NOTIFY_B,
            direction="outbound",
            started_t=142.0,
            ended_t=187.0,
            outcome="no_answer",
            transcript="[sin contestar · 45 s de tono]",
            facts=None,  # la extracción no tiene nada que extraer
        ),
        f"call:{CALL_NO_ANSWER}",
        label="callend_b",
        causes=("callstart_b",),
    )
    tl.add(
        195.0,
        EventType.HUMAN_OVERRIDE,
        HumanOverride(
            kind="assert_fact",
            target=f"civilians:{PUEBLO_B}:warned",
            value=True,
            note="Pueblo B confirma por radio de la Guardia Civil; el teléfono no da",
        ),
        "human",
        label="override_b",
        causes=("callend_b",),
    )


# --- Salida y validación --------------------------------------------------------


def write(events: list[Event], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    # Una línea JSON por evento, \n siempre (no \r\n): el fichero se compara por hash
    # y Windows no puede cambiarlo.
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for ev in events:
            fh.write(ev.model_dump_json() + "\n")


def write_runs(sc: Scenario, count: int, directory: Path) -> list[Path]:
    """N runs del mismo guion, de peor a mejor, en `runs/`.

    Es lo que me deja construir la pantalla partida del run 1 vs run 12 sin esperar a que
    Carlos corra los doce el domingo de madrugada. **No entran en `fixtures/`**: `runs/`
    es salida, está gitignored, y cada uno de estos lleva `run_fake` en el `run_id`, que
    es lo que hace que `/api/runs` los marque *sintéticos* en pantalla.

    Se escriben en orden para que el `mtime` cuente la misma historia que la calidad: el
    primero es el más viejo y el peor, como el run 1 de verdad.
    """
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for i in range(count):
        # Con un solo run, el peor; con varios, de 0 a 1 repartido.
        quality = i / (count - 1) if count > 1 else 0.0
        var = Variation(run_id=f"run_fake_{i + 1:02d}", quality=quality)
        events = build(sc, var=var)
        path = directory / f"{var.run_id}.jsonl"
        write(events, path)
        errors = validate(path)
        if errors:
            raise SystemExit(f"{path}: {len(errors)} errores · {errors[0]}")
        written.append(path)
        print(
            f"✓ {path} · calidad {quality:.2f} · {len(events)} eventos · "
            f"score {var.final_score}"
        )
    return written


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
    ap.add_argument("--out", type=Path, default=OUT, help="por defecto, el fixture v3")
    ap.add_argument("--scenario", type=Path, default=SCENARIO)
    ap.add_argument(
        "--validate",
        action="store_true",
        help="no genera: revalida el fichero que ya existe",
    )
    ap.add_argument(
        "--runs",
        type=int,
        default=0,
        help="N runs sintéticos de peor a mejor, para el run 1 vs run 12 del H5",
    )
    ap.add_argument(
        "--dir",
        type=Path,
        default=Path("runs"),
        help="dónde escribir los runs de --runs. `fixtures/` no se toca nunca",
    )
    args = ap.parse_args()
    out: Path = args.out

    if args.runs:
        sc = Scenario.model_validate(
            yaml.safe_load(args.scenario.read_text(encoding="utf-8"))
        )
        write_runs(sc, args.runs, args.dir)
        return

    if args.validate:
        if not out.exists():
            raise SystemExit(f"no existe {out}: genéralo primero")
        errors = validate(out)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        if errors:
            raise SystemExit(f"{len(errors)} errores en {out}")
        lines = sum(1 for _ in out.open(encoding="utf-8"))
        print(f"✓ {out} · {lines} eventos · {len(PAYLOAD_MODELS)} tipos del catálogo")
        return

    sc = Scenario.model_validate(yaml.safe_load(args.scenario.read_text(encoding="utf-8")))
    ents = _entities(sc)
    events = build(sc)
    write(events, out)
    errors = validate(out)
    for e in errors:
        print(f"  ✗ {e}", file=sys.stderr)
    if errors:
        raise SystemExit(f"{len(errors)} errores: el fixture no sirve")
    types = len({ev.type for ev in events})
    print(
        json.dumps(
            {
                "out": str(out),
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
