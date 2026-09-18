// Formateo para pantalla de proyector. Nada de dependencias: son cuatro funciones.

/** `t_sim` en segundos → `mm:ss`. El tiempo del dominio es `t_sim`; `t_wall` solo
 *  depura, y por eso no se pinta nunca. */
export function mmss(tSim: number): string {
  const total = Math.max(0, Math.floor(tSim))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

/** Confianza como porcentaje entero: `0.93` → `93 %`. Con coma decimal no se lee de
 *  lejos, y el decimal no aporta nada a diez metros. */
export function pct(value: number): string {
  return `${Math.round(value * 100)} %`
}

/** Un valor de hecho (`string | number | boolean`) tal cual se dice en voz alta. */
export function factValue(value: string | number | boolean): string {
  if (typeof value === 'boolean') return value ? 'sí' : 'no'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2)
  return value
}

/** Ids con prefijo → algo legible: `unit_truck1` → `truck1`, `poi_pueblo_a` → `pueblo a`.
 *  El id completo sigue disponible donde importa la procedencia; esto es para que la
 *  frase se lea como una frase. */
export function shortId(id: string): string {
  const cut = id.indexOf('_')
  const tail = cut === -1 ? id : id.slice(cut + 1)
  return tail.replace(/_/g, ' ')
}

export function seconds(s: number): string {
  return s >= 60 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`
}
