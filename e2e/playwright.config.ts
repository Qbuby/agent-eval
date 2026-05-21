import { defineConfig } from '@playwright/test'
import path from 'node:path'

// E2E suite for the Agent-Eval evaluation workbench.
// Targets the dockerized stack on http://localhost (frontend) — backend is
// reached transparently through the nginx /api proxy baked into the image.
export default defineConfig({
  testDir: './tests',
  globalSetup: path.resolve(__dirname, 'global-setup.ts'),
  timeout: 240_000, // tests 4 & 6 wait for real agent: 3 cases × ~30-50s
  expect: { timeout: 10_000 },
  fullyParallel: false, // tests share UI state (login + run history)
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: 'http://localhost',
    headless: false, // headed per user request
    viewport: { width: 1440, height: 900 },
    actionTimeout: 8_000,
    navigationTimeout: 15_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    storageState: path.resolve(__dirname, 'auth.json'),
  },
  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],
})
