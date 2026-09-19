// El mapa 2D: unidades, POIs, celdas y flechas de asignación.
// Las coordenadas llegan en (x, z) del mundo Minecraft; la proyección a píxeles
// es cosa de este panel y de nadie más.
//
// Si Minecraft falla, este panel es la demo (plan B nivel 3).
//
// Cinco piezas y este fichero solo las junta: `useScenario` trae la geometría estática
// (no viaja por eventos), `useWorldView` trae lo que se mueve, `map/project.ts` hace la
// única traducción de mundo a SVG que existe en el proyecto, `map/terrain.ts` pinta la
// imagen de satélite (ilustrativa: SPEC-006) y `map/problems.ts` decide qué zonas tienen
// un problema. Con satélite y fenómenos, el mapa es nivel 1 de verdad.
import { useMemo, useRef } from 'react'

import type { Event, Plan, WorldState } from '../types'
import { Empty, Panel } from '../components/Panel'
import { useScenario } from '../hooks/useScenario'
import { useWorldView } from '../hooks/useWorldView'
import {
  AssignmentsLayer,
  CellsLayer,
  MapDefs,
  PoisLayer,
  RoadsLayer,
  SmokeLayer,
  TerrainLayer,
  ThreatLayer,
  UnitsLayer,
  WindField,
  WindLegend,
  cellPoints,
} from '../map/layers'
import { poiThreat, problems, type PoiThreat } from '../map/problems'
import {
  boxOf,
  geoOf,
  growViewBox,
  strokeFor,
  viewBoxAttr,
  waypointMap,
  type Box,
} from '../map/project'
import { renderTerrain } from '../map/terrain'

