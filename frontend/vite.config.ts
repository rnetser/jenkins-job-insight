import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath } from 'url'

// SPA-friendly bypass: return index.html for browser navigation
const spaBypass = (req: { headers: { accept?: string } }) => {
  if (req.headers.accept?.includes('text/html')) return '/index.html'
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/results': {
        target: 'http://localhost:8000',
        bypass: spaBypass,
      },
      '/history': {
        target: 'http://localhost:8000',
        bypass: spaBypass,
      },
      '/health': 'http://localhost:8000',
      '/ai-configs': 'http://localhost:8000',
    },
  },
})
