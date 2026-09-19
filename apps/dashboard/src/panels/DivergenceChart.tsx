// La línea que sube y cruza el umbral (0,25) justo antes del banner rojo.
// Se alimenta solo de eventos `plan.divergence`.
//
// SVG a mano y sin librería de gráficas: son ocho puntos, una línea de umbral y unas
// marcas verticales. Una dependencia nueva para esto es peso en el camino crítico del
// domingo a cambio de nada.
//
// El umbral viene de `types.ts` (`DIVERGENCE_THRESHOLD`), generado desde `contracts`: si
// P1 lo mueve, la línea se mueve con él. Escrito a mano aquí, el panel mentiría el día
// que cambiara y no se vería hasta que el banner saliera antes que el cruce.
//
// Gris, salvo lo urgente (SPEC-009 REQ-322): la línea en tinta, el umbral discontinuo en
// gris, y en rojo solo los replanes y los puntos por encima del umbral.
import { Empty, Panel, Row, Rows, Section } from '../components/Panel'
import { path, series, xOf, yOf } from '../story/divergence'
import { mmss } from '../story/format'
import { DIVERGENCE_THRESHOLD } from '../types'
import type { Event } from '../types'

// Coordenadas internas del `viewBox`: el SVG escala al tamaño del hueco, así que estos
// números son proporciones, no píxeles.
const W = 300
const H = 100

const comma = (n: number, digits?: number) =>
  (digits === undefined ? n.toString() : n.toFixed(digits)).replace('.', ',')

export function DivergenceChart({ events }: { events: Event[] }) {
  const s = series(events)
  const line = path(s, W, H)
  const threshold = yOf(s, DIVERGENCE_THRESHOLD, H)
  const n = s.points.length

  return (
    <Panel
      title="Divergencia"
      subtitle={`${n} ${n === 1 ? 'medición' : 'mediciones'}${s.partial ? ' · histórico desde la reconexión' : ''}`}
      stats={[
        {
          label: s.last ? `actual · t ${mmss(s.last.t_sim)}` : 'actual',
          value: s.last ? comma(s.last.value, 2) : '—',
          tone: s.last?.over ? 'urgent' : undefined,
        },
        { label: 'umbral', value: comma(DIVERGENCE_THRESHOLD) },
        { label: 'replans', value: s.marks.length },
      ]}
    >
      {s.last === null ? (
        // El vacío dice qué va a aparecer y qué lo dispara, no "sin datos": en el
        // segundo cero del pitch esto enseña al jurado qué mirar antes de que ocurra.
        <Empty>
          {`Midiendo. La línea cruza ${comma(DIVERGENCE_THRESHOLD)} cuando el mundo se aleja del plan.`}
        </Empty>
      ) : (
        <div className="flex h-full min-h-0 flex-col">
          {/* La gráfica llena el hueco libre (REQ-325), con un mínimo para no quedarse en
              una tira cuando la lista de abajo crece. */}
          <div className="relative mt-3 min-h-[240px] flex-1">
            <svg
              viewBox={`0 0 ${W} ${H}`}
              preserveAspectRatio="none"
              className="absolute inset-0 h-full w-full overflow-visible"
              role="img"
              aria-label="divergencia respecto a las suposiciones del plan"
            >
              <line x1={0} x2={W} y1={H} y2={H} className="stroke-vela-edge" strokeWidth={1} vectorEffect="non-scaling-stroke" />
              {/* El umbral, discontinuo: es una referencia, no un dato medido. */}
              <line
                x1={0}
                x2={W}
                y1={threshold}
                y2={threshold}
                className="stroke-vela-dim"
                strokeWidth={1}
                strokeDasharray="4 4"
                vectorEffect="non-scaling-stroke"
              />
              {/* Cada replan, una vertical. Es lo que convierte la línea en un argumento:
                  se ve que el cruce va ANTES. */}
              {s.marks.map((m) => {
                const x = xOf(s, m.t_sim, W)
                return x === null ? null : (
                  <line
                    key={m.seq}
                    x1={x}
                    x2={x}
                    y1={0}
                    y2={H}
                    className="stroke-vela-replan"
                    strokeWidth={1.5}
                    vectorEffect="non-scaling-stroke"
                  />
                )
              })}
              <polyline
                points={line}
                fill="none"
                className="stroke-vela-ink"
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
              />
            </svg>
            {/* Los puntos y el rótulo van en HTML encima del SVG: con `preserveAspectRatio
                ="none"` un círculo se estira en elipse y un texto se deforma. */}
            {s.points
              .filter((p) => p.over)
              .map((p) => {
                const x = xOf(s, p.t_sim, W)
                return x === null ? null : (
                  <span
                    key={p.seq}
                    className="absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-vela-replan"
                    style={{ left: `${(x / W) * 100}%`, top: `${(yOf(s, p.value, H) / H) * 100}%` }}
                  />
                )
              })}
            <span
              className="absolute right-0 -translate-y-full pb-0.5 text-xs text-vela-dim"
              style={{ top: `${(threshold / H) * 100}%` }}
            >
              umbral {comma(DIVERGENCE_THRESHOLD)}
            </span>
          </div>

          {/* Qué se rompió. El número sin esto no explica nada. */}
          <div className="shrink-0">
            <Section
              title="Suposiciones rotas"
              n={s.last.broken.length}
              empty="Ninguna suposición rota."
            >
              <Rows>
                {s.last.broken.map((key) => (
                  <Row key={key} primary={key} />
                ))}
              </Rows>
            </Section>
          </div>
        </div>
      )}
    </Panel>
  )
}
