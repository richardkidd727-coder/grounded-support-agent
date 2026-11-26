import { expect, test } from "@playwright/test";

import { freshConversation, waitForTurns } from "../helpers";
import { VOICE_WAV } from "../playwright.config";

// The microphone is a WAV file: a question, then the customer talking over Drift's answer.
test.use({
  permissions: ["microphone"],
  launchOptions: {
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${VOICE_WAV}`,
      "--autoplay-policy=no-user-gesture-required",
    ],
  },
});

test("voice: transcribed turn, spoken reply, and barge-in recorded on the trace", async ({ page }) => {
  await freshConversation(page);
  await page.getByRole("button", { name: /Voice/ }).click();
  const panel = page.getByRole("region", { name: "Voice conversation" });
  await expect(panel).toBeVisible();

  // first utterance -> voice turn with a grounded, spoken answer
  await expect(page.getByText("What is your refund policy?").first()).toBeVisible({ timeout: 30_000 });
  await expect(panel.getByText("Drift is speaking - talk to interrupt")).toBeVisible({ timeout: 30_000 });

  // the customer talks over it -> playback stops, a second voice turn, the first marked interrupted
  await waitForTurns(page, 2);
  await expect(page.getByText("Actually, how do I export my data?").first()).toBeVisible();
  await expect(page.getByText("Interrupted by customer")).toBeVisible();

  await panel.getByRole("button", { name: "End voice" }).click();
  await expect(panel).toBeHidden();

  // the trace shows speech-to-text before the turn started
  await page.getByRole("button", { name: "trace" }).last().click();
  const drawer = page.getByRole("dialog", { name: "Observability" });
  await expect(drawer.getByRole("img", { name: /Speech to text/ })).toBeVisible();
  await expect(drawer.getByText("🎙 voice")).toBeVisible();
});
