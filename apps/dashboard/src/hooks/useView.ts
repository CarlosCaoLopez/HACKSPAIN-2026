// La vista activa vive en el hash de la URL (SPEC-008 REQ-272).
//
// Cuatro vistas siguen sin justificar un router: con el hash, recargar conserva la
// vista y puedo abrir el proyector directamente en `#/mapa`. Un hash desconocido cae en
// Dashboards.
//
// Los ids van sin tilde a propósito (`simulacion`): el navegador percent-codifica el
// hash, así que `#/simulación` no casaría nunca con el literal.
import { useCallback, useEffect, useState } from 'react'

export type View = 'dashboards' | 'mapa' | 'simulacion' | 'minecraft'

const VIEWS: readonly View[] = ['dashboards', 'mapa', 'simulacion', 'minecraft']

function fromHash(): View {
  const name = window.location.hash.replace(/^#\/?/, '')
  return VIEWS.find((v) => v === name) ?? 'dashboards'
}

export function useView(): [View, (next: View) => void] {
  const [view, setView] = useState<View>(fromHash)

  useEffect(() => {
    const onChange = () => setView(fromHash())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  const go = useCallback((next: View) => {
    // Escribir el hash dispara `hashchange`, que es lo que actualiza el estado: una sola
    // ruta de actualización, así el botón atrás y el clic no pueden divergir.
    window.location.hash = `/${next}`
  }, [])

  return [view, go]
}