export function MapPanel({
  state,
  plan,
  events,
  scenarioId,
  awaitingSnapshot,
}: {
  state: WorldState | null
  plan: Plan | null
  events: Event[]
  scenarioId: string | null
  awaitingSnapshot: boolean
}) {
  const layer = useScenario(scenarioId)
  const view = useWorldView(state, events)
  // El encuadre se guarda entre renders porque solo puede CRECER: si se recalculase
  // con cada posición, el mapa daría un salto por segundo (project.ts, growViewBox).
  const boxRef = useRef<Box | null>(null)

  const geo = useMemo(() => (layer ? geoOf(layer) : null), [layer])

  const box = useMemo(() => {
    if (!layer || !geo) return null
    const points = [
      ...layer.waypoints.map((wp) => ({ x: wp.x, z: wp.z })),
      ...layer.pois.map((poi) => ({ x: poi.x, z: poi.z })),
      ...[...view.units.values()].map((u) => ({ x: u.x, z: u.z })),
      ...cellPoints(view.cells, geo),
    ]
    boxRef.current = growViewBox(boxRef.current, boxOf(points, geo.cellSize * 2))
    return boxRef.current
  }, [layer, geo, view])

  const waypoints = useMemo(() => waypointMap(layer?.waypoints ?? []), [layer])

  // Una vez por escenario y cacheada (REQ-215): `renderTerrain` mira su propia caché, pero
  // el `useMemo` evita ni siquiera llamarla en cada evento.
  const terrain = useMemo(() => (layer ? renderTerrain(layer) : null), [layer])

  // El halo de cada POI y el resumen de zonas salen de la misma función (REQ-224): el mapa
  // y su lista no pueden contradecirse.
  const threats = useMemo(() => {
    const out = new Map<string, PoiThreat>()
    if (layer && geo) for (const poi of layer.pois) out.set(poi.id, poiThreat(poi, view, geo))
    return out
  }, [layer, geo, view])
  const trouble = useMemo(
    () => (layer && !awaitingSnapshot ? problems(view, layer, plan) : []),
    [layer, view, plan, awaitingSnapshot],
  )

  // Un id con datos pero sin geometría conocida no se esconde: va a un carril al pie
  // del panel. El día que el golden traiga ids distintos a los de la capa del escenario,
  // quiero verlo ahí y no descubrirlo en el pitch.
  const unlocated = useMemo(() => {
    const known = new Set(layer?.pois.map((p) => p.id))
    if (!layer) return []
    return [...view.civilians.values()].filter((c) => !known.has(c.poiId)).map((c) => c.poiId)
  }, [layer, view])

  if (!layer || !geo || !box) {
    return (
      // El vacío dice qué falta y de dónde tiene que llegar; el endpoint va al nivel
      // *registro*, que es para mí y no para la sala (REQ-193, REQ-197).
      <Panel title="Mapa" count={0} level={1} flush={false}>
        <Empty>Esperando geometría del escenario.</Empty>
        <p className="mt-1 text-xs text-vela-dim">GET /api/scenario sin responder</p>
      </Panel>
    )
  }

  const stroke = strokeFor(box)
  const units = [...view.units.values()]

  return (
    // `level={1}`: el mapa es el primer nivel de la jerarquía y los otros cinco el
    // segundo (REQ-196). Sin navegación que ordene la pantalla, el borde es lo único que
    // dice desde el fondo de la sala por dónde empezar a mirar.
    <Panel
      title="Mapa"
      count={units.length}
      level={1}
      // El chip de REQ-217: la imagen es ilustrativa y la pantalla lo dice. Sin él, un
      // relieve inventado se lee como un dato del escenario.
      note={`${layer.name} · relieve ilustrativo`}
      flush
      className="relative"
    >
      <svg
        viewBox={viewBoxAttr(box)}
        preserveAspectRatio="xMidYMid meet"
        className="h-full w-full bg-vela-ground"
      >
        <MapDefs stroke={stroke} cellSize={geo.cellSize} />
        {terrain && <TerrainLayer url={terrain.url} box={terrain.box} />}
        <CellsLayer cells={view.cells} geo={geo} />
        {/* Ambiente: estelas y humo van por debajo de todo lo que es información. */}
        <WindField wind={view.wind} box={box} stroke={stroke} />
        <SmokeLayer cells={view.cells} geo={geo} wind={view.wind} />
        <RoadsLayer
          roads={layer.roads}
          waypoints={waypoints}
          cutRoads={view.cutRoads}
          stroke={stroke}
        />
        <ThreatLayer pois={layer.pois} threats={threats} stroke={stroke} />
        <PoisLayer pois={layer.pois} civilians={[...view.civilians.values()]} stroke={stroke} />
        {/* Esperando el snapshot, el mapa se dibuja SIN lo que se mueve (REQ-202): el
            terreno es geometría cacheada y sigue siendo cierto, pero unidades y flechas
            son estado del run y todavía no se sabe dónde están. Enseñar las de antes de
            la reconexión sería pintar un mundo que quizá ya no existe. */}
        {!awaitingSnapshot && (
          <>
            <AssignmentsLayer plan={plan} waypoints={waypoints} stroke={stroke} />
            <UnitsLayer units={units} stroke={stroke} />
          </>
        )}
        {view.wind && <WindLegend wind={view.wind} box={box} stroke={stroke} />}
      </svg>

      {/* Resumen de zonas (REQ-224): lo que cuenta el mapa, por gravedad y a tamaño de sala.
          Sobre la imagen y con fondo propio, porque una lista sin él se pierde en el
          terreno. Enseña el 0 cuando no hay nada (REQ-194): «sin problemas» es un dato. */}
      <div className="absolute bottom-3 left-3 max-h-[45%] max-w-[18rem] overflow-auto rounded-[9px] border border-vela-edge bg-vela-panel/95 px-2.5 py-1.5 shadow-sm">
        <p className="text-xs font-semibold text-vela-dim">
          Zonas con problema · {trouble.length}
        </p>
        {trouble.length === 0 ? (
          <p className="text-sm text-vela-ink">Sin zonas con problema.</p>
        ) : (
          <ul className="mt-1 flex flex-col gap-1">
            {trouble.map((p) => (
              <li key={p.id} className="flex items-baseline gap-2 text-sm text-vela-ink">
                <span
                  aria-hidden
                  className={`inline-block h-2 w-2 shrink-0 rounded-full ${
                    p.level === 2 ? 'bg-vela-glow' : 'bg-vela-alert'
                  }`}
                />
                {p.text}
              </li>
            ))}
          </ul>
        )}
      </div>
      {unlocated.length > 0 && (
        <p className="absolute right-3 bottom-3 rounded bg-vela-panel/95 px-2 py-1 text-xs text-vela-warn">
          sin ubicar: {[...new Set(unlocated)].join(', ')}
        </p>
      )}
    </Panel>
  )
}
