import { describe, expect, it, vi, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { isReactive } from "vue";
import { useChatStore } from "@/stores/chat";

// mock api 层（避免真实网络）
vi.mock("@/api", () => ({
  sessionsApi: {
    history: vi.fn(async () => []),
    list: vi.fn(async () => []),
    deleteMessage: vi.fn(async () => undefined),
  },
  streamChat: vi.fn(),
}));

import { sessionsApi, streamChat } from "@/api";
import { useSessionsStore } from "@/stores/sessions";

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

function emitStream(events: unknown[]) {
  (streamChat as unknown as ReturnType<typeof vi.fn>).mockImplementation(
    async (_payload: unknown, onEvent: (ev: unknown) => void) => {
      for (const ev of events) await onEvent(ev);
    }
  );
}

describe("chat store", () => {
  it("appends user + assistant messages on send", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    emitStream([{ type: "token", content: "你" }, { type: "token", content: "好" }]);
    await chat.send("hi");
    expect(chat.messages.length).toBe(2);
    expect(chat.messages[0].role).toBe("user");
    expect(chat.messages[1].role).toBe("assistant");
    expect(chat.messages[1].content).toBe("你好");
    expect(chat.messages[1].streaming).toBe(false);
  });

  it("handles interrupt and keeps orbit in same message on resume", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    emitStream([
      { type: "start", content: "" },
      { type: "tool", content: "web_search" },
      { type: "interrupt", content: "确认？", data: { session_id: "s1" } },
    ]);
    await chat.send("search news");
    const agentMsg = chat.messages[1];
    expect(agentMsg.hitl).not.toBeNull();
    expect(agentMsg.orbit?.map((n) => n.type)).toEqual(["start", "tool"]);
    expect(chat.hitlMsgId).toBe(agentMsg.id);

    // 确认 → 复用同一消息（不新增气泡）
    emitStream([
      { type: "tool", content: "web_search" },
      { type: "message", content: "答案", data: { session_id: "s1" } },
    ]);
    await chat.resume("confirmed", "s1");
    expect(chat.messages.length).toBe(2); // 仍是 2 条
    expect(chat.messages[1].content).toBe("答案");
    expect(chat.messages[1].hitl).toBeNull();
    // 轨道在同一消息内继续，且 start 不重复；同工具标签去重（不出现重复节点）
    const types = chat.messages[1].orbit?.map((n) => n.type) || [];
    expect(types.filter((t) => t === "start").length).toBe(1);
    expect(types).toEqual(["start", "tool"]);
  });

  it("assistant message object is reactive (streaming updates must re-render DOM)", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    emitStream([{ type: "token", content: "流式" }]);
    await chat.send("hi");
    // 通过闭包修改的 agentMsg 必须是 reactive proxy，否则组件 watch 不会触发
    expect(isReactive(chat.messages[1])).toBe(true);
  });

  it("stop() aborts controller", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    (streamChat as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      (_payload: unknown, _onEvent: unknown, signal: AbortSignal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => {
            reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
          });
        })
    );
    const p = chat.send("hi");
    expect(chat.sending).toBe(true);
    chat.stop();
    expect(chat.abortController?.signal.aborted).toBe(true);
    await p;
    expect(chat.messages[1].content).toContain("已停止生成");
    expect(chat.sending).toBe(false);
  });

  it("retry() truncates from user question and resends it", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    emitStream([{ type: "token", content: "第一次回答" }]);
    await chat.send("问题A");
    const firstAssistant = chat.messages[1];

    // 后续再发一条
    emitStream([{ type: "token", content: "第二次回答" }]);
    await chat.send("问题B");
    expect(chat.messages.length).toBe(4);

    // 重试第一条助手消息：截断"问题A"起的消息并重新发送
    emitStream([{ type: "token", content: "重试后的回答" }]);
    await chat.retry(firstAssistant);
    expect(chat.messages.length).toBe(2);
    expect(chat.messages[0].role).toBe("user");
    expect(chat.messages[0].content).toBe("问题A");
    expect(chat.messages[1].content).toBe("重试后的回答");
    expect(chat.sending).toBe(false);
  });

  it("retry() is a no-op while sending", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    (streamChat as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      (_payload: unknown, _onEvent: unknown, signal: AbortSignal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => {
            reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
          });
        })
    );
    const p = chat.send("问题A");
    expect(chat.sending).toBe(true);
    const before = chat.messages.length;
    await chat.retry(chat.messages[1]);
    expect(chat.messages.length).toBe(before); // 未变化
    chat.abortController?.abort();
    await p;
  });

  it("retry() deletes the replaced exchange on the backend", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    emitStream([{ type: "token", content: "第一次回答" }]);
    await chat.send("问题A");
    chat.messages[0].backendId = "u1";
    chat.messages[1].backendId = "a1";

    emitStream([{ type: "token", content: "重试后的回答" }]);
    await chat.retry(chat.messages[1]);

    expect(sessionsApi.deleteMessage).toHaveBeenCalledWith("s1", "u1");
    expect(sessionsApi.deleteMessage).toHaveBeenCalledWith("s1", "a1");
    expect(chat.messages.map((m) => m.content)).toEqual([
      "问题A",
      "重试后的回答",
    ]);
  });

  it("editAndResend() replaces the user message without duplicating it", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    emitStream([{ type: "token", content: "第一次回答" }]);
    await chat.send("问题A");
    chat.messages[0].backendId = "u1";
    chat.messages[1].backendId = "a1";

    emitStream([{ type: "token", content: "编辑后的回答" }]);
    await chat.editAndResend(chat.messages[0], "问题A（改）");

    expect(sessionsApi.deleteMessage).toHaveBeenCalledWith("s1", "u1");
    expect(sessionsApi.deleteMessage).toHaveBeenCalledWith("s1", "a1");
    expect(chat.messages.map((m) => m.content)).toEqual([
      "问题A（改）",
      "编辑后的回答",
    ]);
    expect(chat.messages.filter((m) => m.role === "user")).toHaveLength(1);
  });

  it("keeps only the latest history when session switches overlap", async () => {
    const chat = useChatStore();
    let resolveFirst!: (v: unknown) => void;
    let resolveSecond!: (v: unknown) => void;
    (sessionsApi.history as unknown as ReturnType<typeof vi.fn>)
      .mockImplementationOnce(
        () => new Promise((resolve) => (resolveFirst = resolve))
      )
      .mockImplementationOnce(
        () => new Promise((resolve) => (resolveSecond = resolve))
      );

    const first = chat.loadHistory("s1");
    const second = chat.loadHistory("s2");
    resolveSecond([{ id: "b1", role: "assistant", content: "B" }]);
    await second;
    resolveFirst([{ id: "a1", role: "assistant", content: "A" }]);
    await first;

    expect(chat.messages.map((m) => m.content)).toEqual(["B"]);
  });

  it("records history failures instead of rejecting callers", async () => {
    const chat = useChatStore();
    (sessionsApi.history as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("加载失败")
    );

    await expect(chat.loadHistory("s1")).resolves.toBeUndefined();
    expect(chat.historyError).toBe("加载失败");
  });

  it("loadHistory aborts the in-flight stream and frees sending", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    (streamChat as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      (_payload: unknown, _onEvent: unknown, signal: AbortSignal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => {
            reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
          });
        })
    );
    const pending = chat.send("hi");
    expect(chat.sending).toBe(true);
    (sessionsApi.history as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce([]);

    await chat.loadHistory("s2");

    expect(chat.sending).toBe(false);
    expect(chat.messages).toEqual([]);
    // 新会话可立即发送，旧流的 finally 不会反向清掉新流状态
    emitStream([{ type: "token", content: "新回答" }]);
    await chat.send("next");
    expect(chat.messages[1].content).toBe("新回答");
    await pending;
  });

  it("clear() aborts the in-flight stream", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    (streamChat as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      (_payload: unknown, _onEvent: unknown, signal: AbortSignal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => {
            reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
          });
        })
    );
    const pending = chat.send("hi");
    expect(chat.sending).toBe(true);

    chat.clear();

    expect(chat.sending).toBe(false);
    expect(chat.messages).toEqual([]);
    await pending;
  });
});
