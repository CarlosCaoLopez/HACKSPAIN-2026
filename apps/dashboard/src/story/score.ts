// Comparar dos runs campo a campo. Lógica pura, como `actions` y `calls`.
//
// El criterio de puntos extra dice «run 1 contra run 12 en pantalla partida con la
// puntuación de cada uno». Lo que convierte dos columnas de números en un argumento es
// **saber en qué dirección está lo bueno**: menos celdas quemadas es mejor, más civiles
// a salvo es mejor, y menos llamadas al modelo también (invariante 7: una por replan).
// Sin esa tabla, una flecha verde puede estar celebrando un empeoramiento.
import type { RunRow, RunScore } from '../hooks/useRuns'
import { seconds } from './format'

/** Hacia dónde está lo mejor en cada campo. `null` = ni mejor ni peor, es contexto. */
type Direction = 'up' | 'down' | null

interface FieldSpec {
  key: keyof RunScore
  label: string
  direction: Direction
  format: (v: number) => string
}

const FIELDS: FieldSpec[] = [
  { key: 'total', label: 'puntuación', direction: 'up', format: (v) => v.toFixed(2) },
  { key: 'civilians_safe', label: 'civiles a salvo', direction: 'up', format: String },
  {
    key: 'civilians_exposed_end',
    label: 'civiles expuestos al final',
    direction: 'down',
    format: String,
  },
  { key: 'cells_burnt', label: 'celdas quemadas', direction: 'down', format: String },
  {
    key: 'mean_hangup_to_turn_s',
    // El número del pitch, y el único en tiempo de pared de todo el dashboard: lo demás
    // va en `t_sim`. Se dice en la etiqueta para que nadie lo lea como tiempo simulado.
    label: 'colgar → giro (tiempo real)',
    direction: 'down',
    format: (v) => seconds(v),
  },
  { key: 'replans', label: 'replanes', direction: null, format: String },
  { key: 'llm_calls', label: 'llamadas al modelo', direction: 'down', format: String },
  { key: 'calls_placed', label: 'llamadas hechas', direction: null, format: String },
]

export interface Comparison {
  label: string
  /** `null` = ese run no tiene el dato. Se pinta como hueco, nunca como cero. */
  a: string | null
  b: string | null
  /** `null` = no se puede comparar, o el campo no tiene dirección buena. */
  verdict: 'mejor' | 'peor' | 'igual' | null
  delta: string | null
}

export function compare(a: RunScore | null, b: RunScore | null): Comparison[] {
  return FIELDS.map((field) => {
    const va = value(a, field.key)
    const vb = value(b, field.key)
    return {
      label: field.label,
      a: va === null ? null : field.format(va),
      b: vb === null ? null : field.format(vb),
      verdict: verdict(va, vb, field.direction),
      delta: delta(va, vb, field.format),
    }
  })
}

function value(score: RunScore | null, key: keyof RunScore): number | null {
  if (!score) return null
  const raw = score[key]
  return typeof raw === 'number' ? raw : null
}

function verdict(
  a: number | null,
  b: number | null,
  direction: Direction,
): Comparison['verdict'] {
  if (a === null || b === null) return null
  // La igualdad se dice siempre, también en los campos sin dirección buena (los replanes
  // no son mejores ni peores por ser más): que dos runs coincidan es un dato, y una
  // celda en blanco se lee como "no lo he calculado".
  if (a === b) return 'igual'
  if (direction === null) return null
  const better = direction === 'up' ? b > a : b < a
  return better ? 'mejor' : 'peor'
}

function delta(
  a: number | null,
  b: number | null,
  format: (v: number) => string,
): string | null {
  if (a === null || b === null || a === b) return null
  const diff = b - a
  return `${diff > 0 ? '+' : '−'}${format(Math.abs(diff))}`
}

/** Los dos runs que se comparan por defecto: **el más viejo contra el más nuevo**, que
 *  es literalmente «run 1 vs run 12». `/api/runs` llega ordenado por `mtime` descendente. */
export function defaultPair(rows: RunRow[]): [string | null, string | null] {
  if (rows.length === 0) return [null, null]
  if (rows.length === 1) return [rows[0]!.run_id, rows[0]!.run_id]
  return [rows[rows.length - 1]!.run_id, rows[0]!.run_id]
}

/** Lo que hay que decir de un run antes de creerse sus números. El orden importa:
 *  *sintético* va primero porque invalida la comparación como argumento del pitch. */
export function caveats(row: RunRow | null): string[] {
  if (!row) return []
  const out: string[] = []
  if (row.synthetic) out.push('sintético · generado por fake_journal, no es un run real')
  if (row.provisional) out.push('provisional · contado por el gateway, sin total')
  if (row.incomplete) out.push('incompleto · el journal no llega a run.ended')
  return [...out, ...row.notes]
}
