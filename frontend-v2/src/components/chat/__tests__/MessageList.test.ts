import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";
import MessageList from "@/components/chat/MessageList.vue";
import { useChatStore } from "@/stores/chat";
import { useSessionsStore } from "@/stores/sessions";
import { setReadMarker } from "@/utils/readMarker";

function setScrollMetrics(
  el: HTMLElement,
  metrics: { scrollHeight: number; clientHeight: number; scrollTop: number },
) {
  Object.defineProperty(el, "scrollHeight", {
    value: metrics.scrollHeight,
    configurable: true,
  });
  Object.defineProperty(el, "clientHeight", {
    value: metrics.clientHeight,
    configurable: true,
  });
  el.scrollTop = metrics.scrollTop;
}

describe("MessageList auto scroll", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
  });

  it("用户上滑后不再自动跟随，点回到最新能滚回底部", async () => {
    const chat = useChatStore();
    chat.messages = [{ id: "m1", role: "assistant", content: "第一段", streaming: true }];
    const wrapper = mount(MessageList, {
      global: { stubs: { MessageItem: true } },
    });
    const list = wrapper.element as HTMLElement;
    setScrollMetrics(list, {
      scrollHeight: 1000,
      clientHeight: 400,
      scrollTop: 0,
    });

    await wrapper.trigger("scroll");

    expect(chat.atBottom).toBe(false);
    expect(chat.showJumpButton).toBe(true);

    chat.messages[0].content = "第一段 + 新内容";
    await nextTick();
    await nextTick();

    expect(list.scrollTop).toBe(0);

    chat.jumpToBottom();
    await nextTick();
    await new Promise((resolve) => requestAnimationFrame(() => resolve(null))); // 滚动已合并到动画帧

    expect(list.scrollTop).toBe(1000);
    expect(chat.atBottom).toBe(true);
    wrapper.unmount();
  });

  it("打开会话时在上次已读位置画分隔线，且读完不消失", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s1", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    chat.messages = [
      { id: "m1", backendId: "b1", role: "user", content: "旧问题" },
      { id: "m2", backendId: "b2", role: "assistant", content: "旧回答" },
      { id: "m3", backendId: "b3", role: "user", content: "新问题" },
      { id: "m4", backendId: "b4", role: "assistant", content: "新回答" },
    ];
    setReadMarker("s1", "b2"); // 上次读到第二条（后端 id，跨刷新稳定）

    const wrapper = mount(MessageList, { global: { stubs: { MessageItem: true } } });
    await nextTick();

    const divider = wrapper.find("[data-new-divider]");
    expect(divider.exists()).toBe(true);
    // 分隔线插在第三条消息之前（即第二条之后）
    const items = wrapper.findAll("message-item-stub");
    expect(items.length).toBe(4);
    expect(items[1].element.nextElementSibling?.hasAttribute("data-new-divider")).toBe(true);
    expect(items[2].element.previousElementSibling?.hasAttribute("data-new-divider")).toBe(true);

    // 贴底阅读后，已读位置推进到最后一条
    expect(localStorage.getItem("chat-read-s1")).toBe("b4");
    // 但本次会话的分隔线不消失
    expect(wrapper.find("[data-new-divider]").exists()).toBe(true);
    wrapper.unmount();
  });

  it("没有未读时（标记就是最后一条）不画分隔线", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s2", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s2";
    chat.messages = [
      { id: "n1", role: "user", content: "问题" },
      { id: "n2", role: "assistant", content: "回答" },
    ];
    setReadMarker("s2", "n2");

    const wrapper = mount(MessageList, { global: { stubs: { MessageItem: true } } });
    await nextTick();

    expect(wrapper.find("[data-new-divider]").exists()).toBe(false);
    wrapper.unmount();
  });

  it("历史异步到达时才拿到消息（真实加载顺序）也能画出分隔线", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.list = [{ id: "s3", title: "t", created_at: "", updated_at: "" }];
    sessions.currentId = "s3";
    chat.messages = []; // 挂载时历史还没到
    setReadMarker("s3", "pb1");

    const wrapper = mount(MessageList, { global: { stubs: { MessageItem: true } } });
    await nextTick();
    expect(wrapper.find("[data-new-divider]").exists()).toBe(false);

    // 历史加载完成
    chat.messages = [
      { id: "p1", backendId: "pb1", role: "user", content: "已读" },
      { id: "p2", backendId: "pb2", role: "assistant", content: "新回答" },
    ];
    await nextTick();

    expect(wrapper.find("[data-new-divider]").exists()).toBe(true);
    wrapper.unmount();
  });

  it("历史加载中显示骨架，不闪欢迎页", async () => {
    const chat = useChatStore();
    chat.messages = [];
    chat.historyLoading = true;

    const wrapper = mount(MessageList, { global: { stubs: { MessageItem: true } } });

    expect(wrapper.find("[data-testid='history-skeleton']").exists()).toBe(true);
    expect(wrapper.text()).not.toContain("Multi-Agent 助手");

    chat.historyLoading = false;
    await nextTick();

    expect(wrapper.find("[data-testid='history-skeleton']").exists()).toBe(false);
    expect(wrapper.text()).toContain("Multi-Agent 助手");
    wrapper.unmount();
  });

  it("流式输出的滚动合并到动画帧（每帧最多滚一次）", async () => {
    const chat = useChatStore();
    chat.messages = [{ id: "m1", role: "assistant", content: "第一段", streaming: true }];
    const wrapper = mount(MessageList, { global: { stubs: { MessageItem: true } } });
    const list = wrapper.element as HTMLElement;
    setScrollMetrics(list, { scrollHeight: 1000, clientHeight: 400, scrollTop: 600 });
    await wrapper.trigger("scroll"); // 贴底 → atBottom

    let scrollWrites = 0;
    Object.defineProperty(list, "scrollTop", {
      get: () => 600,
      set: () => {
        scrollWrites += 1;
      },
      configurable: true,
    });

    // 连续两个 token（两次 watcher 触发）在同一个动画帧内
    chat.messages[0].content += "a";
    await nextTick();
    chat.messages[0].content += "b";
    await nextTick();
    expect(scrollWrites).toBe(0); // 还没到动画帧，不做同步滚动

    await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
    expect(scrollWrites).toBe(1); // 两个 token 合并成一次

    wrapper.unmount();
  });
});

