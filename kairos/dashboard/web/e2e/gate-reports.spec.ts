import { expect, test } from "@playwright/test";

test.describe("gate reports", () => {
  test("lists reports and opens a detail page", async ({ page }) => {
    await page.goto("/gate-reports");

    await expect(
      page.getByRole("heading", { name: /Gate Reports/i }),
    ).toBeVisible();

    // Either the table renders or the empty-state message does. If there are
    // no reports on disk the test stops here — there is nothing to click.
    const emptyState = page.locator(':text("No gate reports found")');
    const firstRowLink = page
      .locator("table tbody tr")
      .first()
      .locator("a")
      .first();

    const hasReports = await firstRowLink.isVisible().catch(() => false);
    if (!hasReports) {
      await expect(emptyState).toBeVisible();
      return;
    }

    await firstRowLink.click();

    // Detail page shows a Summary block and the Strategy Sharpes chart.
    await expect(
      page.getByRole("heading", { name: /Summary/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /Strategy Sharpes/i }),
    ).toBeVisible();
  });
});
