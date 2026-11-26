import { expect, test } from "@playwright/test";

import { freshConversation, send, waitForTurns } from "../helpers";

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

test("phone layout: persona drawer, chat and observability are all reachable", async ({ page }) => {
  await freshConversation(page);

  await page.getByRole("button", { name: "Demo customers" }).click();
  await expect(page.getByText("Alice Martin")).toBeVisible();
  await page
    .locator("li", { hasText: "Bob Chen" })
    .getByRole("button", { name: "Use this customer" })
    .click();
  await expect(page.getByText("Alice Martin")).toBeHidden(); // drawer closed itself
  await expect(page.locator("#composer")).toHaveValue(/bob@chenlabs\.example/);

  await page.locator("#composer").fill("");
  await send(page, "hello");
  await waitForTurns(page, 1);

  await page.getByRole("button", { name: /Observability/ }).click();
  const drawer = page.getByRole("dialog", { name: "Observability" });
  await expect(drawer).toBeVisible();
  const box = await drawer.boundingBox();
  expect(box?.width).toBeGreaterThan(380); // full width on a phone
  await drawer.getByRole("button", { name: "Close observability" }).click();
  await expect(drawer).toBeHidden();

  // nothing overflows horizontally
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
