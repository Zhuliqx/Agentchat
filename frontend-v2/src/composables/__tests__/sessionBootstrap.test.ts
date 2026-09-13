import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

vi.mock("@/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api")>();
  return {
    ...actual,
    sessionsApi: {
      ...actual.sessionsApi,
      history: vi.fn(async () => []),
      create: vi.fn(async () => ({
        id: "s-new",
        title: "新会话",
        created_at: "",
        updated_at: "",
      })),
    },
  };
});

import { sessionsApi } from "@/api";
import { bootstrapActiveSession } from "@/composables/useSessionBootstrap";
import { useChatStore } from "@/stores/chat";
import { useSessionsStore } from "@/stores/sessions";

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

describe("bootstrapActiveSession", () => {
  it("已有当前会话时不动作，避免打断进行中的回答", async () => {
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "已有会话", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";

    await bootstrapActiveSession();

    expect(sessionsApi.history).not.toHaveBeenCalled();
    expect(sessionsApi.create).not.toHaveBeenCalled();
  });

  it("没有当前会话时选中最近一条并加载历史", async () => {
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "最近会话", created_at: "", updated_at: "" }];

    await bootstrapActiveSession();

    expect(sessions.currentId).toBe("s1");
    expect(sessionsApi.history).toHaveBeenCalledWith("s1");
  });

  it("一条会话都没有时新建空会话并清空消息", async () => {
    const sessions = useSessionsStore();
    const chat = useChatStore();
    chat.messages = [{ id: "m1", role: "user", content: "上一条内容" }];

    await bootstrapActiveSession();

    expect(sessionsApi.create).toHaveBeenCalled();
    expect(sessions.currentId).toBe("s-new");
    expect(chat.messages).toHaveLength(0);
  });
});
