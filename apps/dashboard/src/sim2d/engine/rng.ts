// Un generador sembrado, porque la propagación del fuego es estocástica.
//
// `Math.random()` haría que reiniciar la simulación diera otro incendio, y entonces
// «pon el mismo foco con el mismo viento y mira» dejaría de ser una demostración. Con
// mulberry32 y la semilla del escenario (1821), el mismo número de ticks produce
// exactamente el mismo run. Es lo mismo que hace `sim/hazard.py` con `random.Random(seed)`.
export interface Rng {
  next(): number
}

export function mulberry32(seed: number): Rng {
  let a = seed >>> 0
  return {
    next(): number {
      a = (a + 0x6d2b79f5) >>> 0
      let t = a
      t = Math.imul(t ^ (t >>> 15), t | 1)
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296
    },
  }
}
