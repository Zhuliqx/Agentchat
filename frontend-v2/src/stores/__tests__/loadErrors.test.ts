import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

vi.mock("@/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api")>();
  return {
    ...actual,
    sessionsApi: { ...actual.sessionsApi, list: vi.fn() },
    docsApi: { ...actual.docsApi, list: vi.fn() },
    memoryApi: { ...actual.memoryApi, list: vi.fn() },
  };
});

import { docsApi, memoryApi, sessionsApi } from "@/api";
import { useDocsStore } from "@/stores/docs";
import { useMemoryStore } from "@/stores/memory";
import { useSessionsStore } from "@/stores/sessions";

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

describe("列表加载失败的状态", () => {
  it("会话加载失败时记录原因并保留旧列表", async () => {
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "旧会话", created_at: "", updated_at: "" }];
    vi.mocked(sessionsApi.list).mockRejectedValueOnce(new Error("后端不可达"));

    await sessions.load();

    expect(sessions.error).toBe("后端不可达");
    expect(sessions.loading).toBe(false);
    expect(sessions.list).toHaveLength(1);
  });

  it("重试成功后清空错误", async () => {
    const sessions = useSessionsStore();
    vi.mocked(sessionsApi.list).mockRejectedValueOnce(new Error("后端不可达"));
    await sessions.load();

    vi.mocked(sessionsApi.list).mockResolvedValueOnce([
      { id: "s1", title: "新会话", created_at: "", updated_at: "" },
    ]);
    await sessions.load();

    expect(sessions.error).toBeNull();
    expect(sessions.list).toHaveLength(1);
  });

  it("文档与记忆加载失败同样记录原因", async () => {
    const docs = useDocsStore();
    const memory = useMemoryStore();
    vi.mocked(docsApi.list).mockRejectedValueOnce(new Error("文档加载失败"));
    vi.mocked(memoryApi.list).mockRejectedValueOnce(new Error("记忆加载失败"));

    await docs.load();
    await memory.load();

    expect(docs.error).toBe("文档加载失败");
    expect(memory.error).toBe("记忆加载失败");
  });
});
