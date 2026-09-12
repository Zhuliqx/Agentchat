import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { defineComponent } from "vue";

const state = vi.hoisted(() => ({ statsImported: false }));

vi.mock("@/components/dialogs/StatsModal.vue", async () => {
  const { defineComponent } = await import("vue");
  state.statsImported = true;
  const component = defineComponent({
    name: "StatsModal",
    template: "<div />",
  });
  return {
    // Vue 仅对 ES module 取 default；否则 test-utils 会继续探测 mock 代理的未知键
    __esModule: true,
    default: component,
  };
});

import ChatView from "@/components/chat/ChatView.vue";

describe("ChatView lazy dialogs", () => {
  it("loads the stats dialog only when it is opened", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const HeaderStub = defineComponent({
      emits: ["stats"],
      template: '<button @click="$emit(\'stats\')">stats</button>',
    });
    expect(state.statsImported).toBe(false);

    const wrapper = mount(ChatView, {
      global: {
        plugins: [pinia],
        stubs: {
          ChatHeader: HeaderStub,
          MessageList: true,
          ChatInput: true,
          TasksModal: true,
          TimeTravelModal: true,
          TaskAgentModal: true,
        },
      },
    });
    expect(state.statsImported).toBe(false);

    await wrapper.find("button").trigger("click");
    await flushPromises();

    expect(state.statsImported).toBe(true);
    wrapper.unmount();
  });
});
