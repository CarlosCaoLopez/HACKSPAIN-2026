// El marco común de los seis paneles. Solo cromo: título, contador y hueco.
//
// `note` y `bodyRef` se añadieron en H3 y son aditivos: `note` para que un panel diga
// en su cabecera algo que el usuario necesita saber para interpretar lo que ve (qué
// criterio de orden usa, si la geometría es provisional), y `bodyRef` para que el panel
// de cambios pueda decidir si hace auto-scroll o no sin sacar el scroll del marco.
import type { ReactNode, Ref } from 'react'

export function Panel({
  title,
  count,
  note,
  bodyRef,
  className = '',
  children,
}: {
  title: string
  count?: number
  note?: ReactNode
  bodyRef?: Ref<HTMLDivElement>
  className?: string
  children?: ReactNode
}) {
  return (
    // min-h-0 + overflow-hidden: REQ-018, el scroll vive dentro del panel y
    // nunca en el body. Un panel que empuja la rejilla rompe la proyección.
    <section
      className={`flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-vela-edge bg-vela-panel ${className}`}
    >
      <header className="flex shrink-0 items-baseline gap-2 border-b border-vela-edge px-3 py-2">
        <h2 className="text-sm font-semibold tracking-wide text-vela-ink uppercase">
          {title}
        </h2>
        {note && <span className="truncate text-xs text-vela-dim">{note}</span>}
        {count !== undefined && (
          <span className="ml-auto text-xs tabular-nums text-vela-accent">{count}</span>
        )}
      </header>
      <div
        ref={bodyRef}
        className="min-h-0 flex-1 overflow-auto p-3 text-sm text-vela-dim"
      >
        {children ?? <Empty />}
      </div>
    </section>
  )
}

export function Empty() {
  return <p className="text-vela-dim">sin datos</p>
}
