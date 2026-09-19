// La cola de prioridades con su justificación, y los tres botones de override
// sobre cada asignación: forzar, vetar, replanificar.
//
// Los botones son del H4 y ya no son un hueco reservado: publican `human.override` por
// `POST /control/override`, que es el criterio *Control* del reto. Siguen visibles en cada
// fila (SPEC-009 D2): en directo no hay tiempo de abrir un menú. Dos reglas:
//
// - **El panel no toca su estado local al pulsar.** El efecto llega por el chorro de
//   eventos como todo lo demás. Pintar el veto como aplicado y que el core lo ignore es
//   una pantalla que miente, y eso se descubre en el pitch.
// - **Mientras nadie aplique el override, la fila lo dice.** *Enviado · pendiente de
//   aplicar* hasta que entre un plan nuevo.
//
// Lo que sí es de este panel desde el H3: que se lea la frase del planner.
// `Policy.rationale` es medio pitch y va arriba, en grande.
import { useMemo, useRef, useState } from 'react'

import type { Event, OverrideKind, Plan, WorldState } from '../types'
import { Empty, Panel, Row, Rows, Section, Skeleton, toneClass, type DataTone } from '../components/Panel'
import { useControl, type OverrideRequest } from '../hooks/useControl'
import { SEVERITY } from '../story/labels'
import { seconds, shortId } from '../story/format'
import { knownTasks, taskName } from '../story/tasks'

/** Los tres de la fila. `set_priority` existe en el contrato pero no tiene botón:
 *  no hay nada que pulsar sin un campo de prioridad, y un botón de más en directo es
 *  un botón que se pulsa sin querer. */
const BUTTONS: { kind: OverrideKind; label: string }[] = [
  { kind: 'force_assignment', label: 'forzar' },
  { kind: 'veto_assignment', label: 'vetar' },
  { kind: 'force_replan', label: 'replan' },
]

/** El campo de asertar hechos es uno para todo el panel, no uno por fila. */
const ASSERT_KEY = 'assert_fact'

/** Botón neutro (REQ-320): h-8 = 32 px (REQ-148), y ni un color al pasar el ratón. */
const BUTTON =
  'h-8 rounded-lg border border-vela-edge px-3 text-[13px] text-vela-ink hover:border-vela-ink disabled:opacity-40'

type Mark = { text: string; tone?: DataTone }

