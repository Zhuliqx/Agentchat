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
    truncate: vi.fn(async () => ({ deleted: 2 })),
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
    },
  );
}

describe("chat store", () => {
  it("appends user + assistant messages on send", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    emitStream([
      { type: "token", content: "你" },
      { type: "token", content: "好" },
    ]);
    await chat.send("hi");
    expect(chat.messages.length).toBe(2);
    expect(chat.messages[0].role).toBe("user");
    expect(chat.messages[1].role).toBe("assistant");
    expect(chat.messages[1].content).toBe("你好");
    expect(chat.messages[1].streaming).toBe(false);
  });

  it("发送时给用户消息、回答完成时给助手消息打时间戳", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    emitStream([{ type: "token", content: "好" }]);

    await chat.send("hi");

    const [userMsg, agentMsg] = chat.messages;
    expect(userMsg.createdAt).toBeTruthy();
    expect(agentMsg.createdAt).toBeTruthy();
    // 助手时间在流结束时写入，不应早于用户消息
    expect(new Date(agentMsg.createdAt!).getTime()).toBeGreaterThanOrEqual(
      new Date(userMsg.createdAt!).getTime(),
    );
  });

  it("滚动状态：贴底阈值、按钮出现条件与未读计数", () => {
    const chat = useChatStore();
    chat.messages = [
      { id: "m1", role: "user", content: "问题" },
      { id: "m2", role: "assistant", content: "回答" },
    ];

    // 贴底：不显示按钮、无未读
    chat.markScroll(0);
    expect(chat.atBottom).toBe(true);
    expect(chat.showJumpButton).toBe(false);
    expect(chat.unseen).toBe(0);

    // 轻微上滑（未超过 240）：不弹按钮
    chat.markScroll(100);
    expect(chat.atBottom).toBe(false);
    expect(chat.showJumpButton).toBe(false);

    // 期间新增 2 条消息：按钮出现并计数
    chat.messages.push(
      { id: "m3", role: "user", content: "再问" },
      { id: "m4", role: "assistant", content: "再答" },
    );
    expect(chat.unseen).toBe(2);
    expect(chat.showJumpButton).toBe(true);

    // 滚远（超过 240）：即使没有新消息也显示按钮
    chat.markScroll(400);
    expect(chat.showJumpButton).toBe(true);

    // 回到最新：未读清零、按钮隐藏
    chat.jumpToBottom();
    expect(chat.unseen).toBe(0);
    expect(chat.atBottom).toBe(true);
    expect(chat.showJumpButton).toBe(false);
  });

  it("编辑重发走服务端原子截断，再本地截断并重发", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    chat.messages = [
      { id: "m1", backendId: "b1", role: "user", content: "旧问题" },
      { id: "m2", backendId: "b2", role: "assistant", content: "旧回答" },
      { id: "m3", backendId: "b3", role: "user", content: "后续问题" },
    ];
    const truncate = sessionsApi.truncate as unknown as ReturnType<typeof vi.fn>;
    truncate.mockClear().mockResolvedValue({ deleted: 3 });
    emitStream([{ type: "token", content: "新回答" }]);

    await chat.editAndResend(chat.messages[0], "新问题");

    expect(truncate).toHaveBeenCalledWith("s1", "b1");
    // 截断点之后（含截断点）的旧消息不再保留，只剩"新问题 + 新回答"
    expect(chat.messages.map((m) => m.content)).toEqual(["新问题", "新回答"]);
    expect(chat.historyError).toBeNull();
  });

  it("截断失败时中止重发，并给出错误提示", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    chat.messages = [
      { id: "m1", backendId: "b1", role: "user", content: "旧问题" },
      { id: "m2", backendId: "b2", role: "assistant", content: "旧回答" },
    ];
    const truncate = sessionsApi.truncate as unknown as ReturnType<typeof vi.fn>;
    truncate.mockClear().mockRejectedValue(new Error("网络错误"));
    const stream = streamChat as unknown as ReturnType<typeof vi.fn>;
    stream.mockClear();

    await chat.editAndResend(chat.messages[0], "新问题");

    expect(chat.historyError).toContain("截断历史失败");
    expect(stream).not.toHaveBeenCalled(); // 没有重发
    expect(chat.messages.map((m) => m.content)).toEqual(["旧问题", "旧回答"]);
  });

  it("同一工具先发 agent 再发带来源的 tool 事件：来源仍会写入消息", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    emitStream([
      { type: "start", content: "" },
      { type: "agent", content: "调用 rag_agent" },
      {
        type: "tool",
        content: "工具: rag_agent",
        data: {
          sources: [
            { path: "/kb/a.md", hits: 3 },
            { path: "/kb/b.md", hits: 1 },
          ],
        },
      },
      { type: "token", content: "答案" },
    ]);

    await chat.send("知识库里有什么内容？");

    const agentMsg = chat.messages[1];
    expect(agentMsg.sources).toEqual([
      { path: "/kb/a.md", hits: 3 },
      { path: "/kb/b.md", hits: 1 },
    ]);
    // 轨道仍然只有一个 rag 节点（去重不能被破坏）
    expect(agentMsg.orbit?.filter((n) => n.label.includes("rag_agent"))).toHaveLength(1);
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
        }),
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
        }),
    );
    const p = chat.send("问题A");
    expect(chat.sending).toBe(true);
    const before = chat.messages.length;
    await chat.retry(chat.messages[1]);
    expect(chat.messages.length).toBe(before); // 未变化
    chat.abortController?.abort();
    await p;
  });

  it("retry() 走服务端原子截断后重新发送", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    emitStream([{ type: "token", content: "第一次回答" }]);
    await chat.send("问题A");
    chat.messages[0].backendId = "u1";
    chat.messages[1].backendId = "a1";
    const truncate = sessionsApi.truncate as unknown as ReturnType<typeof vi.fn>;
    truncate.mockReset();
    truncate.mockResolvedValue({ deleted: 2 });

    emitStream([{ type: "token", content: "重试后的回答" }]);
    await chat.retry(chat.messages[1]);

    // 一次原子截断（含该用户消息及其后的助手消息），而不是逐条删除
    expect(truncate).toHaveBeenCalledTimes(1);
    expect(truncate).toHaveBeenCalledWith("s1", "u1");
    expect(chat.messages.map((m) => m.content)).toEqual(["问题A", "重试后的回答"]);
  });

  it("editAndResend() 原子截断后替换用户消息，不重复", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    emitStream([{ type: "token", content: "第一次回答" }]);
    await chat.send("问题A");
    chat.messages[0].backendId = "u1";
    chat.messages[1].backendId = "a1";
    const truncate = sessionsApi.truncate as unknown as ReturnType<typeof vi.fn>;
    truncate.mockReset();
    truncate.mockResolvedValue({ deleted: 2 });

    emitStream([{ type: "token", content: "编辑后的回答" }]);
    await chat.editAndResend(chat.messages[0], "问题A（改）");

    expect(truncate).toHaveBeenCalledWith("s1", "u1");
    expect(chat.messages.map((m) => m.content)).toEqual(["问题A（改）", "编辑后的回答"]);
    expect(chat.messages.filter((m) => m.role === "user")).toHaveLength(1);
  });

  it("keeps only the latest history when session switches overlap", async () => {
    const chat = useChatStore();
    let resolveFirst!: (v: unknown) => void;
    let resolveSecond!: (v: unknown) => void;
    (sessionsApi.history as unknown as ReturnType<typeof vi.fn>)
      .mockImplementationOnce(() => new Promise((resolve) => (resolveFirst = resolve)))
      .mockImplementationOnce(() => new Promise((resolve) => (resolveSecond = resolve)));

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
      new Error("加载失败"),
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
        }),
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
        }),
    );
    const pending = chat.send("hi");
    expect(chat.sending).toBe(true);

    chat.clear();

    expect(chat.sending).toBe(false);
    expect(chat.messages).toEqual([]);
    await pending;
  });
});
