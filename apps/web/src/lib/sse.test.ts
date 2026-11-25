import { describe, expect, it } from "vitest";

import { readSse, SseParser } from "./sse";

describe("SseParser", () => {
  it("parses id, event and data fields", () => {
    const frames = new SseParser().push('id: 3\nevent: token\ndata: {"text":"hi"}\n\n');
    expect(frames).toEqual([{ id: "3", event: "token", data: '{"text":"hi"}' }]);
  });

  it("reassembles frames split across chunks", () => {
    const parser = new SseParser();
    expect(parser.push("event: tok")).toEqual([]);
    expect(parser.push('en\ndata: {"a"')).toEqual([]);
    expect(parser.push(":1}\n\nevent: x\ndata: 2\n")).toEqual([
      { id: null, event: "token", data: '{"a":1}' },
    ]);
    expect(parser.push("\n")).toEqual([{ id: null, event: "x", data: "2" }]);
  });

  it("ignores keep-alive comments and handles CRLF and multi-line data", () => {
    const frames = new SseParser().push(": keep-alive\r\n\r\ndata: line1\r\ndata: line2\r\n\r\n");
    expect(frames).toEqual([{ id: null, event: "message", data: "line1\nline2" }]);
  });
});

describe("readSse", () => {
  it("reads a byte stream, including a final frame without a trailing blank line", async () => {
    const encoder = new TextEncoder();
    const chunks = ["event: a\ndata: 1\n\nev", "ent: b\ndata: é\n"];
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const c of chunks) controller.enqueue(encoder.encode(c));
        controller.close();
      },
    });
    const frames = [];
    for await (const f of readSse(body)) frames.push(f);
    expect(frames.map((f) => [f.event, f.data])).toEqual([
      ["a", "1"],
      ["b", "é"],
    ]);
  });
});
