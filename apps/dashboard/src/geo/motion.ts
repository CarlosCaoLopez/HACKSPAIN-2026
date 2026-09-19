// Movimiento continuo de las unidades. SPEC-008 REQ-304.
//
// El problema: el sim emite UNA posición cada 4 s de simulación (12 s el dron). Si el mapa
// solo se mueve cuando llega un evento, la unidad salta de un sitio a otro. Y deslizarla
// «lo que haya tardado en llegar el evento» tampoco vale: depende de la red, de la
// velocidad del replay y de cómo se agrupen los mensajes, y en cuanto no coincide con el
// intervalo real la unidad corre y se queda quieta.
//
// La solución es la de un juego en red, **interpolación entre instantáneas**: se enseña
// cada unidad donde estaba INTERVALO Y MEDIO ANTES del «ahora» de la simulación, así siempre hay
// dos muestras conocidas entre las que interpolar y el recorrido es continuo. Se paga con
// un retraso de unos 6 s de simulación a cambio de que no haya saltos.
//
// Solo se INTERPOLA entre posiciones ya emitidas; no se predice ninguna (REQ-072).
//
// Todo aquí es puro y sin DOM: se prueba pasándole muestras y relojes.

export interface Sample {
  /** `t_sim` en el que se emitió la posición. */
  t: number
  x: number
  z: number
}

/** Lo máximo que se extrapola el «ahora» sin eventos nuevos, en segundos de pared: el doble
 *  del hueco habitual entre eventos, con un suelo y un techo. Si el run se para o el socket
 *  cae, las unidades acaban de llegar a su última posición y se quedan ahí, en vez de seguir
 *  un reloj que ya no cuenta nada. Depende del hueco medido porque con `world.tick` cada
 *  segundo bastan 1,5 s, pero un run a 1× que solo emitiera posiciones daría 4 s entre
 *  eventos y la unidad se pararía a media distancia. */
const MIN_EXTRAPOLATION_S = 1.5
const MAX_EXTRAPOLATION_S = 10
/** Cada cuánta pared se recalcula el ritmo de la simulación. */
const RATE_WINDOW_S = 1
const MAX_RATE = 200
const RATE_SMOOTHING = 0.75
/** Con qué fuerza el reloj mostrado persigue al objetivo (fracción del error por segundo). */
const CATCH_UP_PER_S = 2
/** Un error mayor que esto no se corrige suavemente: es un salto de verdad (reset, seek). */
const SNAP_ERROR_S = 20

/** El intervalo entre posiciones que se supone si aún no hay dos muestras. */
const DEFAULT_INTERVAL_S = 4
const MIN_LAG_S = 1
const MAX_LAG_S = 24
/** Se enseña la unidad intervalo y medio por detrás: el intervalo hace que siempre haya dos
 *  muestras entre las que interpolar, y la mitad extra es el margen para que una posición
 *  pueda llegar tarde o agrupada con otras sin que la unidad se pare al final de su tramo
 *  y luego dé un salto. Se hace en tiempo de simulación y no de pared a propósito: un
 *  margen que dependiera del ritmo medido se movería con él, y con él el icono. */
const LAG_INTERVALS = 1.5
const MAX_SAMPLES = 8
/** Lo más deprisa que puede cambiar el retraso mostrado, en segundos de retraso por segundo
 *  de simulación. El retraso sale del último intervalo entre posiciones, y ese intervalo
 *  cambia (la muestra sembrada al cargar la página no está a 4 s de la primera posición
 *  emitida): si el retraso saltara de golpe, el tiempo mostrado saltaría con él y la unidad
 *  daría un salto de varios metros en un fotograma. Limitado, la unidad solo acelera o
 *  frena un poco durante el cambio. */
const LAG_SLEW = 0.5

/** «Qué hora es en la simulación», a partir de los `t_sim` que van llegando y de cuándo
 *  llegaron. La simulación corre a un ritmo (1×, 3×, 60×) que no se conoce: se mide. */
export class SimClock {
  private latest = { tSim: 0, wallMs: 0 }
  private ref = { tSim: 0, wallMs: 0 }
  private rate = 0
  private gapS = 0
  private started = false
  // El reloj que se ENSEÑA. Va aparte del objetivo porque el objetivo puede retroceder un
  // poco cuando llega un evento tarde, y una unidad que va hacia atrás se ve rota.
  private shown: { tSim: number; wallMs: number } | null = null

