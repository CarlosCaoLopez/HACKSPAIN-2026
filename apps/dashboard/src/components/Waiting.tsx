// La sala de espera de la /demo autoservicio: no hay run. Quien llega aquí viene de la
// landing (o ha llegado tarde y el run ya acabó). Se le dice qué hacer, no se le enseña
// un dashboard vacío como si algo fallara.
//
// Es una capa sobre el dashboard, no una ruta: «Ver el panel igualmente» la quita, que es
// lo que quiere quien desarrolla contra `make dev-voice` sin run.
import type { DemoStatus } from '../hooks/useDemoStatus'

export function Waiting({ status, onPeek }: { status: DemoStatus; onPeek: () => void }) {
  const roles = status.phone_roles
  return (
    <div className="absolute inset-0 z-20 flex items-center justify-center bg-vela-bg/95 p-6">
      <div className="w-full max-w-2xl rounded-2xl border border-vela-edge bg-vela-panel p-8 shadow-sm">
        <h1 className="text-3xl font-semibold text-vela-ink">Ahora mismo no hay ninguna demo en marcha</h1>
        <p className="mt-3 text-lg text-vela-dim">
          La demo se arranca desde la landing con cinco teléfonos. Cuando alguien pulse «empezar», esta
          pantalla pasa sola al Minecraft en directo y a las llamadas.
        </p>
        {status.landing_url && (
          <a
            href={status.landing_url}
            className="mt-5 inline-block rounded-lg bg-vela-ink px-4 py-2.5 text-white hover:opacity-90"
          >
            Ir a la landing y arrancar una demo
          </a>
        )}
        <div className="mt-8 grid gap-3 sm:grid-cols-2">
          {roles.map((r) => (
            <div key={r.key} className="rounded-lg border border-vela-edge p-3">
              <div className="font-medium text-vela-ink">{r.label}</div>
              <div className="text-sm text-vela-dim">{r.explica}</div>
            </div>
          ))}
        </div>
        {status.inbound_number && (
          <p className="mt-6 text-sm text-vela-dim">
            El 112 de la demo, al que llama el vecino:{' '}
            <span className="font-medium tabular-nums text-vela-ink">{status.inbound_number}</span>
          </p>
        )}
        <p className="mt-2 text-sm text-vela-dim">
          Cada demo dura {Math.round(status.run_max_s / 60)} minutos y solo puede haber una a la vez.
        </p>
        <button type="button" onClick={onPeek} className="mt-6 text-sm text-vela-dim underline hover:text-vela-ink">
          Ver el panel igualmente
        </button>
      </div>
    </div>
  )
}
