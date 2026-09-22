// La solución del solver y su significado, con la ecuación de coste término a término.
//
// La ecuación no es decorativa: son los mismos factores que multiplicó
// `weightedCost`, con sus nombres. Si la multiplicación que se lee no da el coste que
// el solver guardó, la explicación miente.
//
// Y al señalar una fila se resalta su punto rojo en el mapa: es lo que une «la
// solución» con «lo que pasa».
import { Empty, Panel, Section } from '../../components/Panel'
import type { RoadGraph } from '../engine/graph'
import type { Frame } from '../engine/loop'
import { routeName, unitName } from '../engine/calls'
import { explainAssignment, explainUnassigned, num2 } from '../explain'

export function SolverPanel({
  frame,
  graph,
  aliases,
  focus,
  onFocus,
}: {
  frame: Frame
  graph: RoadGraph
  aliases: Record<string, string>
  focus: string | null
  onFocus: (key: string | null) => void
}) {
  const plan = frame.plan
  const assignments = plan?.assignments ?? []
  const unassigned = plan?.unassigned_tasks ?? []

  return (
    <Panel
      title="Solver"
      subtitle={
        plan
          ? `${assignments.length} ${assignments.length === 1 ? 'asignación' : 'asignaciones'}${unassigned.length > 0 ? ` · ${unassigned.length} sin cubrir` : ''}`
          : 'sin plan todavía'
      }
      stats={
        plan
          ? [
              { label: 'asignadas', value: assignments.length },
              { label: 'sin cubrir', value: unassigned.length, tone: unassigned.length > 0 ? 'urgent' : undefined },
            ]
          : undefined
      }
    >
      {!plan ? (
        <Empty>
          El reparto aparece en cuanto haya una tarea: enciende un foco y el solver asigna.
        </Empty>
      ) : (
        <>
          {/* Un medio parado tiene que decir POR QUÉ lo está, o se lee como un fallo:
              es exactamente lo que pasaba cuando la reserva no se aplicaba. */}
          <Reserve frame={frame} />
          <Section title="Reparto" n={assignments.length} empty="nadie asignado">
            <ul className="divide-y divide-vela-edge">
              {assignments.map((a) => {
                const key = `${a.unit_id}:${a.task_id}`
                const label = routeName(a.route, frame.state.roads, aliases)
                const e = explainAssignment(a, frame.state, graph, frame.traces.get(key), label)
                const on = focus === key
                return (
                  <li
                    key={key}
                    onMouseEnter={() => onFocus(key)}
                    onFocus={() => onFocus(key)}
                    tabIndex={0}
                    className={`py-2.5 ${on ? 'bg-vela-bg' : ''}`}
                  >
                    <div className="flex items-baseline gap-3">
                      <span className="min-w-0 flex-1 font-medium text-vela-ink">{e.headline}</span>
                      <span className="shrink-0 tabular-nums text-sm text-vela-dim">{e.etaText}</span>
                    </div>
                    <p className="text-vela-ink">{e.meaning}</p>
                    {e.equation && (
                      <>
                        <p className="pt-1 font-mono text-sm leading-snug text-vela-ink">
                          {e.equation.text}
                        </p>
                        {e.equation.inapplicable.length > 0 && (
                          <p className="text-sm italic text-vela-dim">
                            No aplican:{' '}
                            {e.equation.inapplicable
                              .map((i) => `${i.name} (${num2(i.value)}) — ${i.why}`)
                              .join('; ')}
                            .
                          </p>
                        )}
                      </>
                    )}
                    <p className="text-sm text-vela-dim">{e.routeText}</p>
                  </li>
                )
              })}
            </ul>
          </Section>

          <Section
            title="Sin cubrir"
            n={unassigned.length}
            empty="todo lo que hay que hacer tiene a alguien"
          >
            <ul className="divide-y divide-vela-edge">
              {unassigned.map((taskId) => {
                const e = explainUnassigned(taskId, frame.state, frame.infeasible, graph)
                return (
                  <li key={taskId} className="py-2.5">
                    <p className={`font-medium ${reservedTask(frame, taskId) ? 'text-vela-warn' : 'text-vela-replan'}`}>
                      {e.headline}
                    </p>
                    <p className="text-vela-ink">{e.meaning}</p>
                    <ul className="text-sm text-vela-dim">
                      {e.evidence.map((line) => (
                        <li key={line}>· {line}</li>
                      ))}
                    </ul>
                  </li>
                )
              })}
            </ul>
          </Section>
        </>
      )}
    </Panel>
  )
}

function reservedTask(frame: Frame, taskId: string): boolean {
  return frame.infeasible.some((i) => i.taskId === taskId && i.reason === 'reserved')
}

/** Los medios que la Policy ha decidido NO comprometer. */
function Reserve({ frame }: { frame: Frame }) {
  if (frame.reserves.length === 0 || frame.reservedUnits.length === 0) {
    const released = frame.policy?.released ?? []
    if (released.length === 0) return null
    return (
      <p className="border-b border-vela-edge py-2.5 text-sm text-vela-warn">
        <span className="font-medium">Sin reserva.</span>{' '}
        {released.map((r) => r.why).join('; ')}.
      </p>
    )
  }
  const names = frame.reservedUnits
    .map((id) => {
      const u = frame.state.units.get(id)
      return u ? unitName(u) : id
    })
    .join(', ')
  return (
    <p className="border-b border-vela-edge py-2.5 text-sm">
      <span className="font-medium text-vela-ink">En reserva: {names}.</span>{' '}
      <span className="text-vela-dim">
        No se compromete todo a propósito: si entra otra emergencia, ese medio sale de inmediato.
      </span>
    </p>
  )
}
