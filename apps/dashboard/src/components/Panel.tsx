// El marco común de los seis paneles (SPEC-009): tarjeta, cabecera en dos líneas, franja de
// cifras, fila y sección. Solo cromo: ningún panel decide aquí qué enseña.
//
// Todo lo que un panel pinta pasa por estas piezas y no por clases sueltas, porque el problema
// que resuelven es justo ese: seis paneles acabados cada uno por su lado se leían como seis
// productos distintos (seis gramáticas de fila, cinco colores de acento). Si una fila o una
// cifra se escribe igual en los seis sitios, se lee igual.
//
// Color (REQ-321, enmienda de REQ-200): gris para todo, y solo dos tonos de dato —rojo si
// hay que actuar, verde si está resuelto—. Por eso `tone` es una unión cerrada y no un
// `className`: un tercer color no se puede colar sin tocar este fichero.
import type { ReactNode, Ref } from 'react'

/** Los dos únicos tonos de dato (REQ-321). La lista de qué es urgente y qué es resuelto es
 *  cerrada y vive en la spec; aquí solo se pinta. */
export type DataTone = 'urgent' | 'done'

const TONE: Record<DataTone, string> = {
  urgent: 'text-vela-replan',
  done: 'text-vela-good',
}

export function toneClass(tone: DataTone | undefined, fallback = 'text-vela-dim'): string {
  return tone ? TONE[tone] : fallback
}

export type Stat = { label: string; value: string | number; tone?: DataTone }

export function Panel({
  title,
  subtitle,
  stats,
  action,
  bodyRef,
  flush = false,
  className = '',
  children,
}: {
  title: string
  /** El recuento con su sustantivo y, si aplica, el estado: `4 órdenes · 1 en curso`
   *  (REQ-315). Un número suelto sin sustantivo no dice de qué es. */
  subtitle: string
  /** La franja de cifras (REQ-316). Fuera del scroll: es el resumen del panel y tiene que
   *  seguir a la vista cuando se baja por la lista. */
  stats?: Stat[]
  /** Un control a la derecha de la cabecera, como el `…` de la referencia. Hoy solo lo usa
   *  el `Cerrar` de la ventana de runs. */
  action?: ReactNode
  bodyRef?: Ref<HTMLDivElement>
  /** Sin relleno en el cuerpo, para lo que va de borde a borde. */
  flush?: boolean
  className?: string
  children: ReactNode
}) {
  return (
    // min-h-0 + overflow-hidden: el scroll vive dentro del panel (REQ-018).
    <section
      className={`flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-vela-edge bg-vela-panel ${className}`}
    >
      <header className="flex shrink-0 items-start gap-4 border-b border-vela-edge px-5 pb-3.5 pt-4">
        <div className="min-w-0 flex-1">
          <h2 className="text-lg font-semibold text-vela-ink">{title}</h2>
          <p className="truncate text-sm text-vela-dim">{subtitle}</p>
        </div>
        {action}
      </header>
      {stats && stats.length > 0 && <Stats stats={stats} />}
      <div
        ref={bodyRef}
        className={`min-h-0 flex-1 overflow-auto text-base text-vela-dim ${flush ? '' : 'px-5 py-1'}`}
      >
        {children}
      </div>
    </section>
  )
}

