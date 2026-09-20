// El Minecraft en la web: el vídeo del cliente headless que corre junto al servidor, servido
// por MediaMTX como HLS. No es un canvas ni un visor propio: es la misma imagen que antes
// capturaba OBS de la segunda pantalla, ahora dentro de la página.
//
// HLS y no WebRTC por defecto: la wifi del evento solo deja salir 80/443 y bloquea UDP
// (medido, `scripts/retunnel.py`), y HLS va por HTTPS como cualquier otra petición. Se paga
// en latencia (unos segundos), que para una demo de llamadas de un minuto no se nota.
//
// Si el stream no arranca, se dice y se ofrece el mapa: nunca un rectángulo negro mudo.
import Hls from 'hls.js'
import { useEffect, useRef, useState } from 'react'

type CamState = 'loading' | 'playing' | 'error'

const STALL_MS = 15000

export function CamPanel({ url, className = '' }: { url: string; className?: string }) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [state, setState] = useState<CamState>('loading')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const video = videoRef.current
    if (!video || !url) return
    setState('loading')
    let hls: Hls | null = null
    // Sin imagen en 15 s: se da por caído y se ofrece reintentar. Un HLS que aún no tiene
    // segmentos (el cliente headless arrancando) tarda menos que eso en dar el primero.
    const stall = window.setTimeout(() => setState((s) => (s === 'playing' ? s : 'error')), STALL_MS)
    const onPlaying = () => setState('playing')
    video.addEventListener('playing', onPlaying)

    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      // Safari: HLS nativo.
      video.src = url
      video.play().catch(() => {})
    } else if (Hls.isSupported()) {
      hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 2, enableWorker: true })
      hls.loadSource(url)
      hls.attachMedia(video)
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        video.play().catch(() => {})
      })
      hls.on(Hls.Events.ERROR, (_ev, data) => {
        if (data.fatal) setState('error')
      })
    } else {
      setState('error')
    }

    return () => {
      window.clearTimeout(stall)
      video.removeEventListener('playing', onPlaying)
      hls?.destroy()
    }
  }, [url, attempt])

  return (
    <div className={`relative overflow-hidden rounded-xl border border-vela-edge bg-black ${className}`}>
      <video
        ref={videoRef}
        muted
        autoPlay
        playsInline
        className="h-full w-full object-contain"
        aria-label="Minecraft en directo"
      />
      {state !== 'playing' && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/70 text-center text-white">
          {state === 'loading' ? (
            <>
              <span className="text-lg">Conectando con el Minecraft…</span>
              <span className="text-sm text-white/70">La cámara entra en el servidor al arrancar el run. Unos segundos.</span>
            </>
          ) : (
            <>
              <span className="text-lg">Minecraft no disponible</span>
              <span className="max-w-md text-sm text-white/70">
                El vídeo no llega. El run sigue igual: las llamadas y el mapa no dependen de él.
              </span>
              <button
                type="button"
                onClick={() => setAttempt((n) => n + 1)}
                className="mt-2 rounded-lg border border-white/40 px-3 py-1.5 text-sm hover:bg-white/10"
              >
                Reintentar
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
