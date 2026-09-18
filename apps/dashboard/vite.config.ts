import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// El gateway es un solo proceso en :8000 (Makefile, `dev-dash` y `dev-voice`).
// El dashboard no hace polling de nada: `/ws` es el chorro y el resto son
// endpoints puntuales.
const GATEWAY = 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: GATEWAY, changeOrigin: true },
      '/control': { target: GATEWAY, changeOrigin: true },
      '/ws': { target: GATEWAY, ws: true },
    },
  },
})
