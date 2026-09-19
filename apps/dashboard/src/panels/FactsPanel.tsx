// Hechos: la foto de lo que hay AHORA, no un log (SPEC-008 REQ-311). Contesta «¿dónde
// está todo?» mientras *Qué ha cambiado* contesta «¿qué ha pasado?».
//
// No deriva nada nuevo. Lo del mundo sale de `useWorldView`, la misma vista que pinta el
// mapa (se calcula una vez en `App`), así que panel y mapa no pueden contar dos verdades.
// Los hechos son una sola lista (SPEC-009 REQ-330): los `world.fact.asserted` llegados
// después del snapshot, y detrás los de `state.facts` (el snapshot, ya plegado por
// `belief`). Se concatenan SIN fusionar por clave: fusionarlos o recalcular confianzas
// sería rehacer `belief` en TypeScript, y eso es de P1.
import type { CellState, Event, Fact, FactAsserted, VelaEvent, WorldState } from '../types'
import type { WorldView } from '../hooks/useWorldView'
import { Empty, Panel, Row, Rows, Section, Skeleton, type DataTone } from '../components/Panel'
import { factValue, mmss, pct, seconds, shortId } from '../story/format'
import { CELL_STATE, CIV_STATE, UNIT_KIND, UNIT_STATUS } from '../story/labels'

/** Las celdas que cuentan la emergencia. `intact` y `dark` no: una es el fondo y la otra
 *  es del escenario de apagón, que no se cuenta como fuego (REQ-311). */
const COUNTED_CELLS: CellState[] = ['burning', 'at_risk', 'burnt', 'flooded']

/** Invariante 8 sin color (REQ-323): un hecho asumido nunca se disfraza de observado. */
const FACT_KIND: Record<Fact['kind'], { className: string; tag: string | null }> = {
  observed: { className: 'text-vela-ink', tag: null },
  inferred: { className: 'text-vela-dim', tag: 'inferido' },
  assumed_default: { className: 'text-vela-dim italic', tag: 'asumido' },
}

/** Civiles: atrapados es lo urgente y a salvo lo resuelto (REQ-321); lo demás, gris. */
const CIV_TONE: Partial<Record<keyof typeof CIV_STATE, DataTone>> = {
  trapped: 'urgent',
  safe: 'done',
}

