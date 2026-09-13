import { beforeEach, describe, expect, it } from "vitest";
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
    await nextTick();

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
});
