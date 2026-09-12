import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick, reactive } from "vue";
import MessageItem from "@/components/chat/MessageItem.vue";
import type { ChatMsg } from "@/stores/chat";

describe("MessageItem streaming render", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the final content when streaming ends within the throttle window", async () => {
    const msg = reactive<ChatMsg>({
      id: "m1",
      role: "assistant",
      content: "",
      streaming: true,
    });
    const wrapper = mount(MessageItem, { props: { msg } });

    msg.content = "中间内容";
    await nextTick();
    msg.content = "最终答案";
    msg.streaming = false;
    await nextTick();
    vi.runAllTimers();
    await nextTick();

    expect(wrapper.text()).toContain("最终答案");
    wrapper.unmount();
  });
});