export function FactsPanel({
  state,
  events,
  worldView,
  awaitingSnapshot,
}: {
  state: WorldState | null
  events: Event[]
  worldView: WorldView
  awaitingSnapshot: boolean
}) {
  const units = [...worldView.units.values()].sort((a, b) => a.id.localeCompare(b.id))
  const cutRoads = [...worldView.cutRoads.entries()]
  const civilians = [...worldView.civilians.values()].sort((a, b) => a.id.localeCompare(b.id))
  const cellCounts = countCells(worldView.cells)
  // Lo llegado después primero, y dentro de cada tramo lo último arriba.
  const facts = [
    ...factsSinceSnapshot(events),
    ...[...(state?.facts ?? [])]
      .sort((a, b) => b.t_sim - a.t_sim)
      .map((fact) => ({ id: `${fact.key}|${fact.source}|${fact.t_sim}`, t_sim: fact.t_sim, fact })),
  ]
  const nothing = !state && worldView.applied === 0

  const operativas = units.filter((unit) => unit.status !== 'unavailable').length
  const ardiendo = cellCounts.find(([cellState]) => cellState === 'burning')?.[1] ?? 0
  const atrapados = civilians
    .filter((group) => group.state === 'trapped')
    .reduce((sum, group) => sum + group.count, 0)

  return (
    <Panel
      title="Hechos"
      subtitle={`${facts.length} ${facts.length === 1 ? 'hecho' : 'hechos'}`}
      stats={[
        { label: 'unidades operativas', value: `${operativas}/${units.length}` },
        { label: 'celdas ardiendo', value: ardiendo },
        { label: 'cortes', value: cutRoads.length },
        { label: 'atrapados', value: atrapados, tone: atrapados ? 'urgent' : undefined },
      ]}
    >
      {awaitingSnapshot ? (
        <Skeleton rows={6} />
      ) : nothing ? (
        <Empty>Aquí aparece dónde está cada unidad, qué arde, qué está cortado y qué se sabe.</Empty>
      ) : (
        <>
          <Section title="Unidades" n={units.length} empty="Sin unidades en el escenario.">
            <Rows>
              {units.map((unit) => {
                const kind = state?.units[unit.id]?.kind
                return (
                  // Sin coordenadas (REQ-330): dónde está lo dice el mapa.
                  <Row
                    key={unit.id}
                    primary={`${shortId(unit.id)}${kind ? ` · ${UNIT_KIND[kind]}` : ''}`}
                    meta={unit.etaS !== null ? `llega en ${seconds(unit.etaS)}` : undefined}
                    status={UNIT_STATUS[unit.status]}
                    tone={unit.status === 'unavailable' ? 'urgent' : undefined}
                    hint={unit.id}
                  />
                )
              })}
            </Rows>
          </Section>

          <Section title="Carreteras cortadas" n={cutRoads.length} empty="Sin cortes.">
            <Rows>
              {cutRoads.map(([edgeId, cause]) => (
                <Row key={edgeId} primary={edgeId} meta={cause ?? undefined} />
              ))}
            </Rows>
          </Section>

          <Section
            title="Fuego y viento"
            n={cellCounts.length + (worldView.wind ? 1 : 0)}
            empty="Sin fuego."
          >
            <Rows>
              {cellCounts.map(([cellState, n]) => (
                <Row
                  key={cellState}
                  primary={`${n} ${n === 1 ? 'celda' : 'celdas'} ${CELL_STATE[cellState]}`}
                />
              ))}
              {worldView.wind && (
                <Row
                  primary={`Viento ${Math.round(worldView.wind.bearing_deg)}° · ${worldView.wind.speed.toFixed(1)}`}
                />
              )}
            </Rows>
          </Section>

          <Section title="Civiles" n={civilians.length} empty="Sin grupos de civiles.">
            <Rows>
              {civilians.map((group) => (
                <Row
                  key={group.id}
                  primary={shortId(group.poiId)}
                  meta={`${group.count} ${group.count === 1 ? 'persona' : 'personas'}`}
                  status={CIV_STATE[group.state]}
                  tone={CIV_TONE[group.state]}
                />
              ))}
            </Rows>
          </Section>

          <Section title="Lo que se sabe" n={facts.length} empty="Nada asertado todavía.">
            <Rows>
              {facts.map(({ id, t_sim, fact }) => (
                <FactRow key={id} t_sim={t_sim} fact={fact} />
              ))}
            </Rows>
          </Section>
        </>
      )}
    </Panel>
  )
}

function FactRow({ t_sim, fact }: { t_sim: number; fact: FactAsserted }) {
  const kind = FACT_KIND[fact.kind]
  return (
    <Row
      time={mmss(t_sim)}
      primary={
        <span className={kind.className}>
          {fact.key} = {factValue(fact.value)}
        </span>
      }
      status={pct(fact.confidence)}
      // Ningún hecho se pinta sin su procedencia (REQ-090).
      meta={`${fact.source}${kind.tag ? ` · ${kind.tag}` : ''}`}
    />
  )
}

/** Los `world.fact.asserted` posteriores al snapshot, lo último arriba. `events` se vacía
 *  con cada snapshot y el recorte del hook nunca tira un hecho, así que todos los que hay
 *  son posteriores: no hace falta comparar `seq`. */
function factsSinceSnapshot(events: Event[]): { id: string; t_sim: number; fact: FactAsserted }[] {
  const out: { id: string; t_sim: number; fact: FactAsserted }[] = []
  for (const envelope of events) {
    if (envelope.type !== 'world.fact.asserted') continue
    const ev = envelope as unknown as VelaEvent
    if (ev.type === 'world.fact.asserted') {
      out.push({ id: `seq${ev.seq}`, t_sim: ev.t_sim, fact: ev.payload })
    }
  }
  return out.reverse()
}

function countCells(cells: Map<string, CellState>): [CellState, number][] {
  const counts = new Map<CellState, number>()
  for (const cellState of cells.values()) counts.set(cellState, (counts.get(cellState) ?? 0) + 1)
  return COUNTED_CELLS.flatMap((cellState) => {
    const n = counts.get(cellState) ?? 0
    return n ? [[cellState, n] as [CellState, number]] : []
  })
}
