// La cola de prioridades con su justificación, y los tres botones de override
// sobre cada tarjeta de asignación: forzar, vetar, replanificar.
//
// Los botones son del H4 y ya no son un hueco reservado: publican `human.override` por
// `POST /control/override`, que es el criterio *Control* del reto. Dos reglas:
//
// - **El panel no toca su estado local al pulsar.** El efecto llega por el chorro de
//   eventos como todo lo demás. Pintar el veto como aplicado y que el core lo ignore es
//   una pantalla que miente, y eso se descubre en el pitch.
// - **Mientras nadie aplique el override, la tarjeta lo dice.** *Enviado · pendiente de
//   aplicar* hasta que entre un plan nuevo. Hoy el core no tiene cuerpo, así que la
//   marca se queda puesta — y eso es información correcta, no un fallo.
//
// Lo que sí es de este panel desde el H3: que se lea la frase del planner.
// `Policy.rationale` es medio pitch y va arriba, en grande.
import { useRef, useState } from 'react'

import type { OverrideKind, Plan, WorldState } from '../types'
import { Empty, Panel, Skeleton } from '../components/Panel'
import { useControl, type OverrideRequest } from '../hooks/useControl'
import { SEVERITY } from '../story/labels'
import { seconds, shortId } from '../story/format'

/** Los tres de la tarjeta. `set_priority` existe en el contrato pero no tiene botón:
 *  no hay nada que pulsar sin un campo de prioridad, y un botón de más en directo es
 *  un botón que se pulsa sin querer. */
const BUTTONS: { kind: OverrideKind; label: string }[] = [
  { kind: 'force_assignment', label: 'forzar' },
  { kind: 'veto_assignment', label: 'vetar' },
  { kind: 'force_replan', label: 'replan' },
]

/** El campo de asertar hechos es uno para todo el panel, no uno por tarjeta. */
const ASSERT_KEY = 'assert_fact'

