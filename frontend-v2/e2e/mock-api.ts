import type { Page } from "@playwright/test";

/** 固定时间轴：相对"今天"生成，避免用例随真实日期漂移 */
function daysAgo(n: number, hour = 10): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  d.setHours(hour, 0, 0, 0);
  return d.toISOString();
}

export const SESSIONS = [
  { id: "s-today", title: "新会话", created_at: daysAgo(0), updated_at: daysAgo(0) },
  { id: "s-yesterday", title: "昨天的会话", created_at: daysAgo(1), updated_at: daysAgo(1) },
  { id: "s-earlier", title: "更早的会话", created_at: daysAgo(6), updated_at: daysAgo(6) },
];

/** 昨天的会话带一条回答 + 一个来源，用来验证引用编号与 chip 的联动 */
export const MESSAGES = [
  { id: "m1", role: "user", content: "知识库中有什么内容？", created_at: daysAgo(1) },
  {
    id: "m2",
    role: "assistant",
    content: "数据存储与保留规则见数据与隐私政策 [1]。",
    sources: [{ path: "D:\\kb\\policies.md", hits: 2 }],
    created_at: daysAgo(1),
  },
];

/** SSE 帧：工具事件带来源，随后逐段吐字，最后收尾 */
export const STREAM_FRAMES = [
  { type: "start", content: "Supervisor 开始调度..." },
  { type: "agent", content: "调用 rag_agent" },
  {
    type: "tool",
    content: "工具: rag_agent",
    data: { sources: [{ path: "D:\\kb\\policies.md", hits: 2 }] },
  },
  { type: "token", content: "对话记录默认保留 180 天 [1]。" },
  { type: "end", content: "完成" },
];

function sseBody(frames: object[]): string {
  return frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join("");
}

/**
 * 拦截全部 /api/** 请求。
 *
 * 未覆盖的接口一律 404 并记录，避免"某个接口忘了 mock 却静默通过"。
 */
export async function mockApi(page: Page, seen: string[] = []): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^\/api/, "");
    const method = req.method();
    seen.push(`${method} ${path}`);

    if (path === "/health") {
      return route.fulfill({
        json: {
          status: "ok",
          postgres: { ok: true, error: "" },
          milvus: { collection: "agent_documents", num_entities: 0, connected: true },
          mcp_servers: [],
          redis: { enabled: false },
        },
      });
    }
    if (path === "/auth/capabilities") return route.fulfill({ json: { platform_operator: true } });
    if (path === "/models") return route.fulfill({ json: { models: [], current: null } });
    if (path === "/rag/documents") return route.fulfill({ json: [] });
    if (path === "/memory") return route.fulfill({ json: [] });
    if (path === "/sessions" && method === "GET") return route.fulfill({ json: SESSIONS });
    if (path === "/sessions" && method === "POST") {
      return route.fulfill({
        json: { id: "s-new", title: "新会话", created_at: daysAgo(0), updated_at: daysAgo(0) },
      });
    }
    if (path === "/sessions/s-today") return route.fulfill({ json: [] });
    if (path === "/sessions/s-yesterday") return route.fulfill({ json: MESSAGES });
    if (path === "/sessions/s-earlier") return route.fulfill({ json: [] });
    if (path === "/chat/stream") {
      return route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: sseBody(STREAM_FRAMES),
      });
    }
    return route.fulfill({ status: 404, json: { detail: `未 mock 的接口：${method} ${path}` } });
  });
}
