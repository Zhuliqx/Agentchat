import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("@/api", () => ({
  sessionsApi: {
    create: vi.fn(async () => ({
      id: "s-new",
      title: "新会话",
      created_at: "",
      updated_at: "",
    })),
  },
  searchApi: { search: vi.fn() },
  docsApi: { fileUrl: vi.fn(() => "/file") },
  memoryApi: { list: vi.fn(async () => []) },
  streamChat: vi.fn(),
}));

import { sessionsApi } from "@/api";
import Sidebar from "@/components/Sidebar.vue";
import { useChatStore } from "@/stores/chat";
import { useSessionsStore } from "@/stores/sessions";

function mountSidebar(pinia: ReturnType<typeof createPinia>) {
  return mount(Sidebar, {
    props: { healthText: "服务正常", healthOk: true, width: 236, contentWidth: 236, open: true },
    global: { plugins: [pinia], stubs: { teleport: true } },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("新建会话", () => {
  it("当前会话还是空的：连点不再堆空会话；有内容时才真正新建", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const sessions = useSessionsStore(pinia);
    const chat = useChatStore(pinia);
    sessions.list = [{ id: "s1", title: "新会话", created_at: "", updated_at: "" }];
    sessions.currentId = "s1";
    chat.messages = [];

    const wrapper = mountSidebar(pinia);
    const button = wrapper.findAll("button").find((b) => b.text().includes("新建会话"))!;

    await button.trigger("click");
    await flushPromises();
    expect(sessionsApi.create).not.toHaveBeenCalled();
    expect(sessions.currentId).toBe("s1");

    // 当前会话已有内容：新建才会真正落到服务端
    chat.messages = [{ id: "m1", role: "user", content: "问题" }];
    await button.trigger("click");
    await flushPromises();
    expect(sessionsApi.create).toHaveBeenCalledTimes(1);
    expect(sessions.currentId).toBe("s-new");
    wrapper.unmount();
  });
});
