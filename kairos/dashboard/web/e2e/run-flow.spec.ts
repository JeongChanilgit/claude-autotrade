import { expect, test } from "@playwright/test";

// Window aligned with the downloaded-but-pre-OOS range. Adjust if the local
// data footprint differs — the goal is dates the backend can actually serve.
const START_DATE = "2024-06-01";
const END_DATE = "2024-07-01";

test.describe("run creation flow", () => {
  test("submits a run and lands on the results page with progress", async ({
    page,
  }) => {
    await page.goto("/run/new");

    await expect(
      page.getByRole("heading", { name: /New Run/i }),
    ).toBeVisible();

    // Defaults already match strategy=orb, symbol=BTCUSDT, engine=vectorbt.
    // Select interval=15m explicitly to exercise the radio, and fill dates.
    await page.getByRole("radio", { name: "15m" }).check();
    await page.getByRole("radio", { name: "vectorbt" }).check();

    const startInput = page.locator('input[type="date"]').nth(0);
    const endInput = page.locator('input[type="date"]').nth(1);
    await startInput.fill(START_DATE);
    await endInput.fill(END_DATE);

    await page.getByRole("button", { name: /Create run/i }).click();

    // URL navigates to /run/<id>.
    await page.waitForURL(/\/run\/run_[0-9a-f]+/, { timeout: 30_000 });

    // Progress bar or the final metrics block should appear within 30s —
    // either indicates the worker handshake is live.
    const progressOrMetrics = page.locator(
      ':text("Metrics"), [role="progressbar"], :text("CAGR"), :text("Progress")',
    );
    await expect(progressOrMetrics.first()).toBeVisible({ timeout: 30_000 });
  });
});
