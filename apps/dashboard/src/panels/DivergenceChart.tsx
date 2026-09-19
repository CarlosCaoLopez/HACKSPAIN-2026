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
import { Empty, Panel } from '../components/Panel'
import { path, series, xOf, yOf } from '../story/divergence'
import { mmss } from '../story/format'
import { DIVERGENCE_THRESHOLD } from '../types'
import type { Event } from '../types'

// Coordenadas internas del `viewBox`: el SVG escala solo al ancho del panel, así que
// estos números son proporciones, no píxeles.
const W = 300
const H = 90

export function DivergenceChart({ events }: { events: Event[] }) {
  const s = series(events)
  const line = path(s, W, H)
  const threshold = yOf(s, DIVERGENCE_THRESHOLD, H)

  return (
    <Panel
      title="Divergencia"
      count={s.points.length}
      note={s.partial ? 'histórico desde la reconexión' : undefined}
    >
      {s.last === null ? (
        // El vacío dice qué va a aparecer y qué lo dispara, no "sin datos": en el
        // segundo cero del pitch esto enseña al jurado qué mirar antes de que ocurra.
        // Fue el primero de los seis (REQ-155) y ahora es uno de los seis (REQ-193).
        <Empty>
          {`Midiendo. La línea cruza ${DIVERGENCE_THRESHOLD.toString().replace('.', ',')} cuando el mundo se aleja del plan.`}
        </Empty>
      ) : (
        <div className="flex h-full min-h-0 flex-col gap-2">
          <div className="flex items-baseline gap-2">
            {/* Nivel *sala*: es lo que se lee a diez metros mientras hablo. */}
            <span
              className={`text-3xl tabular-nums ${
                s.last.over ? 'text-vela-replan' : 'text-vela-ink'
              }`}
            >
              {s.last.value.toFixed(2).replace('.', ',')}
            </span>
            <span className="text-xs text-vela-dim">
              umbral {DIVERGENCE_THRESHOLD.toString().replace('.', ',')} · t{' '}
              {mmss(s.last.t_sim)}
            </span>
          </div>

          <svg
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            className="h-20 w-full shrink-0"
            role="img"
            aria-label="divergencia respecto a las suposiciones del plan"
          >
            {/* El umbral, discontinuo: es una referencia, no un dato medido. */}
            <line
              x1={0}
              x2={W}
              y1={threshold}
              y2={threshold}
              stroke="currentColor"
              className="text-vela-replan/50"
              strokeWidth={1}
              strokeDasharray="4 4"
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
                  stroke="currentColor"
                  className="text-vela-replan"
                  strokeWidth={1.5}
                />
              )
            })}
            <polyline
              points={line}
              fill="none"
              stroke="currentColor"
              className="text-vela-accent"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
            {s.points
              .filter((p) => p.over)
              .map((p) => {
                const x = xOf(s, p.t_sim, W)
                return x === null ? null : (
                  <circle
                    key={p.seq}
                    cx={x}
                    cy={yOf(s, p.value, H)}
                    r={3}
                    className="fill-vela-replan"
                    vectorEffect="non-scaling-stroke"
                  />
                )
              })}
          </svg>

          {/* Qué se rompió. El número sin esto no explica nada. */}
          <div className="min-h-0 flex-1 overflow-auto text-xs">
            {s.last.broken.length === 0 ? (
              <p className="text-vela-dim">ninguna suposición rota</p>
            ) : (
              <ul>
                {s.last.broken.map((key) => (
                  <li key={key} className="text-vela-ink">
                    · {key}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </Panel>
  )
}
