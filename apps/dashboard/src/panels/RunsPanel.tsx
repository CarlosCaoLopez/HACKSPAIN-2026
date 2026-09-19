// Run 1 vs run 12, en pantalla partida. El criterio explícito de puntos extra.
//
// Es un **modo a pantalla completa**, no un séptimo panel: la rejilla del H1 es la
// pantalla del pitch y esto se enseña al final, con el run terminado. Cerrado no ocupa
// ni un píxel.
//
// La regla que ordena el componente: **ningún número sin su procedencia**. Un run
// sintético lo dice, una cifra contada por el gateway lo dice, y un journal a medias lo
// dice. Dos columnas de números sin eso son una comparación que no se puede defender
// cuando alguien pregunta de dónde sale el 0,92.
import { useEffect, useState } from 'react'

import type { RunRow } from '../hooks/useRuns'
import { useRuns } from '../hooks/useRuns'
import { caveats, compare, defaultPair } from '../story/score'
import type { Event } from '../types'

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

  return (
    <div className="absolute inset-0 z-20 flex flex-col bg-vela-bg/98 p-6">
      <header className="flex shrink-0 items-baseline gap-4">
        <h2 className="text-2xl font-semibold text-vela-ink">aprendizaje entre runs</h2>
        <span className="text-sm text-vela-dim">
          {rows.length} run{rows.length === 1 ? '' : 's'} en runs/
          {loading && ' · leyendo…'}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto min-h-[2.5rem] rounded-[9px] border border-vela-edge bg-vela-panel px-4 text-base text-vela-ink hover:border-vela-accent hover:text-vela-accent"
        >
          cerrar · Esc
        </button>
      </header>

      {error && <p className="mt-4 text-base text-vela-warn">{error}</p>}

      {rows.length === 0 && !loading && !error ? (
        // El estado vacío dice qué va a aparecer y cómo llenarlo, no "sin datos".
        <p className="mt-8 text-base text-vela-dim">
          Todavía no hay runs. Cada demo terminada deja uno en <code>runs/</code>; para
          probar la comparación sin esperar:{' '}
          <code>uv run python scripts/fake_journal.py --runs 3 --dir runs/</code>
        </p>
      ) : (
        <div className="mt-6 min-h-0 flex-1 overflow-auto">
          <div className="grid grid-cols-[1fr_auto_auto_auto] items-baseline gap-x-6 gap-y-1">
            <Picker rows={rows} value={left} onChange={setLeft} label="run A" />
            <Picker rows={rows} value={right} onChange={setRight} label="run B" />

            {table.map((row) => (
              <Row key={row.label} {...row} />
            ))}
          </div>

          <div className="mt-8 grid grid-cols-2 gap-6">
            <Caveats row={a} title="run A" />
            <Caveats row={b} title="run B" />
          </div>
        </div>
      )}
    </div>
  )
}

function Picker({
  rows,
  value,
  onChange,
  label,
}: {
  rows: RunRow[]
  value: string | null
  onChange: (id: string) => void
  label: string
}) {
  return (
    // `col-start` deja la primera columna libre para los nombres de campo, y los dos
    // selectores caen justo encima de sus cifras.
    <label
      className={`${label === 'run A' ? 'col-start-2' : 'col-start-3'} row-start-1 flex flex-col gap-1 text-xs text-vela-dim`}
    >
      {label}
      <select
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        // 2.5rem de alto: se pulsa con el ratón y se lee desde el fondo de la sala.
        className="min-h-[2.5rem] rounded-[9px] border border-vela-edge bg-vela-panel px-3 text-base text-vela-ink"
      >
        {rows.map((r) => (
          <option key={r.run_id} value={r.run_id}>
            {r.run_id}
            {r.synthetic ? ' · sintético' : ''}
          </option>
        ))}
      </select>
    </label>
  )
}

function Row({
  label,
  a,
  b,
  verdict,
  delta,
}: {
  label: string
  a: string | null
  b: string | null
  verdict: 'mejor' | 'peor' | 'igual' | null
  delta: string | null
}) {
  // Verde y ámbar, nunca rojo: el rojo es del banner REPLAN y de nada más. Un run peor
  // no es una emergencia, es un dato.
  const tone =
    verdict === 'mejor'
      ? 'text-vela-good'
      : verdict === 'peor'
        ? 'text-vela-warn'
        : 'text-vela-dim'
  return (
    <>
      <span className="text-base text-vela-dim">{label}</span>
      <span className="text-right text-lg tabular-nums text-vela-ink">{a ?? '—'}</span>
      <span className="text-right text-lg tabular-nums text-vela-ink">{b ?? '—'}</span>
      <span className={`text-base tabular-nums ${tone}`}>
        {delta ?? (verdict === 'igual' ? '=' : '')}
      </span>
    </>
  )
}

function Caveats({ row, title }: { row: RunRow | null; title: string }) {
  const notes = caveats(row)
  if (!row) return null
  return (
    <div className="text-sm text-vela-dim">
      <p className="text-vela-ink">
        {title} · {row.run_id}
      </p>
      {notes.length === 0 ? (
        <p>puntuado por journal.score</p>
      ) : (
        <ul>
          {notes.map((n) => (
            <li key={n}>· {n}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
