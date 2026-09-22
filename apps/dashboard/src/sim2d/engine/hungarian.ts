// Emparejamiento de coste mínimo. Es el sustituto de `scipy.optimize.linear_sum_assignment`.
//
// Por qué exacto y no voraz: «el LLM nunca asigna recursos; el solver es determinista»
// es el argumento entero del proyecto. Un voraz manda dos camiones al mismo frente y
// deja el otro sin cubrir en cuanto hay un empate, y entonces la frase deja de ser
// verdad justo cuando alguien la pregunta. La matriz es de 7×8: lo exacto es gratis.
//
// Algoritmo: húngaro con potenciales (Jonker-Volgenant, camino aumentante más corto),
// O(n²m). Exige filas ≤ columnas, así que si no, se traspone y se devuelven los índices
// al derecho.
//
// Los tres detalles que hay que replicar de `solver._match` en Python, o el mapa baila:
//   1. los infinitos se sustituyen por un número grande ANTES de resolver, y los pares
//      que caigan en uno se descartan DESPUÉS contra la matriz original;
//   2. se suma un desempate `(i·ncols + j)·1e-9`, porque las cuatro ambulancias empatan
//      al céntimo y sin él el emparejamiento depende del orden de recorrido interno;
//   3. el resultado es estable: la misma matriz da siempre los mismos pares.
//
// Ese desempate de scipy es lineal en `i` y en `j`, así que **no distingue entre
// permutaciones** de un mismo conjunto de celdas empatadas: con cuatro ambulancias a
// 10,99 sobre cuatro columnas iguales, (4→4, 5→5) y (4→5, 5→4) suman lo mismo, y cuál
// sale depende del algoritmo. Medido contra scipy: el coste total coincide siempre,
// los índices no. Como las columnas empatadas son columnas DUPLICADAS de la misma
// tarea, el reparto unidad→tarea es idéntico de todas formas; pero para que el mapa no
// intercambie dos ambulancias entre un plan y el siguiente se añade un segundo término,
// `(i − j)²·1e-12`, que elige canónicamente la permutación más «diagonal». Es cuatro
// órdenes de magnitud menor que el primero y doce menores que cualquier coste real, así
// que no puede cambiar un óptimo: solo ordena los empates exactos.

/** Una matriz densa en un solo array. Existe por `noUncheckedIndexedAccess`: con un
 *  `number[][]` cada acceso del bucle interno sería `number | undefined` y el algoritmo
 *  se llenaría de `!`. Aquí la aserción está en un solo sitio. */
export class Matrix {
  private readonly d: Float64Array

  constructor(
    readonly rows: number,
    readonly cols: number,
    fill = 0,
  ) {
    this.d = new Float64Array(rows * cols)
    if (fill !== 0) this.d.fill(fill)
  }

  at(i: number, j: number): number {
    return this.d[i * this.cols + j] as number
  }

  set(i: number, j: number, v: number): void {
    this.d[i * this.cols + j] = v
  }

  transposed(): Matrix {
    const t = new Matrix(this.cols, this.rows)
    for (let i = 0; i < this.rows; i++) {
      for (let j = 0; j < this.cols; j++) t.set(j, i, this.at(i, j))
    }
    return t
  }
}

/** `p[j]` = fila (1-indexada) emparejada con la columna j, o 0. Matriz sin infinitos. */
function jv(a: Matrix): Int32Array {
  const n = a.rows
  const m = a.cols
  const u = new Float64Array(n + 1)
  const v = new Float64Array(m + 1)
  const p = new Int32Array(m + 1)
  const way = new Int32Array(m + 1)
  const minv = new Float64Array(m + 1)
  const used = new Uint8Array(m + 1)

  for (let i = 1; i <= n; i++) {
    p[0] = i
    let j0 = 0
    minv.fill(Infinity)
    used.fill(0)
    do {
      used[j0] = 1
      const i0 = p[j0] as number
      let delta = Infinity
      let j1 = 0
      for (let j = 1; j <= m; j++) {
        if (used[j]) continue
        const cur = a.at(i0 - 1, j - 1) - (u[i0] as number) - (v[j] as number)
        if (cur < (minv[j] as number)) {
          minv[j] = cur
          way[j] = j0
        }
        if ((minv[j] as number) < delta) {
          delta = minv[j] as number
          j1 = j
        }
      }
      for (let j = 0; j <= m; j++) {
        if (used[j]) {
          u[p[j] as number] = (u[p[j] as number] as number) + delta
          v[j] = (v[j] as number) - delta
        } else {
          minv[j] = (minv[j] as number) - delta
        }
      }
      j0 = j1
    } while (p[j0] !== 0)
    do {
      const j1 = way[j0] as number
      p[j0] = p[j1] as number
      j0 = j1
    } while (j0 !== 0)
  }
  return p
}

/** Los pares (fila, columna) del emparejamiento de coste mínimo.
 *
 *  Solo los pares con coste ORIGINAL finito: un infinito es «esta unidad no puede
 *  hacer esta tarea» (le falta la capacidad, la ruta está cortada, una restricción
 *  dura lo veta) y el hueco se enseña como tarea sin cubrir, no se rellena. */
export function match(matrix: ReadonlyArray<ReadonlyArray<number>>): Array<[number, number]> {
  const nrows = matrix.length
  const ncols = matrix[0]?.length ?? 0
  if (nrows === 0 || ncols === 0) return []

  let maxFinite = -Infinity
  for (const row of matrix) {
    for (const value of row) if (Number.isFinite(value) && value > maxFinite) maxFinite = value
  }
  const big = Number.isFinite(maxFinite) ? (maxFinite + 1) * (nrows * ncols + 1) : 1

  const solvable = new Matrix(nrows, ncols)
  for (let i = 0; i < nrows; i++) {
    const row = matrix[i]
    for (let j = 0; j < ncols; j++) {
      const raw = row?.[j] ?? Infinity
      const tie = (i * ncols + j) * 1e-9 + (i - j) * (i - j) * 1e-12
      solvable.set(i, j, (Number.isFinite(raw) ? raw : big) + tie)
    }
  }

  const flipped = nrows > ncols
  const p = jv(flipped ? solvable.transposed() : solvable)

  const pairs: Array<[number, number]> = []
  // `p` indexa columnas de la matriz que se resolvió; al trasponer, sus «columnas»
  // son nuestras filas.
  for (let j = 1; j < p.length; j++) {
    const i = p[j] as number
    if (i === 0) continue
    const row = flipped ? j - 1 : i - 1
    const col = flipped ? i - 1 : j - 1
    if (Number.isFinite(matrix[row]?.[col] ?? Infinity)) pairs.push([row, col])
  }
  pairs.sort((a, b) => a[0] - b[0] || a[1] - b[1])
  return pairs
}
