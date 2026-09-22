// El mapa 2D de la simulación: un `<svg>` en coordenadas del mundo.
//
// No hay transformación: el `viewBox` está en unidades de Minecraft (x a la derecha,
// z hacia abajo, que es el sur), así que un clic se convierte a mundo con la matriz
// del propio SVG y nada más. Todo lo que se dibuja mide múltiplos de `stroke`.
//
// El encuadre es FIJO y se calcula del escenario: el fuego no puede salir de la caja
// quemable, así que no hace falta la histéresis del mapa antiguo.
import { useMemo, useRef } from 'react'

import type { Plan, Scenario, Waypoint } from '../types'

import { poiThreat } from '../geo/problems'
import type { Frame } from './engine/loop'
import {
  AssignmentsLayer,
  CellsLayer,
  IgnitionLayer,
  MapDefs,
  PendingRoutesLayer,
  PoisLayer,
  RoadsLayer,
  SmokeLayer,
  TargetsLayer,
  ThreatLayer,
  UnitsLayer,
  WindField,
  WindLegend,
} from './map/layers'
import { boxOf, cellIdAt, geoOf, strokeFor, viewBoxAttr, waypointMap } from './map/project'
import type { WorldView } from './worldview'

/** Margen alrededor de la geografía. El mismo `BURNABLE_MARGIN_M` de `sim/runner.py`
 *  más un respiro, porque fuera de la caja quemable el fuego no avanza. */
const FRAME_PAD_M = 40

export interface MapTarget {
  key: string
  waypointId: string
  label: string
}

export function SimMap({
  frame,
  view,
  scenario,
  speed,
  tool,
  focus,
  onPickCell,
  onFocus,
}: {
  frame: Frame
  view: WorldView
  scenario: Scenario
  speed: number
  tool: 'none' | 'ignite' | 'douse'
  focus: string | null
  onPickCell: (cellId: string) => void
  onFocus: (key: string | null) => void
}) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const geo = useMemo(() => geoOf(scenario), [scenario])
  const waypoints = useMemo(() => waypointMap(scenario.waypoints), [scenario])

  // Encuadre fijo: toda la geografía del escenario más el margen quemable.
  const box = useMemo(
    () =>
      boxOf(
        [
          ...scenario.waypoints.map((w) => ({ x: w.x, z: w.z })),
          ...scenario.pois.map((p) => ({ x: p.x, z: p.z })),
        ],
        FRAME_PAD_M,
      ),
    [scenario],
  )
  const stroke = strokeFor(box)

  const threats = useMemo(() => {
    const out = new Map<string, ReturnType<typeof poiThreat>>()
    for (const poi of scenario.pois) out.set(poi.id, poiThreat(poi, view, geo))
    return out
  }, [scenario, view, geo])

  const units = useMemo(() => [...view.units.values()], [view])
  const civilians = useMemo(() => [...view.civilians.values()], [view])

  const targets = useMemo<MapTarget[]>(() => mapTargets(frame.plan, waypoints), [frame.plan, waypoints])
  const pending = useMemo(
    () => [...frame.pendingRoutes.entries()].map(([unitId, route]) => ({ unitId, route })),
    [frame.pendingRoutes],
  )

  const pick = (ev: React.MouseEvent<SVGSVGElement>) => {
    if (tool === 'none') return
    const svg = svgRef.current
    const ctm = svg?.getScreenCTM()
    if (!svg || !ctm) return
    // `getScreenCTM` ya tiene en cuenta el letterboxing de `xMidYMid meet`.
    const p = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(ctm.inverse())
    onPickCell(cellIdAt(p.x, p.y, geo))
  }

  return (
    <svg
      ref={svgRef}
      viewBox={viewBoxAttr(box)}
      preserveAspectRatio="xMidYMid meet"
      onClick={pick}
      onMouseLeave={() => onFocus(null)}
      // `vela-map-light`: este mapa se pinta en claro y redefine ahí sus colores.
      className={`vela-map-light h-full w-full rounded-[10px] bg-vela-ground ${tool === 'none' ? '' : 'cursor-crosshair'}`}
      // La interpolación del movimiento dura lo que dura un tick de pared.
      style={{ ['--vela-tick' as string]: `${1 / speed}s` }}
      role="img"
      aria-label="Mapa de la simulación"
    >
      <MapDefs stroke={stroke} cellSize={geo.cellSize} />
      <CellsLayer cells={view.cells} geo={geo} />
      <WindField wind={view.wind} box={box} stroke={stroke} />
      <SmokeLayer cells={view.cells} geo={geo} wind={view.wind} />
      <IgnitionLayer cells={frame.ignitions} states={view.cells} geo={geo} stroke={stroke} />
      <RoadsLayer roads={scenario.roads} waypoints={waypoints} cutRoads={view.cutRoads} stroke={stroke} />
      <ThreatLayer pois={scenario.pois} threats={threats} stroke={stroke} />
      <PoisLayer pois={scenario.pois} civilians={civilians} stroke={stroke} />
      <PendingRoutesLayer routes={pending} waypoints={waypoints} stroke={stroke} />
      <AssignmentsLayer plan={frame.plan} waypoints={waypoints} stroke={stroke} />
      <TargetsLayer targets={targets} waypoints={waypoints} stroke={stroke} focus={focus} />
      <UnitsLayer units={units} stroke={stroke} />
      {view.wind && <WindLegend wind={view.wind} box={box} stroke={stroke} />}
    </svg>
  )
}

/** Un punto rojo por asignación, en el último waypoint de su ruta. */
function mapTargets(plan: Plan | null, waypoints: Map<string, Waypoint>): MapTarget[] {
  if (!plan) return []
  const out: MapTarget[] = []
  for (const a of plan.assignments) {
    const wp = a.route[a.route.length - 1]
    if (!wp || !waypoints.has(wp)) continue
    out.push({
      key: `${a.unit_id}:${a.task_id}`,
      waypointId: wp,
      label: a.unit_id.replace(/^unit_/, ''),
    })
  }
  return out
}