export function PriorityQueue({
  plan,
  state,
  awaitingSnapshot,
}: {
  plan: Plan | null
  state: WorldState | null
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

  /** Qué enseñar debajo de los botones de una asignación: error, eco o pendiente. */
  const mark = (key: string): { text: string; tone: string } | null => {
    const error = failed.get(key)
    if (error) return { text: error, tone: 'text-vela-warn' }
    const done = sent.get(key)
    if (!done) return null
    if (done.response.echo) {
      return { text: 'eco local (replay) · no publicado', tone: 'text-vela-dim' }
    }
    // Un plan nuevo es la respuesta del sistema: la marca ya no aporta nada.
    if (plan && planAtSend.current.get(key) !== plan.id) return null
    return { text: 'enviado · pendiente de aplicar', tone: 'text-vela-accent' }
  }
  const tasks = state?.tasks ?? null
  // Sin `WorldState.tasks` no hay severidad, así que se ordena por coste y SE DICE.
  // Fingir una severidad que no tenemos sería inventarse la prioridad del sistema,
  // que es justo lo que este panel presume de explicar.
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
      // `count={0}` y no un hueco (REQ-194): un contador ausente se lee como fallo del
      // panel, y un cero se lee como lo que es — todavía no hay nada que repartir.
      <Panel title="Cola de prioridad" count={0}>
        {awaitingSnapshot ? (
          <Skeleton rows={3} />
        ) : (
          <Empty>Sin plan todavía. El solver publica uno en cuanto hay tareas.</Empty>
        )}
      </Panel>
    )
  }

  const { policy } = plan

  return (
    <Panel title="Cola de prioridad" count={assignments.length} note={criterion}>
      <div className="flex flex-col gap-3">
        <blockquote className="border-l-2 border-vela-accent pl-3 text-base leading-relaxed text-vela-ink">
          {policy.rationale}
        </blockquote>

        {/* Los pesos explican POR QUÉ el solver eligió esto: son la política del LLM
            traducida a números, y el solver solo obedece. */}
        {Object.keys(policy.weights).length > 0 && (
          <ul className="flex flex-col gap-1">
            {Object.entries(policy.weights)
              .sort((a, b) => b[1] - a[1])
              .map(([name, weight]) => (
                <li key={name} className="flex items-center gap-2 text-xs">
                  <span className="w-40 shrink-0 truncate text-vela-dim">{name}</span>
                  <span className="h-1.5 flex-1 rounded-[3px] bg-vela-bg">
                    <span
                      className="block h-full rounded-[3px] bg-vela-accent"
                      style={{ width: `${Math.round(weight * 100)}%` }}
                    />
                  </span>
                  <span className="tabular-nums text-vela-dim">{weight.toFixed(2)}</span>
                </li>
              ))}
          </ul>
        )}

        {policy.hard_constraints.length > 0 && (
          <ul className="flex flex-wrap gap-1">
            {policy.hard_constraints.map((constraint) => (
              <li
                key={constraint}
                className="rounded-md border border-vela-edge px-2 py-0.5 text-xs text-vela-dim"
              >
                {constraint}
              </li>
            ))}
          </ul>
        )}

        <ol className="flex flex-col gap-2">
          {assignments.map((assignment) => {
            const task = tasks?.[assignment.task_id]
            const from = assignment.route[0]
            const to = assignment.route[assignment.route.length - 1]
            const target = `${assignment.unit_id}:${assignment.task_id}`
            return (
              <li key={target} className="rounded-[9px] border border-vela-edge p-2.5">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-vela-ink">
                      {shortId(assignment.unit_id)} → {shortId(assignment.task_id)}
                    </p>
                    <p className="text-xs text-vela-dim">
                      {from && to ? `${from} → ${to}` : 'sin ruta'} ·{' '}
                      {seconds(assignment.eta_s)} · coste {assignment.cost.toFixed(1)}
                    </p>
                  </div>
                  {task && (
                    <span className="shrink-0 text-xs text-vela-accent">
                      {SEVERITY[task.severity]}
                    </span>
                  )}
                </div>

                {/* La intervención humana, que es requisito del reto y se usa en
                    directo. El `target` sale de la propia tarjeta: en el pitch no se
                    teclea un id. */}
                <div className="mt-2 flex flex-wrap gap-2">
                  {BUTTONS.map(({ kind, label }) => {
                    const key = `${target}:${kind}`
                    return (
                      <button
                        key={kind}
                        type="button"
                        // h-8 = 32 px (REQ-148) y `text-sm` en caja baja (REQ-197): esto
                        // se pulsa en directo y la sala tiene que leer qué se ha pulsado.
                        className="h-8 rounded-[7px] border border-vela-edge px-3 text-[13px] text-vela-ink hover:border-vela-accent hover:text-vela-accent disabled:opacity-40"
                        disabled={pending.has(key)}
                        onClick={() =>
                          send(key, {
                            kind,
                            target,
                            note: `${label} desde el dashboard`,
                          })
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
                    <p key={kind} className={`mt-1 text-xs ${m.tone}`}>
                      {kind.replace(/_/g, ' ')} · {m.text}
                    </p>
                  ) : null
                })}
              </li>
            )
          })}
        </ol>

        <AssertFact send={send} pending={pending.has(ASSERT_KEY)} mark={mark(ASSERT_KEY)} />

        {/* Lo que el solver no pudo cubrir es información, no un fallo que esconder:
            con un camión averiado, alguien tiene que ver qué se ha quedado sin nadie. */}
        {plan.unassigned_tasks.length > 0 && (
          <div>
            <p className="text-xs font-bold tracking-wide text-vela-warn">SIN CUBRIR</p>
            <ul className="flex flex-col gap-1">
              {plan.unassigned_tasks.map((taskId) => (
                <li key={taskId} className="text-vela-warn">
                  {shortId(taskId)}
                  {tasks?.[taskId] && ` · ${SEVERITY[tasks[taskId]!.severity]}`}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
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
  mark: { text: string; tone: string } | null
}) {
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')

  const input =
    'h-8 flex-1 rounded-[7px] border border-vela-edge bg-vela-bg px-2.5 text-[13px] text-vela-ink placeholder:text-vela-dim'

  return (
    <div className="border-t border-vela-edge pt-2">
      <p className="text-xs font-bold tracking-wide text-vela-dim">ASERTAR UN HECHO</p>
      <form
        className="mt-1 flex flex-wrap gap-2"
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
        <button
          type="submit"
          className="h-8 rounded-[7px] border border-vela-edge px-3.5 text-[13px] text-vela-ink hover:border-vela-accent hover:text-vela-accent disabled:opacity-40"
          disabled={pending || !key.trim()}
        >
          {pending ? '…' : 'asertar'}
        </button>
      </form>
      {mark && <p className={`mt-1 text-xs ${mark.tone}`}>{mark.text}</p>}
    </div>
  )
}
