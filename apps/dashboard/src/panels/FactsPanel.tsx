// Hechos: la foto de lo que hay AHORA, no un log (SPEC-008 REQ-311). Contesta «¿dónde
// está todo?» mientras *Qué ha cambiado* contesta «¿qué ha pasado?».
//
// No deriva nada nuevo. Lo del mundo sale de `useWorldView`, la misma vista que pinta el
// mapa (se calcula una vez en `App`), así que panel y mapa no pueden contar dos verdades.
// Los hechos son dos bloques: los de `state.facts` (el snapshot, ya plegado por `belief`) y
// los `world.fact.asserted` llegados después, LISTADOS tal cual. Fusionarlos por clave o
// recalcular confianzas sería rehacer `belief` en TypeScript, y eso es de P1.
import { useRef, type ReactNode } from 'react'

import type { CellState, Event, Fact, FactAsserted, VelaEvent, WorldState } from '../types'
import type { WorldView } from '../hooks/useWorldView'
import { Empty, Panel, Skeleton } from '../components/Panel'
import { factValue, pct, seconds, shortId } from '../story/format'
import { CELL_STATE, CIV_STATE, UNIT_KIND, UNIT_STATUS } from '../story/labels'

/** Las celdas que cuentan la emergencia. `intact` y `dark` no: una es el fondo y la otra
 *  es del escenario de apagón, que no se cuenta como fuego (REQ-311). */
const COUNTED_CELLS: CellState[] = ['burning', 'at_risk', 'burnt', 'flooded']

/** Invariante 8: un hecho asumido nunca se disfraza de observado. */
const FACT_KIND: Record<Fact['kind'], { className: string; tag: string | null }> = {
  observed: { className: 'text-vela-ink', tag: null },
  inferred: { className: 'text-vela-dim', tag: 'inferido' },
  assumed_default: { className: 'text-vela-dim italic', tag: 'asumido' },
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
  // Lo último arriba, como en los demás paneles.
  const facts = [...(state?.facts ?? [])].sort((a, b) => b.t_sim - a.t_sim)
  const later = factsSinceSnapshot(events)
  const nothing = !state && worldView.applied === 0

  // `state.seq` NO es el del snapshot: `useEventStream` lo pone al día con cada evento. Lo
  // que solo cambia con un snapshot es la referencia de `state.facts` (la copia por evento
  // la conserva), así que el `seq` se anota cuando esa referencia cambia.
  const snapshot = useRef<{ facts: Fact[]; seq: number } | null>(null)
  if (state && snapshot.current?.facts !== state.facts) {
    snapshot.current = { facts: state.facts, seq: state.seq }
  }

  return (
    <Panel
      title="Hechos"
      count={facts.length + later.length}
      note={state && snapshot.current ? `al snapshot seq ${snapshot.current.seq}` : undefined}
    >
      {awaitingSnapshot ? (
        <Skeleton rows={6} />
      ) : nothing ? (
        <Empty>Aquí aparece dónde está cada unidad, qué arde, qué está cortado y qué se sabe.</Empty>
      ) : (
        <div className="flex flex-col gap-4">
          <Section title="Unidades" n={units.length}>
            {units.map((unit) => {
              const kind = state?.units[unit.id]?.kind
              return (
                <li key={unit.id}>
                  <span className="text-vela-ink">{shortId(unit.id)}</span>
                  {kind && <span> · {UNIT_KIND[kind]}</span>}
                  <span className={unit.status === 'unavailable' ? ' text-vela-warn' : ''}>
                    {' '}· {UNIT_STATUS[unit.status]}
                  </span>
                  <span className="tabular-nums">
                    {' '}· ({Math.round(unit.x)}, {Math.round(unit.z)})
                    {unit.etaS !== null && ` · llega en ${seconds(unit.etaS)}`}
                  </span>
                </li>
              )
            })}
          </Section>

          <Section title="Carreteras cortadas" n={cutRoads.length}>
            {cutRoads.map(([edgeId, cause]) => (
              <li key={edgeId}>
                <span className="text-vela-ink">{edgeId}</span> · {cause ?? 'sin causa'}
              </li>
            ))}
          </Section>

          <Section title="Fuego y viento" n={cellCounts.length + (worldView.wind ? 1 : 0)}>
            {cellCounts.map(([cellState, n]) => (
              <li key={cellState}>
                <span className="tabular-nums text-vela-ink">{n}</span>{' '}
                {n === 1 ? 'celda' : 'celdas'} {CELL_STATE[cellState]}
              </li>
            ))}
            {worldView.wind && (
              <li className="tabular-nums">
                viento {Math.round(worldView.wind.bearing_deg)}° · {worldView.wind.speed.toFixed(1)}
              </li>
            )}
          </Section>

          <Section title="Civiles" n={civilians.length}>
            {civilians.map((group) => (
              <li key={group.id}>
                <span className="text-vela-ink">{shortId(group.poiId)}</span>
                <span className="tabular-nums"> · {group.count}</span>
                <span className={group.state === 'trapped' ? ' text-vela-warn' : ''}>
                  {' '}· {CIV_STATE[group.state]}
                </span>
              </li>
            ))}
          </Section>

          <Section title="Lo que se sabe · del snapshot" n={facts.length}>
            {facts.map((fact) => (
              // Clave + fuente: la misma clave puede llegar de dos sitios y las dos cuentan.
              <FactLine key={`${fact.key}|${fact.source}|${fact.t_sim}`} fact={fact} />
            ))}
          </Section>

          <Section title="Llegados después · sin plegar por belief" n={later.length}>
            {later.map(({ seq, fact }) => (
              <FactLine key={seq} fact={fact} />
            ))}
          </Section>
        </div>
      )}
    </Panel>
  )
}

function FactLine({ fact }: { fact: FactAsserted }) {
  const kind = FACT_KIND[fact.kind]
  return (
    <li className={kind.className}>
      {fact.key} = {factValue(fact.value)} · {pct(fact.confidence)}
      {kind.tag && ` · ${kind.tag}`}
      {/* Ningún hecho se pinta sin su procedencia (REQ-090). */}
      <span className="block text-xs text-vela-dim not-italic">{fact.source}</span>
    </li>
  )
}

/** Los `world.fact.asserted` posteriores al snapshot, lo último arriba. `events` se vacía
 *  con cada snapshot y el recorte del hook nunca tira un hecho, así que todos los que hay
 *  son posteriores: no hace falta comparar `seq`. */
function factsSinceSnapshot(events: Event[]): { seq: number; fact: FactAsserted }[] {
  const out: { seq: number; fact: FactAsserted }[] = []
  for (const envelope of events) {
    if (envelope.type !== 'world.fact.asserted') continue
    const ev = envelope as unknown as VelaEvent
    if (ev.type === 'world.fact.asserted') out.push({ seq: ev.seq, fact: ev.payload })
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

/** Una sección vacía se dice, no desaparece: que no haya cortes es un dato. */
function Section({ title, n, children }: { title: string; n: number; children: ReactNode }) {
  return (
    <section>
      <h3 className="mb-1 flex items-baseline gap-2 text-xs font-bold tracking-wide text-vela-accent">
        {title}
        <span className="font-normal tabular-nums text-vela-dim">{n}</span>
      </h3>
      {n === 0 ? (
        <p className="text-sm text-vela-dim">nada por ahora</p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm">{children}</ul>
      )}
    </section>
  )
}
