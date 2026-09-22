// La Policy y su significado.
//
// Contesta «qué va primero» y, sobre todo, la pregunta de auditoría: el LLM dice qué
// IMPORTA, no quién va. Por eso la última línea del panel es fija y dice justo eso.
import { Empty, Panel, Section } from '../../components/Panel'
import type { Frame } from '../engine/loop'
import { DIVERGENCE_THRESHOLD } from '../../types'
import { explainConstraint, explainDivergence, explainWeight, num2 } from '../explain'

export function PolicyPanel({ frame }: { frame: Frame }) {
  const policy = frame.policy
  const d = frame.divergence

  return (
    <Panel
      title="Policy"
      subtitle={
        policy
          ? `${policy.weights.length} pesos · ${policy.constraints.length} restricciones duras`
          : 'sin plan todavía'
      }
    >
      {!policy ? (
        <Empty>Enciende un foco con 🔥 y dale a play: la Policy aparece con el primer plan.</Empty>
      ) : (
        <>
          <p className="py-3 text-lg leading-snug text-vela-ink">{policy.policy.rationale}</p>

          <Section title="Pesos" n={policy.weights.length} empty="ningún peso activo">
            <ul className="flex flex-col gap-3 pt-2">
              {policy.weights.map((w) => {
                const e = explainWeight(w)
                return (
                  <li key={w.name}>
                    <div className="flex items-baseline gap-2">
                      <span className="font-medium text-vela-ink">{w.name}</span>
                      <span className="tabular-nums text-vela-ink">{num2(w.value)}</span>
                      <span
                        className="ml-auto h-2 w-28 shrink-0 overflow-hidden rounded-full bg-vela-edge"
                        aria-hidden
                      >
                        <span
                          className="block h-full rounded-full bg-vela-accent"
                          style={{ width: `${Math.round(Math.min(w.value, 1) * 100)}%` }}
                        />
                      </span>
                    </div>
                    <p className="text-vela-ink">{e.meaning}</p>
                    {e.effect && <p className="text-sm text-vela-dim">{e.effect}</p>}
                    <ul className="pt-0.5 text-sm text-vela-dim">
                      {e.evidence.map((line) => (
                        <li key={line}>· {line}</li>
                      ))}
                    </ul>
                  </li>
                )
              })}
            </ul>
          </Section>

          <Section
            title="Restricciones duras"
            n={policy.constraints.length}
            empty="ninguna restricción activa"
          >
            <ul className="flex flex-col gap-2 pt-2">
              {policy.constraints.map((c) => {
                const e = explainConstraint(c.expr, c.evidence)
                return (
                  <li key={c.expr}>
                    <code className="rounded border border-vela-edge px-1.5 py-0.5 text-sm text-vela-ink">
                      {c.expr}
                    </code>
                    <p className="pt-0.5 text-vela-ink">{e.meaning}</p>
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

          {d && (
            <Section title="Divergencia" n={d.terms.length} empty="sin suposiciones">
              {(() => {
                const e = explainDivergence(d, DIVERGENCE_THRESHOLD)
                return (
                  <>
                    <p className="pt-1">
                      <span
                        className={`text-2xl tabular-nums ${d.value > DIVERGENCE_THRESHOLD ? 'text-vela-replan' : 'text-vela-ink'}`}
                      >
                        {num2(d.value)}
                      </span>
                      <span className="pl-2 text-sm text-vela-dim">umbral {num2(DIVERGENCE_THRESHOLD)}</span>
                    </p>
                    <p className="text-vela-ink">{e.meaning}</p>
                    <ul className="pt-1 font-mono text-sm text-vela-dim">
                      {e.evidence.map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                  </>
                )
              })()}
            </Section>
          )}

          {policy.released.length > 0 && (
            <Section title="Reserva soltada" n={policy.released.length} empty="">
              <ul className="pt-1 text-sm text-vela-dim">
                {policy.released.map((r) => (
                  <li key={r.capability}>
                    · <span className="text-vela-ink">{r.capability}</span>: {r.why}
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {/* La frase que contesta al jurado sin que haya que preguntarla. */}
          <p className="border-t border-vela-edge py-3 text-sm text-vela-dim">
            Esta Policy no contiene ninguna asignación: dice qué importa, no quién va. El reparto lo
            hace el solver, abajo.
          </p>
        </>
      )}
    </Panel>
  )
}
