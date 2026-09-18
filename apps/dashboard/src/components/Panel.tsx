// El marco común de los seis paneles. Solo cromo: título, contador y hueco.
// La lógica de cada uno llega en H3 y H4.
import type { ReactNode } from 'react'

export function Panel({
  title,
  count,
  className = '',
  children,
}: {
  title: string
  count?: number
  className?: string
  children?: ReactNode
}) {
  return (
    // min-h-0 + overflow-hidden: REQ-018, el scroll vive dentro del panel y
    // nunca en el body. Un panel que empuja la rejilla rompe la proyección.
    <section
      className={`flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-vela-edge bg-vela-panel ${className}`}
    >
      <header className="flex shrink-0 items-baseline justify-between border-b border-vela-edge px-3 py-2">
        <h2 className="text-sm font-semibold tracking-wide text-vela-ink uppercase">
          {title}
        </h2>
        {count !== undefined && (
          <span className="text-xs tabular-nums text-vela-accent">{count}</span>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-auto p-3 text-sm text-vela-dim">
        {children ?? <Empty />}
      </div>
    </section>
  )
}

export function Empty() {
  return <p className="text-vela-dim">sin datos</p>
}