function Stats({ stats }: { stats: Stat[] }) {
  return (
    <dl
      className="grid shrink-0 gap-4 border-b border-vela-edge px-5 py-3"
      style={{ gridTemplateColumns: `repeat(${stats.length}, minmax(0, 1fr))` }}
    >
      {stats.map((stat) => (
        // `dt` antes que `dd` como pide el HTML; `flex-col-reverse` pone la cifra arriba.
        <div key={stat.label} className="flex min-w-0 flex-col-reverse">
          <dt className="truncate text-sm text-vela-dim">{stat.label}</dt>
          <dd className={`text-2xl tabular-nums ${toneClass(stat.tone, 'text-vela-ink')}`}>
            {stat.value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/** La lista de filas: divisor fino entre una y otra, nada de bordes a la izquierda ni
 *  tarjetas dentro de la tarjeta (REQ-317). */
export function Rows({ children }: { children: ReactNode }) {
  return <ol className="divide-y divide-vela-edge">{children}</ol>
}

/** La única fila de la rejilla (REQ-317): hora · frase y línea secundaria · estado.
 *
 *  `hint` va al `title`: es donde viven los identificadores que no contestan nada a quien
 *  mira (`seq`, `action_id`, `call_id`) pero que la procedencia exige poder ver (REQ-090). */
export function Row({
  time,
  primary,
  meta,
  status,
  tone,
  hint,
  rowRef,
  className = '',
  children,
}: {
  time?: string
  primary: ReactNode
  meta?: ReactNode
  status?: ReactNode
  tone?: DataTone
  hint?: string
  rowRef?: Ref<HTMLLIElement>
  className?: string
  /** Lo que va debajo de la fila y es de ella: botones, transcripción. */
  children?: ReactNode
}) {
  return (
    <li ref={rowRef} title={hint} className={`flex gap-3 py-2.5 ${className}`}>
      {time !== undefined && (
        <span className="w-12 shrink-0 pt-0.5 text-sm tabular-nums text-vela-dim">{time}</span>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-3">
          <div className="min-w-0 flex-1 text-vela-ink">{primary}</div>
          {status !== undefined && (
            <span className={`shrink-0 text-sm tabular-nums ${toneClass(tone)}`}>{status}</span>
          )}
        </div>
        {meta !== undefined && <div className="text-sm text-vela-dim">{meta}</div>}
        {children}
      </div>
    </li>
  )
}

/** Una subdivisión de panel (REQ-318). Vacía se dice en una línea, no desaparece: que no
 *  haya cortes es un dato. El cuerpo lo pone quien llama (casi siempre `Rows`; en la cola,
 *  también barras y un formulario). */
export function Section({
  title,
  n,
  empty,
  children,
}: {
  title: string
  n: number
  empty: string
  children: ReactNode
}) {
  return (
    <section className="py-3">
      <h3 className="flex items-baseline gap-2 text-sm font-semibold text-vela-ink">
        {title}
        <span className="font-normal tabular-nums text-vela-dim">{n}</span>
      </h3>
      {n === 0 ? <p className="pt-1 text-sm text-vela-dim">{empty}</p> : children}
    </section>
  )
}

/** El vacío dice qué va a aparecer y qué lo dispara, nunca «sin datos» (REQ-193). Recibe la
 *  frase y no tiene una por defecto: un vacío genérico es el problema. */
export function Empty({ children }: { children: string }) {
  return <p className="py-3 text-vela-dim">{children}</p>
}

/** El truncado se confiesa (REQ-195): cortar en silencio deja abierta la pregunta «¿lo estás
 *  enseñando todo?». */
export function Truncated({ n }: { n: number }) {
  if (n <= 0) return null
  return <p className="py-2 text-sm text-vela-dim">+ {n} anteriores</p>
}

/** Esqueleto entre la reconexión y el snapshot (REQ-202), con la anatomía de `Row`
 *  (REQ-331): hora y dos líneas. Anchos distintos para que se lea como filas que están al
 *  llegar y no como un patrón decorativo. */
const SKELETON_WIDTHS = ['w-3/4', 'w-1/2', 'w-5/6', 'w-2/3', 'w-1/2', 'w-4/5']

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="divide-y divide-vela-edge" aria-hidden>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex gap-3 py-3">
          <div className="vela-skeleton h-3 w-10 shrink-0 rounded" />
          <div className="flex flex-1 flex-col gap-1.5">
            <div className={`vela-skeleton h-4 rounded ${SKELETON_WIDTHS[i % SKELETON_WIDTHS.length]}`} />
            <div className="vela-skeleton h-3 w-1/3 rounded" />
          </div>
        </div>
      ))}
    </div>
  )
}
