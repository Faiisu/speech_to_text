import { defineConfig } from '@playwright/test'

const backendPort = 8766
const frontendPort = 4173
const python = process.env.PYTHON || '.venv/bin/python'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    browserName: 'chromium',
    headless: true,
  },
  webServer: [
    {
      command: `${python} -m uvicorn tests.frontend_e2e.fake_backend:app --host 127.0.0.1 --port ${backendPort}`,
      cwd: '../..',
      url: `http://127.0.0.1:${backendPort}/api/v1/profiles`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `VITE_BACKEND_URL=http://127.0.0.1:${backendPort} npm run dev -- --host 127.0.0.1 --port ${frontendPort}`,
      url: `http://127.0.0.1:${frontendPort}`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
})
