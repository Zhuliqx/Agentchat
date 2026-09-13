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

vi.mock("@/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api")>();
  return {
    ...actual,
    sessionsApi: {
      ...actual.sessionsApi,
      list: vi.fn(async () => []),
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
import ChatView from "@/components/chat/ChatView.vue";
import { useSessionsStore } from "@/stores/sessions";

describe("ChatView lazy dialogs", () => {
  it("loads the stats dialog only when it is opened", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const HeaderStub = defineComponent({
      emits: ["stats"],
      template: "<button @click=\"$emit('stats')\">stats</button>",
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

describe("ChatView 加载失败提示", () => {
  it("显示失败原因，点重试后重新拉取并自动消失", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useSessionsStore(pinia).error = "后端不可达";

    const wrapper = mount(ChatView, {
      global: {
        plugins: [pinia],
        stubs: {
          ChatHeader: true,
          MessageList: true,
          ChatInput: true,
          TasksModal: true,
          TaskAgentModal: true,
        },
      },
    });

    expect(wrapper.find('[data-testid="retry-load"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("后端不可达");

    await wrapper.get('[data-testid="retry-load"]').trigger("click");
    await flushPromises();

    expect(sessionsApi.list).toHaveBeenCalled();
    expect(sessionsApi.create).toHaveBeenCalled();
    expect(wrapper.find('[data-testid="retry-load"]').exists()).toBe(false);
    wrapper.unmount();
  });
});