  /** Devuelve `true` si el tiempo ha ido HACIA ATRÁS: un run nuevo o un replay que da la
   *  vuelta. Quien lo llama tiene que olvidar las posiciones antiguas. */
  observe(tSim: number, wallMs: number): boolean {
    if (!this.started) {
      this.started = true
      this.latest = this.ref = { tSim, wallMs }
      return false
    }
    if (tSim < this.latest.tSim - 1) {
      this.latest = this.ref = { tSim, wallMs }
      this.rate = 0
      this.gapS = 0
      this.shown = null
      return true
    }
    if (tSim <= this.latest.tSim) return false
    const gap = (wallMs - this.latest.wallMs) / 1000
    this.gapS = this.gapS === 0 ? gap : this.gapS * RATE_SMOOTHING + gap * (1 - RATE_SMOOTHING)
    this.latest = { tSim, wallMs }
    const dw = (wallMs - this.ref.wallMs) / 1000
    if (dw >= RATE_WINDOW_S) {
      const measured = Math.min(MAX_RATE, (tSim - this.ref.tSim) / dw)
      this.rate = this.rate === 0 ? measured : this.rate * RATE_SMOOTHING + measured * (1 - RATE_SMOOTHING)
      this.ref = { tSim, wallMs }
    }
    return false
  }

  /** El objetivo: lo último que se sabe, más lo que ha avanzado desde entonces. */
  private target(wallMs: number): number {
    const cap = Math.min(MAX_EXTRAPOLATION_S, Math.max(MIN_EXTRAPOLATION_S, 2 * this.gapS))
    const dw = Math.min(Math.max((wallMs - this.latest.wallMs) / 1000, 0), cap)
    return this.latest.tSim + dw * this.rate
  }

  /** El `t_sim` que se enseña en este instante de pared: avanza al ritmo de la simulación,
   *  nunca retrocede y se corrige poco a poco hacia el objetivo. Hay que llamarlo en cada
   *  fotograma. */
  now(wallMs: number): number {
    const target = this.target(wallMs)
    const prev = this.shown
    if (!prev || Math.abs(target - prev.tSim) > SNAP_ERROR_S) {
      this.shown = { tSim: target, wallMs }
      return target
    }
    const dt = Math.min(Math.max((wallMs - prev.wallMs) / 1000, 0), 0.25)
    let tSim = prev.tSim + dt * this.rate
    tSim += (target - tSim) * Math.min(1, dt * CATCH_UP_PER_S)
    tSim = Math.max(tSim, prev.tSim)
    this.shown = { tSim, wallMs }
    return tSim
  }
}

/** Las últimas posiciones de una unidad. */
export class UnitTrack {
  private samples: Sample[] = []
  private lagShown = Number.NaN
  private lastNow = Number.NaN

  push(s: Sample): void {
    const last = this.samples.at(-1)
    if (last && s.t < last.t - 1) this.samples = []
    else if (last && s.t <= last.t) {
      // Misma hora, posición corregida (por ejemplo un snapshot): se sustituye.
      this.samples[this.samples.length - 1] = s
      return
    }
    this.samples.push(s)
    if (this.samples.length > MAX_SAMPLES) this.samples.shift()
  }

  clear(): void {
    this.samples = []
    this.lagShown = Number.NaN
    this.lastNow = Number.NaN
  }

  get last(): Sample | undefined {
    return this.samples.at(-1)
  }

  /** El retraso con el que se enseña la unidad, en segundos de simulación. Cada unidad el
   *  suyo, porque el dron emite cada 12 s y un camión cada 4. */
  lag(): number {
    const n = this.samples.length
    const a = this.samples[n - 2]
    const b = this.samples[n - 1]
    const interval = a && b ? b.t - a.t : DEFAULT_INTERVAL_S
    return Math.min(MAX_LAG_S, Math.max(MIN_LAG_S, interval * LAG_INTERVALS))
  }

  /** Dónde estaba la unidad en `t`: interpolación lineal entre las dos muestras que lo
   *  encierran. Antes de la primera se queda en la primera y después de la última, en la
   *  última. */
  at(t: number): { x: number; z: number } | null {
    const first = this.samples[0]
    const last = this.samples.at(-1)
    if (!first || !last) return null
    if (t <= first.t) return { x: first.x, z: first.z }
    if (t >= last.t) return { x: last.x, z: last.z }
    for (let i = 0; i < this.samples.length - 1; i += 1) {
      const a = this.samples[i]
      const b = this.samples[i + 1]
      if (!a || !b || t > b.t) continue
      const k = b.t === a.t ? 1 : (t - a.t) / (b.t - a.t)
      return { x: a.x + (b.x - a.x) * k, z: a.z + (b.z - a.z) * k }
    }
    return { x: last.x, z: last.z }
  }

  /** La posición que se enseña en el instante de simulación `now`: `lag()` por detrás, con
   *  el retraso acercándose a su valor poco a poco (`LAG_SLEW`). Hay que llamarlo con `now`
   *  creciente, una vez por fotograma. */
  shownAt(now: number): { x: number; z: number } | null {
    const target = this.lag()
    if (Number.isNaN(this.lagShown)) {
      this.lagShown = target
    } else {
      const step = LAG_SLEW * Math.max(0, now - this.lastNow)
      this.lagShown += Math.max(-step, Math.min(step, target - this.lagShown))
    }
    this.lastNow = now
    return this.at(now - this.lagShown)
  }
}
