import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";
import ChatInput from "@/components/chat/ChatInput.vue";
import { useChatOptionsStore } from "@/stores/chatOptions";
import { useChatStore } from "@/stores/chat";

vi.mock("@/api", () => ({
  modelsApi: {
    list: vi.fn(async () => ({ models: [], current: null })),
    setCurrent: vi.fn(),
  },
}));

describe("ChatInput 能力开关", () => {
  it("知识库/记忆/联网三个开关都在输入区，点击写回 store", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const options = useChatOptionsStore();
    options.useRag = true;
    options.useMemory = true;
    options.useSearch = true;

    const wrapper = mount(ChatInput, {
      global: { plugins: [pinia], stubs: { teleport: true } },
    });

    const switches = wrapper.findAll('[role="switch"]');
    expect(switches).toHaveLength(3);
    expect(wrapper.text()).toContain("知识库");
    expect(wrapper.text()).toContain("记忆");
    expect(wrapper.text()).toContain("联网");

    await switches[0].trigger("click");
    expect(options.useRag).toBe(false);
    await switches[1].trigger("click");
    expect(options.useMemory).toBe(false);
    await switches[2].trigger("click");
    expect(options.useSearch).toBe(false);
  });

  it("离开底部时显示回到最新，点击后复位滚动状态", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const chat = useChatStore();
    chat.messages = [{ id: "m1", role: "assistant", content: "答案" }];
    chat.markScroll(0);

    const wrapper = mount(ChatInput, {
      global: { plugins: [pinia], stubs: { teleport: true } },
    });
    expect(wrapper.find("button.rounded-full").exists()).toBe(false);

    // 滚远 + 来了新消息 → 按钮出现并显示条数
    chat.markScroll(400);
    chat.messages.push({ id: "m2", role: "user", content: "新问题" });
    await nextTick();
    const jump = wrapper.find("button.rounded-full");
    expect(jump.exists()).toBe(true);
    expect(jump.text()).toContain("1 条新消息");

    const before = chat.scrollNonce;
    await jump.trigger("click");
    expect(chat.scrollNonce).toBe(before + 1);
    expect(chat.atBottom).toBe(true);
  });
});
