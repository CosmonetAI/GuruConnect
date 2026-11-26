import { defineConfig, devices } from '@playwright/test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const data = mkdtempSync(join(tmpdir(), 'guruconnect-browser-'))
const environment = {
  ENVIRONMENT: 'development', DATABASE_URL: '', SUPABASE_URL: '', SUPABASE_SERVICE_ROLE_KEY: '',
  EMBEDDED_PROCESSING_ENABLED: 'true', ELEVENLABS_API_KEY: '', OPENAI_API_KEY: '',
  SUPABASE_ANON_KEY: '', CREDENTIAL_ENCRYPTION_KEY: '', LOCAL_DATA_DIR: data,
  CORS_ORIGINS: '["http://127.0.0.1:5177","http://localhost:5177"]',
  VITE_SUPABASE_URL: '', VITE_SUPABASE_PUBLISHABLE_KEY: '', VITE_SUPABASE_ANON_KEY: '',
      VITE_API_URL: 'http://127.0.0.1:8011',
}
export default defineConfig({
  testDir: './browser', fullyParallel: false, workers: 1, forbidOnly: !!process.env.CI,
  retries: 0, timeout: 60000, reporter: [['list']],
  use: { baseURL: 'http://127.0.0.1:5177', channel: 'chrome', trace: 'retain-on-failure', screenshot: 'only-on-failure',
    permissions: ['microphone'], launchOptions: { args: [
      '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
      `--use-file-for-fake-audio-capture=${resolve('tests/fixtures/microphone.wav')}`, '--autoplay-policy=no-user-gesture-required',
    ] },
  },
  webServer: [
    { command: 'npm run dev:api -- --port 8011', url: 'http://127.0.0.1:8011/ready', reuseExistingServer: false, timeout: 60000, env: environment },
    { command: 'npm run dev:web -- --port 5177', url: 'http://127.0.0.1:5177', reuseExistingServer: false, timeout: 180000, env: environment },
  ],
  projects: [{ name: 'local-chromium', use: { ...devices['Desktop Chrome'] } }],
})
