import { describe, expect, it } from "vitest";
import type { SSEEvent } from "@/types/api";
import { parseFrame, readSSEStream } from "@/utils/sse";

describe("SSE frame parsing", () => {
  it("parses a valid data frame", () => {
    const ev = parseFrame('data: {"type":"token","content":"你好"}');
    expect(ev).toEqual({ type: "token", content: "你好" });
  });

  it("ignores non-data frames", () => {
    expect(parseFrame("event: token")).toBeNull();
    expect(parseFrame(": comment")).toBeNull();
  });

  it("returns null for invalid JSON", () => {
    expect(parseFrame("data: not-json")).toBeNull();
  });

  it("parses interrupt frame with data", () => {
    const ev = parseFrame(
      'data: {"type":"interrupt","content":"确认？","data":{"session_id":"abc"}}'
    );
    expect(ev?.type).toBe("interrupt");
    expect(ev?.data?.session_id).toBe("abc");
  });

  it("reads fragmented CRLF-delimited frames without corrupting UTF-8 content", async () => {
    const encoder = new TextEncoder();
    const payload = 'data: {"type":"token","content":"你好"}\r\n\r\ndata: {"type":"message","content":"done"}\r\n\r\n';
    const bytes = encoder.encode(payload);
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, 38));
        controller.enqueue(bytes.slice(38));
        controller.close();
      },
    });
    const events: SSEEvent[] = [];

    await readSSEStream(new Response(stream), (event) => {
      events.push(event);
    });

    expect(events).toEqual([
      { type: "token", content: "你好" },
      { type: "message", content: "done" },
    ]);
  });
});
