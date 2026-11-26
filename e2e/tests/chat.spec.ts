import { expect, test } from "@playwright/test";

import { finishedTurns, freshConversation, resetDemoData, send, waitForTurns } from "../helpers";
import { API_URL } from "../playwright.config";

test.beforeEach(async ({ request }) => {
  await resetDemoData(request);
});

test("refund: identity verified, policy applied, refund issued - and the trace shows every step", async ({
  page,
}) => {
  await freshConversation(page);
  await page
    .locator("li", { hasText: "Alice Martin" })
    .getByRole("button", { name: "Use this customer" })
    .click();
  await expect(page.locator("#composer")).toHaveValue(/alice@northpeak\.example.*4242/);
  await page.getByRole("button", { name: "Send" }).click();

  await waitForTurns(page, 1);
  await expect(page.getByText(/your refund of \$45\.00 is approved/)).toBeVisible();
  await expect(page.getByText("✓ Alice Martin · pro")).toBeVisible();

  // the collapsed step list expands to the full tool chain
  await finishedTurns(page).first().click();
  for (const step of ["Verifying identity", "Checking account tier & contract", "Processing refund"]) {
    await expect(page.getByText(step, { exact: false }).first()).toBeVisible();
  }
  await expect(page.getByText(/Approved \$45\.00 \(re_/)).toBeVisible();

  // observability drawer: waterfall + model calls for this turn
  await page.getByRole("button", { name: "trace" }).first().click();
  const drawer = page.getByRole("dialog", { name: "Observability" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("img", { name: /Tool Processing refund.* ok/ })).toBeVisible();
  await expect(drawer.getByText("scripted-primary").first()).toBeVisible();
  await expect(drawer.getByText(/Event stream \(\d+ events\)/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
});

test("asking for more refunds is refused by the policy engine", async ({ page }) => {
  await freshConversation(page);
  const ask =
    "I was charged $45, can I get a refund? My email is alice@northpeak.example and my card ends in 4242.";
  await send(page, ask);
  await waitForTurns(page, 1);
  await send(page, "Please refund me again.");
  await waitForTurns(page, 2);
  // The $45 invoice is already refunded, so the only candidate left is a 42-day-old charge -
  // outside the Pro 30-day window. The policy engine refuses; nothing more is approved.
  await expect(page.getByText(/I'm sorry, I can't refund that charge/)).toBeVisible();
  await expect(page.getByText(/is approved/)).toHaveCount(1);
});

test("the page ships security headers", async ({ request }) => {
  const res = await request.get("/");
  const csp = res.headers()["content-security-policy"];
  expect(csp).toContain(`connect-src 'self' ${API_URL}`);
  expect(csp).toContain("frame-ancestors 'none'");
  expect(res.headers()["permissions-policy"]).toContain("microphone=(self)");
  expect(res.headers()["x-content-type-options"]).toBe("nosniff");
});
