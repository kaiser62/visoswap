import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': `http://127.0.0.1:${process.env.BACKEND_PORT || 8000}`,
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // vitest's default include also matches e2e/*.spec.ts (Playwright owns those).
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
