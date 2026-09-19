// Run 1 vs run 12, en pantalla partida. El criterio explícito de puntos extra.
//
// Es un **modo a pantalla completa**, no un séptimo panel: la rejilla del H1 es la
// pantalla del pitch y esto se enseña al final, con el run terminado. Cerrado no ocupa
// ni un píxel. Dentro, el mismo marco que los seis paneles (SPEC-009 REQ-333).
//
// La regla que ordena el componente: **ningún número sin su procedencia**. Un run
// sintético lo dice, una cifra contada por el gateway lo dice, y un journal a medias lo
// dice. Dos columnas de números sin eso son una comparación que no se puede defender
// cuando alguien pregunta de dónde sale el 0,92. Lo que NO se dice es el caso normal
// (REQ-335): «puntuado por journal.score» debajo de cada run era ruido.
import { useEffect, useState } from 'react'

import type { RunRow } from '../hooks/useRuns'
import { useRuns } from '../hooks/useRuns'
import { Panel } from '../components/Panel'
import { caveats, compare, defaultPair, type Comparison } from '../story/score'
import type { Event } from '../types'

/** Control neutro (REQ-320), a 2.5rem: se pulsa con el ratón en directo. */
const CONTROL =
  'min-h-[2.5rem] rounded-lg border border-vela-edge bg-vela-panel px-3 text-base text-vela-ink hover:border-vela-ink'

export function RunsPanel({ events, onClose }: { events: Event[]; onClose: () => void }) {
  const { rows, loading, error } = useRuns(events, true)
  const [left, setLeft] = useState<string | null>(null)
  const [right, setRight] = useState<string | null>(null)

  // La pareja por defecto se fija en cuanto hay lista, y solo entonces: si se recalculara
  // en cada render, un `run.ended` nuevo movería las columnas mientras las estoy
  // enseñando.
  useEffect(() => {
    if (left !== null || rows.length === 0) return
    const [a, b] = defaultPair(rows)
    setLeft(a)
    setRight(b)
  }, [rows, left])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const a = rows.find((r) => r.run_id === left) ?? null
  const b = rows.find((r) => r.run_id === right) ?? null
  const table = compare(a?.score ?? null, b?.score ?? null)
  const score = table.find((row) => row.label === 'puntuación')
  const mejoras = table.filter((row) => row.verdict === 'mejor').length

  return (
    // Opaco: con el dashboard transparentándose detrás, la tabla no se lee (REQ-333).
    <div className="absolute inset-0 z-20 flex justify-center bg-vela-bg p-6">
      <div className="h-full w-full max-w-[1100px]">
        <Panel
          title="Comparar runs"
          subtitle={loading ? 'leyendo…' : `${rows.length} ${rows.length === 1 ? 'run guardado' : 'runs guardados'}`}
          stats={[
            { label: 'puntuación A', value: score?.a ?? '—' },
            { label: 'puntuación B', value: score?.b ?? '—' },
            { label: 'mejoras de B sobre A', value: mejoras, tone: mejoras ? 'done' : undefined },
          ]}
          action={
            <button type="button" onClick={onClose} className={CONTROL}>
              Cerrar · Esc
            </button>
          }
        >
          {error && <p className="py-3 text-vela-replan">{error}</p>}

          {rows.length === 0 && !loading && !error ? (
            // El vacío dice qué va a aparecer y cómo llenarlo, no "sin datos".
            <p className="py-3 text-vela-dim">
              Todavía no hay runs. Cada demo terminada deja uno en <code>runs/</code>; para
              probar la comparación sin esperar:{' '}
              <code>uv run python scripts/fake_journal.py --runs 3 --dir runs/</code>
            </p>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-6 py-4">
                <Picker rows={rows} value={left} onChange={setLeft} label="Run A" row={a} />
                <Picker rows={rows} value={right} onChange={setRight} label="Run B" row={b} />
              </div>
              <ScoreTable table={table} />
            </>
          )}
        </Panel>
      </div>
    </div>
  )
}

function Picker({
  rows,
  value,
  onChange,
  label,
  row,
}: {
  rows: RunRow[]
  value: string | null
  onChange: (id: string) => void
  label: string
  row: RunRow | null
}) {
  const notes = caveats(row)
  return (
    <label className="flex flex-col gap-1 text-sm text-vela-dim">
      {label}
      <select value={value ?? ''} onChange={(e) => onChange(e.target.value)} className={CONTROL}>
        {rows.map((r) => (
          <option key={r.run_id} value={r.run_id}>
            {r.run_id}
            {r.synthetic ? ' · sintético' : ''}
          </option>
        ))}
      </select>
      {/* Solo lo que obliga a desconfiar de los números (REQ-335). */}
      {notes.map((note) => (
        <span key={note}>{note}</span>
      ))}
    </label>
  )
}

/** Una fila por métrica, con sus cuatro columnas en la misma fila de tabla (REQ-334): la
 *  rejilla de antes colocaba los selectores a mano y el resto fluía desalineado. */
function ScoreTable({ table }: { table: Comparison[] }) {
  return (
    <table className="w-full text-base">
      <thead>
        <tr className="border-b border-vela-edge text-left text-sm text-vela-dim">
          <th className="py-2 font-normal">Métrica</th>
          <th className="py-2 text-right font-normal">Run A</th>
          <th className="py-2 text-right font-normal">Run B</th>
          <th className="py-2 text-right font-normal">Diferencia</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-vela-edge">
        {table.map((row) => (
          <tr key={row.label}>
            <td className="py-2.5 text-vela-ink">{row.label}</td>
            <td className="py-2.5 text-right tabular-nums text-vela-ink">{row.a ?? '—'}</td>
            <td className="py-2.5 text-right tabular-nums text-vela-ink">{row.b ?? '—'}</td>
            {/* Verde lo mejor y gris lo demás (REQ-321): un run peor no es una emergencia,
                es un dato. */}
            <td
              className={`py-2.5 text-right tabular-nums ${
                row.verdict === 'mejor' ? 'text-vela-good' : 'text-vela-dim'
              }`}
            >
              {row.delta ?? (row.verdict === 'igual' ? '=' : '')}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
