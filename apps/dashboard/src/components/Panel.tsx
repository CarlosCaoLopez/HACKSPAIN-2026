// El marco común de los seis paneles. Solo cromo: título, contador y hueco.
//
// `note` y `bodyRef` se añadieron en H3 y son aditivos: `note` para que un panel diga
// en su cabecera algo que el usuario necesita saber para interpretar lo que ve (qué
// criterio de orden usa, qué escenario dibuja), y `bodyRef` para que el panel
// de cambios pueda decidir si hace auto-scroll o no sin sacar el scroll del marco.
//
// El acabado (REQ-193…200) pasa casi entero por aquí, y los tres vacíos que exporta
// —`Empty`, `Truncated`, `Skeleton`— son la razón: la frase de un panel vacío, la
// confesión del truncado y el hueco de la reconexión tienen que decirse IGUAL en los seis
// sitios, o cada panel parece un producto distinto.
import type { ReactNode, Ref } from 'react'

/** Nivel de la jerarquía visual (REQ-196). Sin navegación que ordene la pantalla, la
 *  única jerarquía disponible es el borde: el mapa es el 1 y los otros cinco el 2, y eso
 *  tiene que verse sin leer un título desde el fondo de la sala.
 *
 *  Es una prop y no un `className` a propósito: `border-vela-edge` y un override en el
 *  atributo tienen la misma especificidad, así que gana el orden de la hoja de estilos y
 *  no el que uno escriba. Aquí se elige una clase o la otra, y no hay duda. */
const BORDER: Record<1 | 2, string> = {
  1: 'border-vela-edge-bright',
  2: 'border-vela-edge',
}

export function Panel({
  title,
  count,
  note,
  bodyRef,
  level = 2,
  flush = false,
  className = '',
  children,
}: {
  title: string
  count?: number
  note?: ReactNode
  bodyRef?: Ref<HTMLDivElement>
  level?: 1 | 2
  /** Sin relleno en el cuerpo: el mapa es una imagen y va de borde a borde. Aditiva. */
  flush?: boolean
  className?: string
  children: ReactNode
}) {
  return (
    // min-h-0 + overflow-hidden: REQ-018, el scroll vive dentro del panel y
    // nunca en el body. Un panel que empuja la rejilla rompe la proyección.
    <section
      className={`flex h-full min-h-0 flex-col overflow-hidden rounded-[10px] border bg-vela-panel ${BORDER[level]} ${className}`}
    >
      <header className="flex shrink-0 items-baseline gap-2 border-b border-vela-edge px-3.5 pb-2.5 pt-3">
        {/* Caja baja (REQ-198): las versales a diez metros pierden la silueta de la
            palabra y se leen más despacio. El peso hace el mismo trabajo mejor. */}
        <h2 className="text-[15px] font-semibold text-vela-ink">{title}</h2>
        {note && <span className="truncate text-xs text-vela-dim">{note}</span>}
        {count !== undefined && (
          // Nivel *registro*, no acento (REQ-197, REQ-200): el número de filas es para
          // mí, no para la sala. Y se enseña SIEMPRE, también en cero (REQ-194): un
          // hueco donde debería haber un número se lee como fallo; un 0 es un dato.
          <span className="ml-auto text-xs tabular-nums text-vela-dim">{count}</span>
        )}
      </header>
      <div
        ref={bodyRef}
        className={`min-h-0 flex-1 overflow-auto text-base text-vela-dim ${flush ? '' : 'p-3.5'}`}
      >
        {children}
      </div>
    </section>
  )
}

/** El vacío dice qué va a aparecer y qué lo dispara, nunca «sin datos» (REQ-193).
 *
 *  Seis paneles diciendo «sin datos» en el segundo 0 del pitch no se leen como *aún no ha
 *  pasado nada*: se leen como *está roto*. Y un vacío que cuenta qué lo va a llenar enseña
 *  a la sala qué mirar antes de que ocurra, que es narración gratis.
 *
 *  Recibe la frase y no tiene una por defecto: un vacío genérico es el problema. */
export function Empty({ children }: { children: string }) {
  return <p className="text-vela-dim">{children}</p>
}

/** El truncado se confiesa (REQ-195).
 *
 *  Cortar la lista en silencio deja abierta la pregunta «¿lo estás enseñando todo?», que
 *  es justo la que hace un jurado técnico. Cuesta una línea. */
export function Truncated({ n }: { n: number }) {
  if (n <= 0) return null
  return <p className="mt-2 text-xs text-vela-dim">+ {n} anteriores</p>
}

/** Esqueleto entre la reconexión y el snapshot (REQ-202).
 *
 *  Anchos distintos y no todos iguales: una rejilla perfecta se lee como un patrón
 *  decorativo, y estas barras tienen que leerse como filas que están al llegar. */
const SKELETON_WIDTHS = ['w-3/4', 'w-1/2', 'w-5/6', 'w-2/3', 'w-1/2', 'w-4/5']

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-3" aria-hidden>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex flex-col gap-1">
          <div className={`vela-skeleton h-2 rounded ${SKELETON_WIDTHS[i % SKELETON_WIDTHS.length]}`} />
          <div className="vela-skeleton h-4 w-full rounded" />
        </div>
      ))}
    </div>
  )
}
