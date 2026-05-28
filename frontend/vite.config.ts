import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    // За nginx (docker :8080 или VPS) — иначе Vite отвечает 403
    allowedHosts: ['localhost', '127.0.0.1', 'mateplace.ru', 'www.mateplace.ru'],
    proxy: {
      '/api': 'http://backend:8000',
    },
  },
})
