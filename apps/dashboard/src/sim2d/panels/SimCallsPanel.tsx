// A quién se llama, en qué orden y por qué.
//
// El beat que este panel tiene que dejar ver: **las unidades no se mueven hasta que
// su dotación cuelga**. Por eso cada tarjeta dice a quién retiene, y en el mapa la
// ruta de una unidad retenida está en gris discontinuo.
//
// Y cuando nadie contesta, a los 45 s la unidad sale igual y la tarjeta lo dice:
// «salió SIN confirmar». La degradación se enseña, nunca se esconde.
import { Empty, Panel } from '../../components/Panel'
import { mmss } from '../../story/format'
import { DISPATCH_RING_S, DISPATCH_TALK_S, roleLabel, type SimCall } from '../engine/dispatch'
import type { Frame } from '../engine/loop'

const PHASE_WORD: Record<SimCall['phase'], string> = {
  ringing: 'sonando',
  talking: 'en curso',
  ended: 'colgada',
}

export function SimCallsPanel({ frame }: { frame: Frame }) {
  const calls = [...frame.calls].reverse()
  const live = frame.calls.filter((c) => c.phase !== 'ended').length

  return (
    <Panel
      title="Llamadas"
      subtitle={
        frame.calls.length === 0
          ? 'ninguna todavía'
          : `${frame.calls.length} ${frame.calls.length === 1 ? 'llamada' : 'llamadas'}${live > 0 ? ` · ${live} en curso` : ''}`
      }
      stats={[
        { label: 'unidades retenidas', value: frame.heldUnits.length, tone: frame.heldUnits.length > 0 ? 'urgent' : undefined },
        { label: 'órdenes dadas', value: frame.actions.length },
      ]}
    >
      {calls.length === 0 ? (
        <Empty>
          Las llamadas nacen del plan: primero se piden los medios al retén y a la ambulancia, y
          solo cuando cuelgan se dicta la orden al pueblo.
        </Empty>
      ) : (
        <ul className="divide-y divide-vela-edge">
          {calls.map((c) => (
            <Card key={c.id} call={c} frame={frame} />
          ))}
        </ul>
      )}
    </Panel>
  )
}

function Card({ call, frame }: { call: SimCall; frame: Frame }) {
  const budget = call.phase === 'talking' ? DISPATCH_TALK_S : DISPATCH_RING_S
  const since = call.answeredT ?? call.startedT
  const left = Math.max(0, budget - (frame.tSim - since))
  // Una unidad que salió por vencimiento del plazo, no por un «vamos».
  const unconfirmed = frame.actions.filter(
    (a) => call.heldUnits.includes(a.unitId) && a.dispatchConfirmed === false,
  )

  return (
    <li className="py-3">
      <div className="flex items-baseline gap-3">
        <span className="w-12 shrink-0 text-sm tabular-nums text-vela-dim">{mmss(call.startedT)}</span>
        <span className="min-w-0 flex-1 font-medium text-vela-ink">
          {roleLabel(call.role)} · {call.callee}
        </span>
        <span
          className={`shrink-0 text-sm ${call.phase === 'ended' ? 'text-vela-good' : 'text-vela-call'}`}
        >
          {PHASE_WORD[call.phase]}
        </span>
      </div>

      <p className="pt-0.5 text-sm text-vela-dim">{call.why}</p>

      {call.heldUnits.length > 0 && (
        <p className="pt-1 text-sm">
          {call.phase === 'ended' ? (
            unconfirmed.length > 0 ? (
              <span className="text-vela-replan">
                Salió SIN confirmar: nadie cogió el teléfono en {Math.round(DISPATCH_RING_S)} s.
              </span>
            ) : (
              <span className="text-vela-good">
                Colgaron y salieron: {call.heldUnits.map(short).join(', ')}.
              </span>
            )
          ) : (
            <span className="text-vela-warn">
              Retiene {call.heldUnits.map(short).join(', ')} — no se mueven hasta que cuelguen.{' '}
              <span className="tabular-nums">{Math.round(left)} s</span> de plazo.
            </span>
          )}
        </p>
      )}

      {call.said.length > 0 && (
        <ol className="flex flex-col gap-1 pt-2">
          {call.said.map((line, i) => (
            <li key={i} className="text-sm leading-snug">
              <span className={line.speaker === 'agente' ? 'text-vela-call' : 'text-vela-dim'}>
                {line.speaker === 'agente' ? 'VELA' : call.callee}:
              </span>{' '}
              <span className="text-vela-ink">{line.text}</span>
            </li>
          ))}
        </ol>
      )}

      {call.facts.length > 0 && (
        <dl className="pt-2 text-sm">
          {call.facts.map((f) => (
            <div key={f.label} className="flex gap-2">
              <dt className="shrink-0 font-mono text-vela-dim">{f.label}</dt>
              <dd className="min-w-0 text-vela-dim">{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  )
}

function short(unitId: string): string {
  return unitId.replace(/^unit_/, '')
}