export function PriorityQueue({
  plan,
  state,
  events,
  awaitingSnapshot,
}: {
  plan: Plan | null
  state: WorldState | null
  /** Para los `task.changed` posteriores al snapshot: sin ellos, un frente nuevo no
   *  tiene ni nombre ni gravedad. */
  events: Event[]
  awaitingSnapshot: boolean
}) {
  const { override, pending, sent, failed } = useControl()
  // El plan vigente cuando se mandó cada override: con eso se sabe si el sistema ya ha
  // contestado (plan nuevo) o si la orden sigue esperando a que alguien la aplique.
  const planAtSend = useRef(new Map<string, string>())

  const send = (key: string, body: OverrideRequest) => {
    planAtSend.current.set(key, plan?.id ?? '')
    void override(key, body)
  }

  /** Qué enseñar debajo de los botones: error, eco o pendiente. */
  const mark = (key: string): Mark | null => {
    const error = failed.get(key)
    // Un override que no llegó es una orden fallida (REQ-321).
    if (error) return { text: error, tone: 'urgent' }
    const done = sent.get(key)
    if (!done) return null
    if (done.response.echo) return { text: 'eco local (replay) · no publicado' }
    // Un plan nuevo es la respuesta del sistema: la marca ya no aporta nada.
    if (plan && planAtSend.current.get(key) !== plan.id) return null
    return { text: 'enviado · pendiente de aplicar' }
  }
  const known = useMemo(() => knownTasks(state, events), [state, events])
  // Sin ninguna tarea del core no hay severidad, así que se ordena por coste y SE DICE.
  // Fingir una severidad que no tenemos sería inventarse la prioridad del sistema,
  // que es justo lo que este panel presume de explicar.
  const tasks = Object.keys(known).length > 0 ? known : null
  const pois = state?.pois ?? null
  const criterion = tasks ? 'por severidad' : 'por coste (sin tareas del core)'

  const assignments = [...(plan?.assignments ?? [])].sort((a, b) => {
    if (tasks) {
      const sa = tasks[a.task_id]?.severity
      const sb = tasks[b.task_id]?.severity
      const rank = { critical: 3, high: 2, medium: 1, low: 0 }
      if (sa && sb && sa !== sb) return rank[sb] - rank[sa]
    }
    return b.cost - a.cost
  })

  if (!plan) {
    return (
      // El cero se enseña con sustantivo (REQ-194, REQ-315): todavía no hay nada que repartir.
      <Panel title="Cola de prioridad" subtitle="0 asignaciones · sin plan">
        {awaitingSnapshot ? (
          <Skeleton rows={3} />
        ) : (
          <Empty>Sin plan todavía. El solver publica uno en cuanto hay tareas.</Empty>
        )}
      </Panel>
    )
  }

  const { policy } = plan
  const unassigned = plan.unassigned_tasks
  const maxEta = assignments.reduce((max, a) => Math.max(max, a.eta_s), 0)
  const weights = Object.entries(policy.weights).sort((a, b) => b[1] - a[1])

  return (
    <Panel
      title="Cola de prioridad"
      subtitle={`${assignments.length} ${assignments.length === 1 ? 'asignación' : 'asignaciones'} · ${criterion}`}
      stats={[
        { label: 'asignadas', value: assignments.length },
        { label: 'sin cubrir', value: unassigned.length, tone: unassigned.length ? 'urgent' : undefined },
        { label: 'ETA máxima', value: assignments.length ? seconds(maxEta) : '—' },
      ]}
    >
      <p className="py-3 leading-relaxed text-vela-ink">{policy.rationale}</p>

      <Section title="Asignaciones" n={assignments.length} empty="El solver no ha asignado nada.">
        <Rows>
          {assignments.map((assignment) => {
            const task = tasks?.[assignment.task_id]
            const from = assignment.route[0]
            const to = assignment.route[assignment.route.length - 1]
            // El core puede mandar DOS unidades al mismo frente (dos asignaciones con el
            // mismo `task_id`): la clave de la fila es la pareja, nunca la tarea sola.
            const target = `${assignment.unit_id}:${assignment.task_id}`
            return (
              <Row
                key={target}
                primary={`${shortId(assignment.unit_id)} → ${taskName(assignment.task_id, tasks, pois)}`}
                meta={`llega en ${seconds(assignment.eta_s)}`}
                status={task ? SEVERITY[task.severity] : undefined}
                tone={task?.severity === 'critical' ? 'urgent' : undefined}
                // Ruta, coste y el id de la tarea son del solver, no de quien mira: al
                // `title` (REQ-329).
                hint={`${assignment.task_id} · ${from && to ? `${from} → ${to}` : 'sin ruta'} · coste ${assignment.cost.toFixed(1)}`}
              >
                {/* La intervención humana, que es requisito del reto y se usa en
                    directo. El `target` sale de la propia fila: en el pitch no se
                    teclea un id. */}
                <div className="mt-2 flex flex-wrap gap-2">
                  {BUTTONS.map(({ kind, label }) => {
                    const key = `${target}:${kind}`
                    return (
                      <button
                        key={kind}
                        type="button"
                        className={BUTTON}
                        disabled={pending.has(key)}
                        onClick={() =>
                          send(key, { kind, target, note: `${label} desde el dashboard` })
                        }
                      >
                        {pending.has(key) ? '…' : label}
                      </button>
                    )
                  })}
                </div>
                {BUTTONS.map(({ kind }) => {
                  const m = mark(`${target}:${kind}`)
                  return m ? (
                    <p key={kind} className={`mt-1 text-sm ${toneClass(m.tone)}`}>
                      {kind.replace(/_/g, ' ')} · {m.text}
                    </p>
                  ) : null
                })}
              </Row>
            )
          })}
        </Rows>
      </Section>

      {/* Lo que el solver no pudo cubrir es información, no un fallo que esconder:
          con un camión averiado, alguien tiene que ver qué se ha quedado sin nadie. */}
      <Section title="Sin cubrir" n={unassigned.length} empty="Todas las tareas tienen unidad.">
        <Rows>
          {unassigned.map((taskId) => {
            const task = tasks?.[taskId]
            return (
              <Row
                key={taskId}
                primary={taskName(taskId, tasks, pois)}
                meta={task ? `gravedad ${SEVERITY[task.severity]}` : 'sin tarea en el estado'}
                status="sin cubrir"
                tone="urgent"
                hint={taskId}
              />
            )
          })}
        </Rows>
      </Section>

      {/* Los pesos explican POR QUÉ el solver eligió esto: son la política del LLM
          traducida a números, y el solver solo obedece. */}
      <Section title="Pesos de la política" n={weights.length} empty="Pesos neutros.">
        <ul className="flex flex-col gap-1.5 pt-1.5">
          {weights.map(([name, weight]) => (
            <li key={name} className="flex items-center gap-3 text-sm">
              <span className="w-40 shrink-0 truncate text-vela-dim">{name}</span>
              <span className="h-1.5 flex-1 rounded-full bg-vela-edge">
                <span
                  className="block h-full rounded-full bg-vela-ink"
                  style={{ width: `${Math.round(weight * 100)}%` }}
                />
              </span>
              <span className="w-10 text-right tabular-nums text-vela-dim">{weight.toFixed(2)}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section
        title="Restricciones duras"
        n={policy.hard_constraints.length}
        empty="Ninguna."
      >
        <ul className="pt-1 text-sm text-vela-dim">
          {policy.hard_constraints.map((constraint) => (
            <li key={constraint}>{constraint}</li>
          ))}
        </ul>
      </Section>

      <AssertFact send={send} pending={pending.has(ASSERT_KEY)} mark={mark(ASSERT_KEY)} />
    </Panel>
  )
}

/** El único control que se escribe a mano: asertar un hecho que el sistema no sabe.
 *
 *  Va con un `placeholder` de clave real porque en directo no hay tiempo de recordar la
 *  sintaxis, y porque una clave inventada la acepta el gateway (avisa, no rechaza) y la
 *  descarta el core sin que se vea. */
function AssertFact({
  send,
  pending,
  mark,
}: {
  send: (key: string, body: OverrideRequest) => void
  pending: boolean
  mark: Mark | null
}) {
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')

  const input =
    'h-8 flex-1 rounded-lg border border-vela-edge bg-vela-panel px-2.5 text-[13px] text-vela-ink placeholder:text-vela-dim'

  return (
    <section className="py-3">
      <h3 className="text-sm font-semibold text-vela-ink">Asertar un hecho</h3>
      <form
        className="mt-1.5 flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (!key.trim()) return
          send(ASSERT_KEY, {
            kind: 'assert_fact',
            target: key.trim(),
            value: value.trim() || true,
            note: 'asertado a mano desde el dashboard',
          })
        }}
      >
        <input
          className={input}
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="road:wp_norte_02-wp_norte_03:cut"
          aria-label="clave del hecho"
        />
        <input
          className={`${input} max-w-32`}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="sí"
          aria-label="valor del hecho"
        />
        <button type="submit" className={BUTTON} disabled={pending || !key.trim()}>
          {pending ? '…' : 'asertar'}
        </button>
      </form>
      {mark && <p className={`mt-1 text-sm ${toneClass(mark.tone)}`}>{mark.text}</p>}
    </section>
  )
}
