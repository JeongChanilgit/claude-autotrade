import { expect, test } from "@playwright/test";

test.describe("home page", () => {
  test("renders heading, New Run link, and runs section", async ({ page }) => {
    await page.goto("/");

    await expect(
      page.getByRole("heading", { name: /Kairos Backtest Dashboard/i }),
    ).toBeVisible();

    // The "New Run" CTA links to /run/new — asserting the link (not just text)
    // catches regressions where a stray <span>New Run</span> would pass.
    const newRun = page.getByRole("link", { name: "New Run" });
    await expect(newRun).toBeVisible();
    await expect(newRun).toHaveAttribute("href", "/run/new");

    // Either the runs table header is visible OR the empty-state text renders.
    const tableOrEmpty = page.locator(
      'table, :text("No runs yet"), :text("Loading")',
    );
    await expect(tableOrEmpty.first()).toBeVisible();
  });
});