describe("欢迎页", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
  });

  it("展示能力卡片，点示例问题直接发送", async () => {
    const chat = useChatStore();
    const send = vi.spyOn(chat, "send").mockResolvedValue(undefined);

    const wrapper = mount(MessageList, { global: { stubs: { MessageItem: true } } });

    const caps = wrapper.find("[data-testid='welcome-capabilities']");
    expect(caps.exists()).toBe(true);
    expect(caps.text()).toContain("知识库检索");
    expect(caps.text()).toContain("人工确认");

    const examples = wrapper.findAll("[data-testid='welcome-examples'] button");
    expect(examples.length).toBe(3);
    await examples[0].trigger("click");
    expect(send).toHaveBeenCalledWith("知识库中有什么内容？");

    wrapper.unmount();
  });

  it("最近会话排除当前会话、最多 3 条，点击后切换并加载历史", async () => {
    const chat = useChatStore();
    const sessions = useSessionsStore();
    sessions.currentId = "s1";
    sessions.list = [
      { id: "s1", title: "当前空会话", created_at: "", updated_at: "2026-09-15T02:00:00Z" },
      { id: "s2", title: "上一条", created_at: "", updated_at: "2026-09-15T01:00:00Z" },
      { id: "s3", title: "上上条", created_at: "", updated_at: "2026-09-14T01:00:00Z" },
      { id: "s4", title: "更早", created_at: "", updated_at: "2026-09-13T01:00:00Z" },
      { id: "s5", title: "最早", created_at: "", updated_at: "2026-09-12T01:00:00Z" },
    ];
    const loadHistory = vi.spyOn(chat, "loadHistory").mockResolvedValue(undefined);

    const wrapper = mount(MessageList, { global: { stubs: { MessageItem: true } } });
    const rows = wrapper.findAll("[data-testid='welcome-recent'] button");
    expect(rows.length).toBe(3);
    expect(rows.map((r) => r.text()).join("|")).not.toContain("当前空会话");

    await rows[0].trigger("click");
    expect(sessions.currentId).toBe("s2");
    expect(loadHistory).toHaveBeenCalledWith("s2");

    wrapper.unmount();
  });

  it("没有别的会话时不出最近会话区块", async () => {
    const sessions = useSessionsStore();
    sessions.currentId = "only";
    sessions.list = [{ id: "only", title: "新会话", created_at: "", updated_at: "" }];

    const wrapper = mount(MessageList, { global: { stubs: { MessageItem: true } } });
    expect(wrapper.find("[data-testid='welcome-recent']").exists()).toBe(false);

    wrapper.unmount();
  });
});
