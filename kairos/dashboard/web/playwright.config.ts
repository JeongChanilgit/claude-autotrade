import { defineConfig, devices } from "@playwright/test";

// The Python FastAPI backend (port 8000) must be running separately before
// `npx playwright test`. Playwright only owns the Vite dev server lifecycle.
//
// Expected user workflow:
//   terminal 1: .venv\Scripts\python.exe -m uvicorn kairos.dashboard.api.main:app --port 8000
//   terminal 2: cd kairos\dashboard\web && npx playwright test
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run dev",
    port: 5173,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
