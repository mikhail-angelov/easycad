import { defineConfig, devices } from '@playwright/test'

// SPEC22 acceptance harness. Requires a running server (default :8852) and,
// for the generation/STL parts, a working backend LLM key. Run with:
//   npm i -D @playwright/test && npx playwright install chromium
//   EASYCAD_URL=http://localhost:8852 npx playwright test -c e2e
export default defineConfig({
  testDir: '.',
  timeout: 60_000,
  use: {
    baseURL: process.env.EASYCAD_URL || 'http://localhost:8852',
    ...devices['Desktop Chrome'],
  },
})
