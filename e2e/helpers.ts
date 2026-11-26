import { expect, type APIRequestContext, type Page } from "@playwright/test";

import { API_URL } from "./playwright.config";

/** Restore the demo customers (e.g. Alice's refundable invoice). */
export async function resetDemoData(request: APIRequestContext): Promise<void> {
  const res = await request.post(`${API_URL}/api/demo/reset`);
  expect(res.ok()).toBeTruthy();
}

/** Open the app on a brand-new conversation. */
export async function freshConversation(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByText("Online")).toBeVisible();
  await page.getByRole("button", { name: "New conversation" }).click();
  await expect(page.getByText("Hi, I'm Drift")).toBeVisible();
}

export async function send(page: Page, text: string): Promise<void> {
  await page.locator("#composer").fill(text);
  await page.getByRole("button", { name: "Send" }).click();
}

/** Collapsed step summaries ("› 7 steps · 3 tool calls · 1.2s") - one per finished turn. */
export function finishedTurns(page: Page) {
  return page.getByText(/^› \d+ steps?/);
}

export async function waitForTurns(page: Page, count: number): Promise<void> {
  await expect(finishedTurns(page)).toHaveCount(count, { timeout: 60_000 });
}

export async function chooseFault(page: Page, label: string): Promise<void> {
  await page.getByLabel("Fault injection").selectOption({ label });
}
