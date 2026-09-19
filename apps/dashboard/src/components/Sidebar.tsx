// La sidebar (SPEC-008 REQ-270, REQ-271): marca, las dos vistas y, anclado abajo, el
// estado que antes vivía en la cabecera. Al estilo del menú de Factorial.
//
// Todo llega por props: los hooks del chorro siguen montados una sola vez en `App`
// (REQ-275), así que cambiar de vista no reconecta el WebSocket.
import { useState } from 'react'

import type { Health } from '../hooks/useHealth'
import type { View } from '../hooks/useView'
import type { WorldState } from '../types'
import { mmss } from '../story/format'
import { VIEW_TITLE, ViewIcon } from './ViewIcon'

const VIEWS: readonly View[] = ['dashboards', 'mapa']
const STORAGE_KEY = 'vela.sidebar.collapsed'

/** El plegado se recuerda, pero el almacenamiento puede no existir (ventana privada,
 *  datos bloqueados): sin él, arranca desplegada y no pasa nada (REQ-271). */
function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

function writeCollapsed(value: boolean) {
  try {
    window.localStorage.setItem(STORAGE_KEY, value ? '1' : '0')
  } catch {
    // Sin almacenamiento la preferencia no persiste; la sidebar sigue funcionando.
  }
}

/** Un plan B en marcha, dicho en la barra. Gris y no ámbar (SPEC-009 REQ-337): es una
 *  aclaración de cómo se está ejecutando, no algo que haya que atender. */
function Badge({ children }: { children: string }) {
  return (
    <span className="rounded-md border border-vela-edge px-2 py-0.5 text-xs text-vela-dim">
      {children}
    </span>
  )
}

/** El estado del run en una palabra y un punto (REQ-336). Sin conexión va primero: si el
 *  socket ha caído, lo demás que diga la barra puede estar desfasado. */
function runStatus(connected: boolean, finished: boolean): { text: string; dot: string } {
  if (!connected) return { text: 'Sin conexión', dot: 'bg-vela-replan' }
  if (finished) return { text: 'Run terminado', dot: 'bg-vela-dim' }
  return { text: 'En directo', dot: 'bg-vela-good' }
}

export function Sidebar({
  view,
  onView,
  scenarioId,
  finished,
  health,
  state,
  connected,
  eventCount,
  onRuns,
}: {
  view: View
  onView: (next: View) => void
  scenarioId: string | null
  finished: boolean
  health: Health | null
  state: WorldState | null
  connected: boolean
  eventCount: number
  onRuns: () => void
}) {
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const status = runStatus(connected, finished)

  const toggle = () => {
    setCollapsed((prev) => {
      writeCollapsed(!prev)
      return !prev
    })
  }

  return (
    <nav
      className={`flex shrink-0 flex-col bg-vela-bg py-3 ${collapsed ? 'w-16 px-2' : 'w-60 px-3'}`}
      aria-label="Vistas"
    >
      <div className={`flex items-center ${collapsed ? 'flex-col gap-3' : 'justify-between px-1'}`}>
        {/* La marca del diseño: cuadrado oscuro con un punto blanco. */}
        <span className="flex items-center gap-2.5">
          <span className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-[9px] bg-vela-ink">
            <span className="h-2.5 w-2.5 rounded-[3px] bg-vela-panel" />
          </span>
          {!collapsed && (
            <span className="text-xl font-bold tracking-[0.14em] text-vela-ink">VELA</span>
          )}
        </span>
        <button
          type="button"
          onClick={toggle}
          title={collapsed ? 'Desplegar menú' : 'Plegar menú'}
          aria-label={collapsed ? 'Desplegar menú' : 'Plegar menú'}
          className="flex h-8 w-8 items-center justify-center rounded-lg text-vela-dim hover:bg-vela-edge/60 hover:text-vela-ink"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-[18px] w-[18px]">
            <rect x="3" y="4" width="18" height="16" rx="3" />
            <path d="M9 4v16" />
          </svg>
        </button>
      </div>

      <ul className="mt-6 flex flex-col gap-1">
        {VIEWS.map((v) => {
          const active = v === view
          return (
            <li key={v}>
              <button
                type="button"
                onClick={() => onView(v)}
                title={VIEW_TITLE[v]}
                aria-current={active ? 'page' : undefined}
                // 2.5rem: se pulsa con el ratón en directo y se lee a diez metros (REQ-148).
                className={`flex min-h-[2.5rem] w-full items-center gap-3 rounded-[9px] text-[15px] ${
                  collapsed ? 'justify-center px-0' : 'px-3'
                } ${
                  active
                    ? 'bg-vela-edge font-medium text-vela-ink'
                    : 'text-vela-dim hover:bg-vela-edge/60 hover:text-vela-ink'
                }`}
              >
                <ViewIcon view={v} />
                {!collapsed && VIEW_TITLE[v]}
              </button>
            </li>
          )
        })}
      </ul>

      {/* Estado, no promesa (REQ-198): si el run sigue vivo, de qué escenario y por dónde
          va. Abajo del todo, como el workspace de Factorial. `seq` y eventos, al `title`
          (REQ-336): son para depurar, y en pantalla se leían como cifras sin sentido. */}
      <div
        className="mt-auto flex flex-col gap-2 px-1"
        title={`seq ${state?.seq ?? '—'} · ${eventCount} eventos`}
      >
        {/* El tiempo que avanza va pegado al punto (enmienda de REQ-201): si el chorro se
            para, el reloj se queda quieto justo al lado de lo que lo explica. */}
        <span className="flex items-center gap-2 text-[13px]">
          <span className={`h-2 w-2 shrink-0 rounded-full ${status.dot}`} aria-hidden />
          {!collapsed && (
            <>
              <span className={connected ? 'text-vela-ink' : 'text-vela-replan'}>{status.text}</span>
              {state && (
                <span className="ml-auto tabular-nums text-vela-dim">t {mmss(state.t_sim)}</span>
              )}
            </>
          )}
        </span>
        {!collapsed && scenarioId && <span className="text-[13px] text-vela-dim">{scenarioId}</span>}
        {!collapsed && (health?.calls === 'simuladas' || health?.minecraft === 'apagado') && (
          <span className="flex flex-wrap gap-1.5">
            {health?.calls === 'simuladas' && <Badge>Llamadas simuladas</Badge>}
            {health?.minecraft === 'apagado' && <Badge>Sin Minecraft</Badge>}
          </span>
        )}
        <button
          type="button"
          onClick={onRuns}
          title="Comparar runs"
          // Neutro también al terminar (REQ-337): el punto ya dice que el run acabó.
          className="mt-1 min-h-[2.5rem] rounded-lg border border-vela-edge bg-vela-panel text-sm text-vela-ink hover:border-vela-ink"
        >
          {collapsed ? '⇄' : 'Comparar runs'}
        </button>
      </div>
    </nav>
  )
}
