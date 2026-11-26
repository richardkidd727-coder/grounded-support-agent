import { expect, test } from "@playwright/test";

import { finishedTurns, freshConversation, send, waitForTurns } from "../helpers";

test("a policy question is answered from retrieved policy, with its sources", async ({ page }) => {
  await freshConversation(page);
  await send(page, "What is your refund policy?");
  await waitForTurns(page, 1);
  await expect(page.getByText(/^According to our policy/)).toBeVisible();
  await expect(page.getByText(/Grounded in policy · \d+%/)).toBeVisible();
  await expect(page.getByText("Sources:")).toBeVisible();
});

test("a question no policy answers is escalated by the guardrail, not guessed", async ({ page }) => {
  await freshConversation(page);
  await send(page, "Do you offer discounts for nonprofit organisations?");
  await waitForTurns(page, 1);
  await expect(page.getByText(/Low confidence · \d+% → human/)).toBeVisible();
  await expect(page.getByText(/Escalated to a human · TCK-\d+/)).toBeVisible();
  await expect(page.getByText(/I've opened ticket TCK-\d+/)).toBeVisible();
  await finishedTurns(page).first().click();
  await expect(page.getByText("auto: low confidence")).toBeVisible(); // opened by code
});
