import { describe, expect, it } from "vitest";
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
      'data: {"type":"interrupt","content":"确认？","data":{"session_id":"abc"}}',
    );
    expect(ev?.type).toBe("interrupt");
    expect(ev?.data?.session_id).toBe("abc");
  });

  it("splits CRLF-delimited frames", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'data: {"type":"token","content":"第"}\r\n\r\n' +
              'data: {"type":"token","content":"二"}\r\n\r\n',
          ),
        );
        controller.close();
      },
    });
    const events: unknown[] = [];

    await readSSEStream(new Response(stream), (ev) => {
      events.push(ev);
    });

    expect(events).toHaveLength(2);
  });
});
