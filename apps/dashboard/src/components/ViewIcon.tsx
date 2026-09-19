// Iconos de las dos vistas. SVG en línea: sin librería de iconos (SPEC-008 REQ-270).
// Los usan la sidebar y la cabecera de cada vista, y por eso viven aparte.
import type { View } from '../hooks/useView'

export function ViewIcon({ view, className = 'h-5 w-5' }: { view: View; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      {view === 'dashboards' ? (
        <>
          <rect x="3" y="3" width="8" height="8" rx="1.5" />
          <rect x="13" y="3" width="8" height="8" rx="1.5" />
          <rect x="3" y="13" width="8" height="8" rx="1.5" />
          <rect x="13" y="13" width="8" height="8" rx="1.5" />
        </>
      ) : (
        <>
          <path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z" />
          <circle cx="12" cy="10" r="2.5" />
        </>
      )}
    </svg>
  )
}

export const VIEW_TITLE: Record<View, string> = {
  dashboards: 'Dashboards',
  mapa: 'Mapa',
}

/** Cabecera de vista al estilo Factorial (REQ-273): cuadrado de acento con el icono y el
 *  título. Se queda en 56 px para no robarle alto a los paneles ni al mapa. */
export function ViewHeader({ view, children }: { view: View; children?: React.ReactNode }) {
  return (
    <div className="flex h-14 shrink-0 items-center gap-3 px-4">
      <span className="flex h-8 w-8 items-center justify-center rounded-[9px] bg-vela-accent text-white">
        <ViewIcon view={view} className="h-[18px] w-[18px]" />
      </span>
      <h1 className="text-2xl font-semibold text-vela-ink">{VIEW_TITLE[view]}</h1>
      {children}
    </div>
  )
}
