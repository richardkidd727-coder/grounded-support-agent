/**
 * Cut a streaming reply into speakable sentences as it arrives, so the first sentence can be
 * synthesised while the model is still writing the rest.
 */

// A sentence ends at . ! ? (optionally followed by quotes/brackets) *followed by whitespace* -
// so "$45.00", "4.5" and "INV-2026-10231." mid-stream are not cut early - or at a line break.
const BOUNDARY = /[.!?]["')\]]*\s+|\n+/g;
const MIN_CHARS = 12; // merge fragments like "Sure." into the next sentence

export class SentenceStream {
  private consumed = 0;

  /** New complete sentences in `text` since the last call. `final` flushes the remainder. */
  next(text: string, final = false): string[] {
    const out: string[] = [];
    let pending = "";
    let start = this.consumed;
    BOUNDARY.lastIndex = start;
    for (let m = BOUNDARY.exec(text); m; m = BOUNDARY.exec(text)) {
      const end = m.index + m[0].length;
      pending += text.slice(start, end);
      start = end;
      if (pending.trim().length >= MIN_CHARS) {
        out.push(pending);
        pending = "";
        this.consumed = end;
      }
    }
    if (final) {
      const rest = text.slice(this.consumed);
      if (rest.trim()) out.push(rest);
      this.consumed = text.length;
    }
    return out.map(cleanForSpeech).filter(Boolean);
  }
}

/** Remove markdown and symbols that a speech engine would read out literally. */
export function cleanForSpeech(text: string): string {
  return text
    .replace(/https?:\/\/\S+/g, "the link in the chat")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/\*\*|__|[*_#>]/g, "")
    .replace(/^\s*[-•]\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}
