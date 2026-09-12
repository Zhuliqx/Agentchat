import { beforeEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";
import MessageList from "@/components/chat/MessageList.vue";
import { useChatStore } from "@/stores/chat";

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
    setActivePinia(createPinia());
  });

  it("does not pull the user back down when they scrolled up", async () => {
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
    chat.messages[0].content = "第一段 + 新内容";
    await nextTick();
    await nextTick();

    expect(list.scrollTop).toBe(0);
    expect(wrapper.text()).toContain("回到最新");

    await wrapper.find("button").trigger("click");
    expect(list.scrollTop).toBe(1000);
    wrapper.unmount();
  });
});
