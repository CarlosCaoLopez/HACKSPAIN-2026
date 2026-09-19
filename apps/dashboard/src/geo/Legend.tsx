// La leyenda del mapa real. SPEC-008 REQ-291, REQ-303.
//
// Sale del MISMO catálogo que los marcadores (`icons.ts`) y enseña SOLO los iconos que
// hay en pantalla: una leyenda con los veinte símbolos posibles ocuparía sitio para decir
// cosas que no están pasando. Plegable y abierta por defecto: también ocupa sitio.
import { useState } from 'react'

import type { Problem } from '../map/problems'
import type { Wind } from '../types'
import type { IconDef } from './icons'

export interface LegendItem {
  key: string
  def: IconDef
}

function Glyph({ def }: { def: IconDef }) {
  return (
    <span className={`vela-tone-${def.tone} inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 border-current bg-vela-marker`}>
      <svg
        viewBox="0 0 24 24"
        fill={def.solid ? 'currentColor' : 'none'}
        stroke="currentColor"
        strokeWidth={2.2}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="h-3 w-3"
        aria-hidden
        // `body` es una constante de `icons.ts`, nunca un dato del run.
        dangerouslySetInnerHTML={{ __html: def.body }}
      />
    </span>
  )
}

export function Legend({
  items,
  wind,
  windSource,
  problems,
  unplacedCalls,
  firmsNote,
}: {
  items: LegendItem[]
  wind: Wind | null
  windSource: string
  problems: Problem[]
  unplacedCalls: number
  /** `FIRMS: sin focos` cuando la fuente responde y no hay ninguno; `null` si no procede. */
  firmsNote: string | null
}) {
  const [open, setOpen] = useState(true)

  return (
    <div className="absolute bottom-8 left-3 z-[500] max-h-[55%] w-64 overflow-auto rounded-[9px] border border-vela-edge bg-vela-panel/90 text-[13px] shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex min-h-[2rem] w-full items-center justify-between px-2.5 text-xs font-semibold text-vela-dim"
        aria-expanded={open}
      >
        Leyenda
        <span aria-hidden>{open ? '–' : '+'}</span>
      </button>
      {open && (
        <div className="flex flex-col gap-2 px-2.5 pb-2">
          {items.length > 0 && (
            <ul className="flex flex-col gap-1">
              {items.map((it) => (
                <li key={it.key} className="flex items-center gap-2 text-vela-ink">
                  <Glyph def={it.def} />
                  {it.def.label}
                </li>
              ))}
            </ul>
          )}
          {wind && (
            <p className="text-vela-ink">
              viento {Math.round(wind.bearing_deg)}° · {wind.speed.toFixed(1)} · {windSource}
            </p>
          )}
          {firmsNote && <p className="text-vela-dim">{firmsNote}</p>}
          {unplacedCalls > 0 && (
            <p className="text-vela-warn">
              {unplacedCalls} {unplacedCalls === 1 ? 'llamada sin ubicar' : 'llamadas sin ubicar'}
            </p>
          )}
          <div>
            <p className="text-xs font-semibold text-vela-dim">Zonas con problema · {problems.length}</p>
            {problems.length === 0 ? (
              <p className="text-vela-ink">Sin zonas con problema.</p>
            ) : (
              <ul className="mt-1 flex flex-col gap-1">
                {problems.map((p) => (
                  <li key={p.id} className="flex items-baseline gap-2 text-vela-ink">
                    <span
                      aria-hidden
                      className={`inline-block h-2 w-2 shrink-0 rounded-full ${p.level === 2 ? 'bg-vela-fire' : 'bg-vela-alert'}`}
                    />
                    {p.text}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
