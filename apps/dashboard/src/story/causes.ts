// La cadena de causas: llamada → hecho → violación → replan → orden.
//
// `causes` es una lista de `seq` en el sobre del bus. Resolverla es lo que permite
// contestar "¿por qué has hecho eso?" en pantalla y sin narración, que es el criterio
// *Qué información importa* del reto.
//
// Dos decisiones de implementación con motivo:
//
// - El índice `seq → evento` se mantiene **incremental** (`extend`) en vez de recorrer
//   el array por cada fila: con 2000 eventos y 40 filas visibles, lo segundo es O(n·m)
//   en cada render.
// - Si el causante ya salió del anillo de 2000 o es anterior a la última reconexión,
//   se devuelve `outOfWindow` y la fila lo dice. Preferimos admitir que no lo sabemos a
//   enseñar media cadena como si estuviera completa.
import type { Event } from '../types'

export type CauseIndex = Map<number, Event>

export function indexBySeq(events: Event[]): CauseIndex {
  return new Map(events.map((ev) => [ev.seq, ev]))
}

/** Añade al índice solo lo que no estaba. Mutar es lo que lo hace incremental. */
export function extend(index: CauseIndex, events: Event[]): CauseIndex {
  for (const ev of events) if (!index.has(ev.seq)) index.set(ev.seq, ev)
  return index
}

export interface Chain {
  /** Del más cercano al más lejano: el primero es la causa directa. */
  links: Event[]
  /** Había una causa declarada pero ya no está en memoria. */
  outOfWindow: boolean
}

const MAX_LINKS = 3

/** Los ancestros de un evento por `causes`, hasta tres eslabones.
 *
 *  Se sigue solo la **primera** causa de cada nivel: un evento puede tener varias
 *  (`plan.replan.started` viene de la violación y de la divergencia), pero una fila de
 *  panel cuenta una línea narrativa, no un árbol. El árbol completo está en el journal
 *  para quien quiera depurarlo. */
export function chain(ev: Event, index: CauseIndex, max = MAX_LINKS): Chain {
  const links: Event[] = []
  let current = ev
  let outOfWindow = false

  while (links.length < max) {
    const causedBy = current.causes[0]
    if (causedBy === undefined) break // llegamos al principio de la cadena
    const parent = index.get(causedBy)
    if (!parent) {
      outOfWindow = true
      break
    }
    links.push(parent)
    current = parent
  }
  return { links, outOfWindow }
}
