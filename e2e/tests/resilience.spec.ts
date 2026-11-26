import { expect, test } from "@playwright/test";

import { chooseFault, finishedTurns, freshConversation, send, waitForTurns } from "../helpers";

test("a rate-limited model falls back to the backup and the turn still succeeds", async ({ page }) => {
  await freshConversation(page);
  await chooseFault(page, "Model rate-limited → backup model");
  await send(page, "hello");
  await waitForTurns(page, 1);
  await expect(page.getByText("Hi! I'm Drift. How can I help with your Driftwood account today?")).toBeVisible();
  await expect(finishedTurns(page).first()).toContainText("1 failed attempt");
  await finishedTurns(page).first().click();
  await expect(page.getByText("rate_limited - falling back to the next model")).toBeVisible();
});

test("when every model is down the reply degrades honestly and offers a retry", async ({ page }) => {
  await freshConversation(page);
  await chooseFault(page, "All models down → degraded reply");
  await send(page, "hello");
  await waitForTurns(page, 1);
  await expect(page.getByText("Degraded reply", { exact: true })).toBeVisible();
  // (Next.js also renders a role=alert route announcer, so filter by content)
  await expect(page.getByRole("alert").filter({ hasText: "temporarily unavailable" })).toBeVisible();

  await chooseFault(page, "No faults");
  await page.getByRole("button", { name: "Retry" }).click();
  await waitForTurns(page, 2);
  await expect(page.getByText("Hi! I'm Drift.", { exact: false })).toBeVisible();
});

test("a failing tool is retried, then the agent says so instead of guessing", async ({ page }) => {
  await freshConversation(page);
  await chooseFault(page, "Knowledge base down");
  await send(page, "What is your refund policy?");
  await waitForTurns(page, 1);
  await expect(page.getByText(/search policies isn't working right now/)).toBeVisible();
  await expect(page.getByText("Grounded in policy")).toHaveCount(0);
});

test("a reload restores the conversation from the server", async ({ page }) => {
  await freshConversation(page);
  await send(page, "hello");
  await waitForTurns(page, 1);
  await send(page, "What is your refund policy?");
  await waitForTurns(page, 2);
  await page.reload();
  await waitForTurns(page, 2);
  await expect(page.getByText(/^According to our policy/)).toBeVisible();
});
