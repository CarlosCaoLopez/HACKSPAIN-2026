// La línea que sube y cruza el umbral justo antes del banner rojo.
//
// Es la pieza que puntúa en *Adaptación* y la que casi nadie implementa: el valor solo
// no dice nada, lo que dice algo es **el orden**. Primero la divergencia cruza 0,25,
// después entra el replan. Si en pantalla se viera al revés, el sistema parecería que
// replanifica porque sí.
//
// Lógica pura y fuera del `.tsx` por lo mismo que `actions` y `calls`: así se le pasa el
// fixture entero sin navegador, que es como salieron los fallos del H4.
import { DIVERGENCE_THRESHOLD } from '../types'
import type { Event, VelaEvent } from '../types'

export interface Point {
  t_sim: number
  value: number
  /** Qué suposición del plan se rompió. Un número sin esto no explica nada. */
  broken: string[]
  seq: number
  /** Por encima del umbral: el momento en el que el plan deja de ser defendible. */
  over: boolean
}

export interface Mark {
  t_sim: number
  seq: number
  reason: string
}

export interface Series {
  points: Point[]
  /** Las marcas verticales de `plan.replan.started`. */
  marks: Mark[]
  last: Point | null
  /** `true` si el primer punto no es del arranque del run: tras una reconexión el
   *  histórico se vacía (REQ-055) y la línea empieza por la mitad. Se rotula, porque
   *  una línea parcial presentada como la línea entera es una mentira. */
  partial: boolean
  /** Tope del eje Y. Nunca por debajo de 1: si la escala se ajustara al máximo visto,
   *  un 0,04 llenaría el panel y parecería una crisis. */
  max: number
}

/** El `t_sim` por debajo del cual se considera que la serie arranca en el principio del
 *  run. Los primeros informes del fixture caen sobre t=30, y un run real publica el
 *  primero en cuanto hay plan que vigilar. */
const START_S = 45

export function series(events: Event[]): Series {
  const points: Point[] = []
  const marks: Mark[] = []
  let sawStart = false

  for (const envelope of events) {
    const ev = envelope as unknown as VelaEvent
    if (ev.type === 'run.started') sawStart = true
    if (ev.type === 'plan.divergence') {
      points.push({
        t_sim: ev.t_sim,
        value: ev.payload.value,
        broken: ev.payload.broken ?? [],
        seq: ev.seq,
        over: ev.payload.value > DIVERGENCE_THRESHOLD,
      })
      continue
    }
    if (ev.type === 'plan.replan.started') {
      marks.push({ t_sim: ev.t_sim, seq: ev.seq, reason: ev.payload.reason })
    }
  }

  const first = points[0]
  return {
    points,
    marks,
    last: points.length ? points[points.length - 1]! : null,
    partial: !sawStart && first !== undefined && first.t_sim > START_S,
    max: Math.max(1, ...points.map((p) => p.value)),
  }
}

/** La polilínea en coordenadas del `viewBox`, ya escalada.
 *
 *  El eje X es `t_sim` y no el índice del punto: los informes no llegan a ritmo fijo, y
 *  con el índice la línea mentiría sobre cuándo pasó cada cosa — que es justo lo que
 *  este panel existe para contar. */
export function path(s: Series, width: number, height: number): string {
  if (s.points.length === 0) return ''
  const last = s.points[s.points.length - 1]!
  const span = Math.max(1, last.t_sim - s.points[0]!.t_sim)
  const x0 = s.points[0]!.t_sim
  return s.points
    .map((p) => {
      const x = ((p.t_sim - x0) / span) * width
      const y = height - (p.value / s.max) * height
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

/** La x de un `t_sim` cualquiera en el mismo sistema, para las marcas de replan. */
export function xOf(s: Series, tSim: number, width: number): number | null {
  if (s.points.length === 0) return null
  const x0 = s.points[0]!.t_sim
  const last = s.points[s.points.length - 1]!
  const span = Math.max(1, last.t_sim - x0)
  const x = ((tSim - x0) / span) * width
  // Una marca fuera del tramo dibujado no se pinta pegada al borde fingiendo estar
  // dentro: no se pinta.
  return x < 0 || x > width ? null : x
}

export function yOf(s: Series, value: number, height: number): number {
  return height - (value / s.max) * height
}
