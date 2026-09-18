// El banner REPLAN. Es lo que la sala tiene que leer sin que se lo señale.
//
// Tres decisiones y su motivo:
//
// - **Estado derivado, no un efecto que enciende y apaga.** El banner es una función
//   de `events`: el último `plan.replan.started` sin `plan.emitted` posterior. Un par
//   de `useState` sincronizados con el chorro se desincroniza en cuanto llega una
//   reconexión, y el banner se queda puesto (o no entra) sin que se sepa por qué.
// - **Va en una banda sobre la rejilla, nunca encima del mapa.** El clímax del H3 son
//   las flechas cambiando de destino, que ocurren justo después del replan: taparlas
//   con el banner sería esconder la mejor parte.
// - **`key={seq}` en el elemento animado.** La animación se reejecuta solo con un
//   replan NUEVO, no en cada render. A `VELA_REPLAY_SPEED=60` los replanes se
//   atropellan y sin esto el banner parpadea en el proyector.
import { useEffect, useState } from 'react'

import type { Event } from '../types'
import { mmss } from '../story/format'
import { currentReplan } from '../story/replan'

/** Si el plan no llega, el banner se va solo. Un banner permanente deja de significar
 *  "acaba de pasar algo", que es justo lo único que significa. */
const MAX_MS = 12_000

export function ReplanBanner({ events }: { events: Event[] }) {
  const replan = currentReplan(events)
  const [expiredSeq, setExpiredSeq] = useState<number | null>(null)

  // El único temporizador del dashboard, y está anclado al `seq`: si entra otro
  // replan, el anterior deja de importar y el reloj empieza de cero.
  useEffect(() => {
    if (!replan) return
    const timer = window.setTimeout(() => setExpiredSeq(replan.seq), MAX_MS)
    return () => window.clearTimeout(timer)
  }, [replan?.seq])

  const visible = replan && replan.seq !== expiredSeq

  return (
    // La banda ocupa sitio SIEMPRE, con replan y sin él: si apareciera y desapareciera
    // de la rejilla, el mapa y los paneles darían un salto de 56 px justo en el momento
    // en el que hay que mirarlos.
    <div className="flex h-14 shrink-0 items-center px-3">
      {visible && (
        <div
          key={replan.seq}
          className="vela-replan-enter flex w-full items-baseline gap-3 border-l-4 border-vela-replan bg-vela-panel px-3 py-2"
          role="status"
        >
          <span className="text-lg font-bold tracking-widest text-vela-replan">REPLAN</span>
          <span className="truncate text-xl text-vela-ink">{replan.reason}</span>
          <span className="ml-auto shrink-0 text-xs tabular-nums text-vela-dim">
            {replan.trigger} · {mmss(replan.t_sim)}
          </span>
        </div>
      )}
    </div>
  )
}
