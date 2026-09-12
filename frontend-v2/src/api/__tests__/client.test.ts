import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiRaw } from "@/api/client";

function mockResponse(status: number, body: unknown = {}): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    statusText: "",
    headers: new Headers(),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe("apiRaw 401 refresh", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("replays the request with the refreshed access token", async () => {
    localStorage.setItem("agentchat_token", "old-token");
    localStorage.setItem("agentchat_refresh_token", "old-refresh");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(mockResponse(401))
      .mockResolvedValueOnce(
        mockResponse(200, {
          token: "new-token",
          refresh_token: "new-refresh",
        })
      )
      .mockResolvedValueOnce(mockResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const res = await apiRaw("/sessions");

    expect(res.status).toBe(200);
    const replayInit = fetchMock.mock.calls[2][1] as RequestInit;
    const replayHeaders = replayInit.headers as Record<string, string>;
    expect(replayHeaders.Authorization).toBe("Bearer new-token");
  });

  it("keeps caller-provided headers", async () => {
    localStorage.setItem("agentchat_token", "token");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(mockResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await apiRaw("/sessions", {
      headers: { "X-Trace-Id": "trace-1" },
    });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer token");
    // fetch Headers 会把名称规范化为小写；取值语义与大小写无关
    expect(headers["x-trace-id"]).toBe("trace-1");
    expect(headers["Content-Type"]).toBe("application/json");
  });
});
