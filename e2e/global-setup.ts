import { writeVoiceFixture } from "./audio";
import { VOICE_WAV } from "./playwright.config";

export default function globalSetup(): void {
  writeVoiceFixture(VOICE_WAV);
}
