import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

const renderHostname = process.env.RENDER_EXTERNAL_HOSTNAME

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5176, strictPort: true },
  preview: {
    host: '0.0.0.0',
    port: Number(process.env.PORT) || 4173,
    allowedHosts: renderHostname ? [renderHostname] : ['.onrender.com'],
  },
  test: { environment: 'jsdom', globals: true, setupFiles: ['./src/test-setup.ts'], include: ['src/**/*.test.{ts,tsx}'], restoreMocks: true },
})
