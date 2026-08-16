import { describe, expect, it } from "vitest";
import { parseFrame } from "@/utils/sse";

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
});
